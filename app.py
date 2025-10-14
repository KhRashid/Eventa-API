import os, json
from flask import Flask, request, jsonify
from openai import OpenAI

OFFTOPIC_REPLY = "вопрос не относится задачам сервиса и ответить на него не могу"

# рядом с константами
MODEL_INTENT = "gpt-4o-mini"
MODEL_FILTERS = "gpt-4o-mini"
MODEL_FORMAT  = "gpt-4o-mini"

def safe_openai(call):
    """
    call: функция без аргументов, внутри которой client.responses.create(...)
    Возвращает dict/text либо поднимает исключение в ответ с заглушкой.
    """
    try:
        return call()
    except Exception as e:
        # лог в stdout, чтобы видеть в Cloud Run
        print("OpenAI error:", repr(e))
        return None

app = Flask(__name__)

def get_client():
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    return OpenAI(api_key=key)

@app.get("/")
def health():
    return "ok", 200

# ---------- Firestore (ADC в Cloud Run) ----------
import firebase_admin
from firebase_admin import firestore
if not firebase_admin._apps:
    firebase_admin.initialize_app()   # в Cloud Run возьмет default credentials
db = firestore.client()

# ---------- Классификатор намерений ----------
INTENT_SCHEMA = {
  "name": "EventIntent",
  "schema": {
    "type": "object",
    "properties": {
      "intent": {"type": "string", "enum": [
        "venue_search","vendor_question","booking_payment",
        "pricing_menu","policy_rules","logistics",
        "about_service","smalltalk_in_scope","off_topic"
      ]},
      "confidence": {"type":"number","minimum":0,"maximum":1}
    },
    "required": ["intent","confidence"],
    "additionalProperties": False
  },
  "strict": True
}

def classify_intent(user_text: str) -> dict:
    client = get_client()
    if client is None:
        # безопасная заглушка: считаем оффтопом, чтобы сервис не падал
        return {"intent": "off_topic", "confidence": 1.0}

    def _call():
        r = client.responses.create(
            model=MODEL_INTENT,
            input=[
                {"role":"system","content":[{"type":"input_text","text":
                  "Классифицируй, относится ли сообщение к теме организации мероприятий. "
                  "Верни ТОЛЬКО JSON по схеме. Всё вне тематики — off_topic."
                }]},
                {"role":"user","content":[{"type":"input_text","text": user_text}]}
            ],
            response_format={"type":"json_schema","json_schema": INTENT_SCHEMA}
        )
        return json.loads(r.output[0].content[0].text)

    out = safe_openai(_call)
    return out or {"intent":"off_topic","confidence":1.0}

# ---------- Извлечение фильтров ----------
FILTER_SCHEMA = {
  "name": "VenueFilters",
  "schema": {
    "type":"object",
    "properties":{
      "city":{"type":"string"},
      "district":{"type":"string"},
      "date":{"type":"string","description":"YYYY-MM-DD"},
      "guest_count":{"type":"integer","minimum":1},
      "price_per_guest_max":{"type":"number"},
      "cuisine":{"type":"string"},
      "features":{"type":"array","items":{"type":"string"}}
    },
    "required":["guest_count"],
    "additionalProperties": False
  },
  "strict": True
}

def extract_filters(user_text: str) -> dict:
    client = get_client()
    if client is None:
        # минимальные дефолты, чтобы код не ломался
        return {"guest_count": 1}
        
    def _call():
        r = client.responses.create(
          model=MODEL_FILTERS,
          input=[
            {"role":"system","content":[{"type":"input_text","text":
              "Извлеки фильтры площадки из текста пользователя. Верни ТОЛЬКО JSON по схеме."
            }]},
            {"role":"user","content":[{"type":"input_text","text": user_text}]}
          ],
          response_format={"type":"json_schema","json_schema": FILTER_SCHEMA}
        )
        return json.loads(r.output[0].content[0].text)

    out = safe_openai(_call)
    return out or {"guest_count": 1}

# ---------- Поиск в Firestore под твою схему документов ----------
from urllib.parse import urlencode

def search_venues_firestore(f: dict) -> dict:
    district = f.get("district")
    guests   = int(f["guest_count"])
    price_max = f.get("price_per_guest_max")
    cuisine  = f.get("cuisine")            # string
    req_feats = set(f.get("features") or [])

    q = db.collection("venues").where("is_active","==",True)
    if district:
        q = q.where("district","==",district)
    q = q.where("capacity_min","<=",guests).where("capacity_max",">=",guests)
    if price_max is not None:
        q = q.where("price_per_person_azn_from","<=",float(price_max))
    if cuisine:
        q = q.where("cuisine","array_contains",cuisine)

    docs = list(q.limit(50).stream())
    items = []
    for d in docs:
        v = d.to_dict()

        # объединяем фичи: facilities ∪ services ∪ tags
        feat_union = set(v.get("facilities",[]) or []) \
                   | set(v.get("services",[]) or []) \
                   | set(v.get("tags",[]) or [])

        if req_feats and not req_feats.issubset(feat_union):
            continue

        photos = (v.get("media") or {}).get("photos") or []
        price_from = v.get("price_per_person_azn_from")
        price_to   = v.get("price_per_person_azn_to")

        items.append({
            "id": v.get("id") or d.id,
            "name": v.get("name"),
            "district": v.get("district"),
            "capacity": [v.get("capacity_min"), v.get("capacity_max")],
            "price_per_guest": [price_from, price_to],
            "features": sorted(list(feat_union))[:8],
            "cuisine": v.get("cuisine") or [],
            "cover": photos[0] if photos else None,
            "base_rental_fee_azn": v.get("base_rental_fee_azn")
        })

    # сортировка: цена-от ↑, затем max capacity ↓
    def sort_key(x):
        pf = x["price_per_guest"][0]
        return (pf if isinstance(pf,(int,float)) else 1e9, -(x["capacity"][1] or 0))
    items.sort(key=sort_key)

    return {"items": items[:7]}

def make_link(filters: dict) -> str:
    base = "https://evengo.space/search"
    qs = urlencode({k: filters[k] for k in
                    ["city","district","date","guest_count","price_per_guest_max","cuisine"]
                    if filters.get(k) is not None})
    if filters.get("features"):
        qs += ("&" if qs else "") + "features=" + ",".join(filters["features"])
    return f"{base}?{qs}" if qs else base

def format_shortlist(user_text: str, result: dict, link: str, locale: str|None) -> str:
    client = get_client()
    if client is None:
        # простая текстовая выдача без LLM
        lines = []
        for i, it in enumerate(result.get("items", [])[:7], 1):
            cap = it.get("capacity") or [None, None]
            price = it.get("price_per_guest") or [None, None]
            lines.append(f"{i}) {it.get('name')} — {it.get('district')} — {cap[0]}–{cap[1]} мест — ~{price[0]}–{price[1]} AZN/гость")
        lines.append(f"Смотреть все: {link}")
        return "\n".join(lines)

    system_scope = (
        "Ты — ассистент Evengo для подбора площадок и услуг для мероприятий в Азербайджане. "
        "Отвечай ТОЛЬКО по теме. Если запрос вне тематики — отвечай фиксированной фразой. "
        "Собери шорт-лист до 7 карточек: Название — район — диапазон мест — ~цена/гость — краткая фича. "
        "В конце выведи одну ссылку link."
    )
    def _call():
        r = client.responses.create(
          model=MODEL_FORMAT,
          input=[
            {"role":"system","content":[{"type":"input_text","text": system_scope}]},
            {"role":"user","content":[{"type":"input_text","text": user_text},
                                      {"type":"input_text","text": f"[locale={locale or 'auto'}]"}]},
            {"role":"assistant","content":[{"type":"input_text","text": json.dumps(result, ensure_ascii=False)}]},
            {"role":"assistant","content":[{"type":"input_text","text": json.dumps({"link":link}, ensure_ascii=False)}]}
          ]
        )
        return "".join(c["content"][0]["text"] for c in r.output if c["type"]=="output_text")
    
    out = safe_openai(_call)
    if out:
        return out
    # резерв без LLM
    lines = []
    for i, it in enumerate(result.get("items", [])[:7], 1):
        cap = it.get("capacity") or [None, None]
        price = it.get("price_per_guest") or [None, None]
        lines.append(f"{i}) {it.get('name')} — {it.get('district')} — {cap[0]}–{cap[1]} мест — ~{price[0]}–{price[1]} AZN/гость")
    lines.append(f"Смотреть все: {link}")
    return "\n".join(lines)

@app.post("/chat")
def chat():
    try:
        body = request.get_json(silent=True) or {}
        
        if get_client() is None:
        return jsonify({"error": "OPENAI_API_KEY is missing"}), 500
        
        payload = request.get_json(force=True, silent=True) or {}
        user_text = (payload.get("text") or "").strip()
        locale = payload.get("locale")
    
        if not user_text:
            return jsonify({"reply": OFFTOPIC_REPLY}), 200
    
        # 1) оффтоп
        intent = classify_intent(user_text)
        if intent.get("intent") == "off_topic" or float(intent.get("confidence",0)) < 0.6:
            return jsonify({
                "intent": "off_topic",
                "confidence": float(intent.get("confidence",0)),
                "reply": OFFTOPIC_REPLY,
                "shortlist": [],
                "link": None,
                "filters_used": None
            }), 200
    
        # 2) фильтры -> поиск -> ссылка -> форматирование
        filters = extract_filters(user_text)
        data = search_venues_firestore(filters)
        link = make_link(filters)
        reply = format_shortlist(user_text, data, link, locale)
        
        '''
        return jsonify({
            "intent": "venue_search",
            "confidence": float(intent.get("confidence",0)),
            "filters_used": filters,
            "shortlist": data.get("items", []),
            "link": link,
            "reply": reply
        }), 200
        '''
        
        return jsonify({
          "intent": intent["intent"], "confidence": intent["confidence"],
          "filters_used": filters, "shortlist": data["items"],
          "link": link, "reply": reply
        }), 200
    except Exception as e:
        print("Handler error:", repr(e))
        return jsonify({"reply": "временная ошибка сервиса, попробуйте позже"}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
