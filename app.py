# rev26
# app.py
import os, json, re
from urllib.parse import urlencode
from flask import Flask, request, jsonify
from openai import OpenAI

OFFTOPIC_REPLY = "вопрос не относится задачам сервиса и ответить на него не могу"
MODEL_INTENT = MODEL_FILTERS = MODEL_FORMAT = "gpt-4o-mini"

app = Flask(__name__)

# ---------- OpenAI ----------
def get_client():
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    return OpenAI(api_key=key)

def safe_openai(call):
    try:
        return call()
    except Exception as e:
        print("OpenAI error:", repr(e))
        return None

@app.get("/")
def health():
    return "ok", 200

# ---------- Firestore (ADC в Cloud Run) ----------
import firebase_admin
from firebase_admin import firestore
if not firebase_admin._apps:
    firebase_admin.initialize_app()  # в Cloud Run возьмет default credentials
db = firestore.client()

# ---------- эвристики / словари ----------
VENUE_KEYWORDS = [
    "гость","гостей","мест","банкета","сцена","парковк",
    "евент","мероприят","свадьб","день рождения","юбиле","аренда",
    "зал","площадк","банкет","торжеств","кейтеринг","сабай","хазар",
    "xezer","khazar","yasamal","binagadi","nizami","sabail","sabayil",
    "бакин","бак","azn","манат","manat","локац"
]
FEATURE_KWS = {
    "озеро":"Lakeside","у озера":"Lakeside","озер":"Lakeside",
    "детская зона":"Kids zone","детская":"Kids zone","дети":"Kids zone",
    "парковк":"Parking","сцена":"Stage",
}
CUISINE_KWS = {"азер":"Azeri","bbq":"BBQ","барбекю":"BBQ","европ":"European"}

# ---------- утилиты парсинга ----------
def parse_fallback_filters(text: str) -> dict:
    t = (text or "").lower()
    out = {}

    m = re.search(r'(\d{1,4})\s*(гост|гостей|гостя|мест)', t)
    if m:
        out["guest_count"] = int(m.group(1))

    m = re.search(r'до\s*(\d{1,5})\s*(azn|манат|manat)', t) or re.search(r'(\d{1,5})\s*(azn|манат|manat)', t)
    if m:
        out["price_per_guest_max"] = float(m.group(1))

    for cand in ["хазар","xezer","xəzər","khazar","сабаиль","sabail","sabayil",
                 "ясамал","yasamal","низами","nizami","бинагади","binagadi","binəqədi"]:
        if cand in t:
            norm = _norm_district(cand)
            if norm:
                out["district"] = norm
                break

    for kw, val in CUISINE_KWS.items():
        if kw in t:
            out["cuisine"] = val
            break

    feats = {feat for kw, feat in FEATURE_KWS.items() if kw in t}
    if feats:
        out["features"] = sorted(list(feats))

    return out

def looks_like_venue_request(text: str, filters: dict) -> bool:
    t = text.lower()
    if (filters.get("guest_count") or filters.get("district") or
        filters.get("price_per_guest_max") or filters.get("cuisine") or
        (filters.get("features") and len(filters["features"]) > 0)):
        return True
    if re.search(r"\b\d+\s*(гост|мест)\b", t):  # 80 гостей / 120 мест
        return True
    if re.search(r"\b\d+\s*azn\b", t):
        return True
    return any(kw in t for kw in VENUE_KEYWORDS)

# ---------- нормализация районов ----------
def _norm_district(txt: str | None) -> str | None:
    if not txt:
        return None
    t = txt.strip().lower()
    mapping = {
        "сабаиль":"Sabail","сабайыл":"Sabail","sabail":"Sabail","sabayil":"Sabail",
        "хазар":"Khazar","xezer":"Khazar","xəzər":"Khazar","khazar":"Khazar",
        "низами":"Nizami","nizami":"Nizami",
        "ясамал":"Yasamal","yasamal":"Yasamal",
        "бинагади":"Binagadi","binagadi":"Binagadi","binəqədi":"Binagadi",
    }
    return mapping.get(t)

# ---------- схемы ----------
INTENT_SCHEMA = {
  "name":"EventIntent",
  "schema":{"type":"object","properties":{
      "intent":{"type":"string","enum":[
          "venue_search","vendor_question","booking_payment",
          "pricing_menu","policy_rules","logistics",
          "about_service","smalltalk_in_scope","off_topic"
      ]},
      "confidence":{"type":"number","minimum":0,"maximum":1}
  },"required":["intent","confidence"],"additionalProperties":False},
  "strict":True
}
FILTER_SCHEMA = {
  "name":"VenueFilters",
  "schema":{"type":"object","properties":{
      "city":{"type":"string"},
      "district":{"type":"string"},
      "date":{"type":"string","description":"YYYY-MM-DD"},
      "guest_count":{"type":"integer","minimum":1},
      "price_per_guest_max":{"type":"number"},
      "cuisine":{"type":"string"},
      "features":{"type":"array","items":{"type":"string"}}
  },"required":["guest_count"],"additionalProperties":False},
  "strict":True
}

# ---------- LLM функции ----------
def classify_intent(user_text: str) -> dict:
    client = get_client()
    if client is None:
        print("OpenAI: no key (classify_intent)")
        return {"intent":"off_topic","confidence":1.0}

    try:
        r = client.responses.create(
            model=MODEL_INTENT,
            temperature=0,
            input=[
                {"role":"system","content":[{"type":"input_text","text":
                    "Ты классификатор намерений сервиса подбора площадок для мероприятий. "
                    "Верни ТОЛЬКО JSON по схеме. Если в запросе есть признаки подбора площадки "
                    "(район/кол-во гостей/бюджет/кухня/особенности/дата) — class=venue_search. "
                    "Невтемные вопросы — off_topic."
                }]},
                {"role":"user","content":[{"type":"input_text","text":"Сабаиль, 100 гостей, до 50 AZN, сцена и парковка"}]},
                {"role":"assistant","content":[{"type":"input_text","text":json.dumps({"intent":"venue_search","confidence":0.95})}]},
                {"role":"user","content":[{"type":"input_text","text":"сколько длина экватора?"}]},
                {"role":"assistant","content":[{"type":"input_text","text":json.dumps({"intent":"off_topic","confidence":0.99})}]},
                {"role":"user","content":[{"type":"input_text","text": user_text}]}
            ],
            response_format={"type":"json_schema","json_schema":INTENT_SCHEMA},
            timeout=20.0
        )
        return json.loads(r.output_text)
    except Exception as e:
        print("OpenAI error (classify_intent):", repr(e))
        return {"intent":"off_topic","confidence":1.0}

def extract_filters(user_text: str) -> dict:
    fb = parse_fallback_filters(user_text)

    client = get_client()
    if client is None:
        return {"guest_count": fb.get("guest_count", 1),
                **{k:v for k,v in fb.items() if k != "guest_count"}}

    system_msg = (
        "Ты извлекаешь фильтры для подбора площадки событий. "
        "Верни ТОЛЬКО JSON по схеме. Если чего-то нет — не выдумывай."
        "\nПримеры:\n"
        "Вход: 'Хазар, 80 гостей, до 50 AZN, озеро и детская зона'\n"
        "Выход: {\"guest_count\":80,\"district\":\"Khazar\",\"price_per_guest_max\":50,"
        "\"features\":[\"Lakeside\",\"Kids zone\"]}\n"
        "Вход: 'Сабаиль, банкет на 120, BBQ'\n"
        "Выход: {\"guest_count\":120,\"district\":\"Sabail\",\"cuisine\":\"BBQ\"}"
    )
    try:
        r = client.responses.create(
            model=MODEL_FILTERS, temperature=0,
            input=[
                {"role":"system","content":[{"type":"input_text","text":system_msg}]},
                {"role":"user","content":[{"type":"input_text","text":user_text}]}
            ],
            response_format={"type":"json_schema","json_schema":FILTER_SCHEMA},
            max_output_tokens=300, timeout=20.0
        )
        llm = json.loads(r.output_text) if r.output_text else {}
    except Exception as e:
        print("OpenAI error (extract_filters):", repr(e))
        llm = {}

    out = {}
    #out["guest_count"] = int(llm.get("guest_count") or fb.get("guest_count") or 1)
    gc = llm.get("guest_count") or fb.get("guest_count")
    if gc is not None:
        out["guest_count"] = int(gc)
    
    if llm.get("price_per_guest_max") is not None:
        out["price_per_guest_max"] = float(llm["price_per_guest_max"])
    elif "price_per_guest_max" in fb:
        out["price_per_guest_max"] = float(fb["price_per_guest_max"])

    district = llm.get("district") or fb.get("district")
    out["district"] = _norm_district(district) if district else None

    out["cuisine"] = llm.get("cuisine") or fb.get("cuisine")
    feats = set(llm.get("features") or []) | set(fb.get("features") or [])
    if feats:
        out["features"] = sorted(list(feats))

    return {k:v for k,v in out.items() if v not in (None, [], "")}

# ---------- распаковка REST вида ----------
def _unwrap_firestore_rest(doc: dict) -> dict:
    if not doc: return {}
    if "fields" not in doc or not isinstance(doc["fields"], dict):
        return doc
    def _val(node):
        if "stringValue"  in node: return node["stringValue"]
        if "integerValue" in node: return int(node["integerValue"])
        if "doubleValue"  in node: return float(node["doubleValue"])
        if "booleanValue" in node: return bool(node["booleanValue"])
        if "timestampValue" in node: return node["timestampValue"]
        if "arrayValue" in node:
            arr = node["arrayValue"].get("values", []) or []
            return [_val(v) for v in arr]
        if "mapValue" in node:
            f = node["mapValue"].get("fields", {}) or {}
            return {k:_val(v) for k,v in f.items()}
        return node
    return {k:_val(v) for k,v in doc["fields"].items()}

# ---------- ПОИСК (без композитных индексов) ----------
def search_venues_firestore(f: dict) -> dict:
    guests = int(f.get("guest_count") or 0)
    price_max = f.get("price_per_guest_max")
    cuisine = (f.get("cuisine") or "").strip() or None
    req_feats = set(f.get("features") or [])
    district = _norm_district(f.get("district"))

    q = db.collection("venues")

    # единственный серверный неравенственный фильтр — чтобы НЕ требовались композитные индексы
    if guests:
        q = q.where("capacity_min", "<=", guests)
        q = q.where("capacity_max", ">=", guests)

    if district:
        q = q.where("district", "==", district)

    if cuisine:
        q = q.where("cuisine", "array_contains", cuisine)

    try:
        docs = list(q.limit(200).stream())
    except Exception as e:
        print("[search_venues_firestore] stream error:", repr(e))
        docs = list(db.collection("venues").limit(200).stream())

    items = []
    for d in docs:
        v = _unwrap_firestore_rest(d.to_dict() or {})

        # client-side фильтры (чтобы не требовались индексы)
        if district and v.get("district") != district:
            continue

        if cuisine and cuisine not in (v.get("cuisine") or []):
            continue

        cap_min, cap_max = v.get("capacity_min"), v.get("capacity_max")
        if guests and (cap_max is None or cap_max < guests):
            continue

        if isinstance(price_max, (int, float)) and v.get("price_per_person_azn_from") is not None:
            if float(v["price_per_person_azn_from"]) > float(price_max):
                continue

        feat_union = set(v.get("facilities") or []) | set(v.get("services") or []) | set(v.get("tags") or [])
        if req_feats and not req_feats.issubset(feat_union):
            continue

        photos = ((v.get("media") or {}).get("photos") or [])
        items.append({
            "id": v.get("id") or d.id,
            "name": v.get("name"),
            "district": v.get("district"),
            "capacity": [cap_min, cap_max],
            "price_per_guest": [v.get("price_per_person_azn_from"), v.get("price_per_person_azn_to")],
            "features": sorted(list(feat_union))[:8],
            "cuisine": v.get("cuisine") or [],
            "cover": photos[0] if photos else None,
            "base_rental_fee_azn": v.get("base_rental_fee_azn"),
        })

    # сортировка: ближе к целевой вместимости, затем по цене-from
    def score(it):
        lo, hi = (it.get("capacity") or [0, 10**9])
        center = ((lo or 0) + (hi or 0)) / 2
        dist = abs(center - (guests or center))
        pf = (it.get("price_per_guest") or [None, None])[0]
        pf = pf if isinstance(pf, (int, float)) else 10**9
        return (dist, pf)

    items.sort(key=score)
    return {"items": items[:7]}

# ---------- форматирование ответа ----------
def make_link(filters: dict) -> str:
    base = "https://evengo.space/search"
    qs = urlencode({k: filters[k] for k in
                    ["city","district","date","guest_count","price_per_guest_max","cuisine"]
                    if filters.get(k) is not None})
    if filters.get("features"):
        qs += ("&" if qs else "") + "features=" + ",".join(filters["features"])
    return f"{base}?{qs}" if qs else base

def _fallback_format(result: dict, link: str) -> str:
    lines = []
    for i, it in enumerate(result.get("items", [])[:7], 1):
        cap = it.get("capacity") or [None, None]
        price = it.get("price_per_guest") or [None, None]
        lines.append(f"{i}) {it.get('name')} — {it.get('district')} — {cap[0]}–{cap[1]} мест — ~{price[0]}–{price[1]} AZN/гость")
    lines.append(f"Смотреть все: {link}")
    return "\n".join(lines)

def format_shortlist(user_text: str, result: dict, link: str, locale: str | None = None) -> str:
    client = get_client()
    if client is None:
        return _fallback_format(result, link)

    system_scope = (
        "Ты — ассистент Evengo для подбора площадок и услуг для мероприятий в Азербайджане. "
        "Отвечай ТОЛЬКО по теме. Если запрос вне тематики — отвечай фиксированной фразой. "
        "Собери шорт-лист до 7 карточек: «Название — район — X–Y мест — ~цена/гость — краткая фича». "
        "В конце выведи одну ссылку link."
    )
    try:
        r = client.responses.create(
            model=MODEL_FORMAT,
            input=[
                {"role":"system","content":[{"type":"input_text","text":system_scope}]},
                {"role":"user","content":[
                    {"type":"input_text","text":user_text},
                    {"type":"input_text","text":f"[locale={locale or 'auto'}]"}
                ]},
                {"role":"assistant","content":[{"type":"input_text","text":json.dumps(result, ensure_ascii=False)}]},
                {"role":"assistant","content":[{"type":"input_text","text":json.dumps({"link":link}, ensure_ascii=False)}]}
            ],
            timeout=20.0
        )
        return r.output_text or _fallback_format(result, link)
    except Exception as e:
        print("OpenAI error (format_shortlist):", repr(e))
        return _fallback_format(result, link)

# ---------- selftest ----------
@app.get("/selftest")
def selftest():
    out = {"has_key": bool(os.getenv("OPENAI_API_KEY")), "openai": None, "firestore": None}
    try:
        c = get_client()
        if c is None:
            out["openai"] = "missing_key"
        else:
            list(c.models.list())
            out["openai"] = "ok"
    except Exception as e:
        out["openai"] = f"error: {repr(e)}"

    try:
        db.collection("venues").limit(1).stream()
        out["firestore"] = "ok"
    except Exception as e:
        out["firestore"] = f"error: {repr(e)}"
    return jsonify(out), 200

# ---------- info ----------
@app.get("/chat")
def chat_info():
    return (
        "<h3>Evengo API</h3>"
        "<p>Use <code>POST /chat</code> with JSON: "
        '<code>{"text": "ваш запрос"}</code>.</p>',
        200,
        {"Content-Type": "text/html; charset=utf-8"},
    )

# ---------- основной обработчик ----------
@app.post("/chat")
def chat():
    try:
        payload = request.get_json(silent=True) or {}
        user_text = (payload.get("text") or "").strip()
        locale = payload.get("locale")

        if not user_text:
            return jsonify({"reply": OFFTOPIC_REPLY}), 200

        if get_client() is None:
            return jsonify({"reply": "OPENAI_API_KEY is missing"}), 200

        # S1: извлекаем фильтры
        try:
            filters = extract_filters(user_text)
        except Exception as e:
            print("[chat][S1 extract] error:", repr(e))
            filters = {"guest_count": 1}

        # S2: быстрый путь — похоже на запрос площадки
        try:
            if looks_like_venue_request(user_text, filters):
                data = search_venues_firestore(filters)
                link = make_link(filters)
                reply = format_shortlist(user_text, data, link, locale)
                return jsonify({
                    "intent":"venue_search","confidence":0.9,
                    "filters_used":filters,"shortlist":data.get("items",[]),
                    "link":link,"reply":reply
                }), 200
        except Exception as e:
            print("[chat][S2 quick] error:", repr(e))

        # S3: классификация
        try:
            intent = classify_intent(user_text)
            conf = float(intent.get("confidence", 0))
        except Exception as e:
            print("[chat][S3 classify] error:", repr(e))
            intent, conf = {"intent":"off_topic"}, 0.0

        if intent.get("intent") == "venue_search" and conf >= 0.5:
            data = search_venues_firestore(filters)
            link = make_link(filters)
            reply = format_shortlist(user_text, data, link, locale)
            return jsonify({
                "intent":"venue_search","confidence":conf,
                "filters_used":filters,"shortlist":data.get("items",[]),
                "link":link,"reply":reply
            }), 200

        return jsonify({
            "intent":"off_topic","confidence":conf,
            "reply":OFFTOPIC_REPLY,"shortlist":[], "link":None, "filters_used":None
        }), 200

    except Exception as e:
        print("[chat][FATAL] error:", repr(e))
        return jsonify({"reply":"временная ошибка сервиса, попробуйте позже"}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

