# rev19
import os, json
from flask import Flask, request, jsonify
from openai import OpenAI
from urllib.parse import urlencode
from typing import Any, Iterable

OFFTOPIC_REPLY = "вопрос не относится задачам сервиса и ответить на него не могу"

# рядом с константами
MODEL_INTENT = MODEL_FILTERS = MODEL_FORMAT  = "gpt-4o-mini"

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

# ----- Добавь эту функцию (выше /chat): ------
import re

VENUE_KEYWORDS = [
    "гость", "гостей", "мест", "гостей", "банкета", "сцена", "парковк",
    "евент", "мероприят", "свадьб", "день рождения", "юбиле", "аренда",
    "зал", "площадк", "банкет", "торжеств", "кейтеринг", "сабай", "хазар",
    "xezer", "khazar", "yasamal", "binagadi", "nizami", "sabail", "sabayil",
    "бакин", "бак"
]

def looks_like_venue_request(text: str, filters: dict) -> bool:
    """Правило: если извлечены смысловые фильтры ИЛИ текст содержит «сигналы» запросов по площадке — считаем venue_search."""
    t = text.lower()
    # сильные фильтры
    if (filters.get("guest_count") or filters.get("district") or
        filters.get("price_per_guest_max") or filters.get("cuisine") or
        (filters.get("features") and len(filters["features"]) > 0)):
        return True

    # числа как «80 гостей», «до 50 azn»
    if re.search(r"\b\d+\s*(гост|мест)", t):         # 80 гостей / 120 мест
        return True
    if re.search(r"\b\d+\s*azn\b", t):               # 50 azn
        return True

    # ключевые слова
    for kw in VENUE_KEYWORDS:
        if kw in t:
            return True

    return False

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
        print("OpenAI: no key (classify_intent)")
        return {"intent": "off_topic", "confidence": 1.0}

    try:
        r = client.responses.create(
            model=MODEL_INTENT,
            input=[
                {"role": "system", "content": [{
                    "type": "input_text",
                    "text": (
                        "Ты классификатор намерений сервиса подбора площадок для мероприятий.\n"
                        "Возврати ТОЛЬКО JSON по схеме. Если в запросе есть признаки подбора площадки "
                        "(район/кол-во гостей/бюджет/кухня/особенности/дата), класс = venue_search.\n"
                        "Если спрашивают о правилах, оплате или логистике — соответствующие классы.\n"
                        "Любые вопросы, не связанные с мероприятиями, = off_topic."
                    )
                }]},
                # пара коротких примеров закрепляют паттерн
                {"role": "user", "content": [{"type": "input_text", "text": "Сабаиль, 100 гостей, до 50 AZN, сцена и парковка"}]},
                {"role": "assistant", "content": [{"type": "input_text", "text": json.dumps({"intent":"venue_search","confidence":0.95})}]},
                {"role": "user", "content": [{"type": "input_text", "text": "сколько длина экватора?"}]},
                {"role": "assistant", "content": [{"type": "input_text", "text": json.dumps({"intent":"off_topic","confidence":0.99})}]},
                # реальный вход
                {"role": "user", "content": [{"type": "input_text", "text": user_text}]}
                '''
                {"role": "system", "content": [{
                    "type": "input_text",
                    "text": (
                        "Классифицируй, относится ли сообщение к теме организации мероприятий. "
                        "Верни ТОЛЬКО JSON по схеме (json_schema). Всё вне тематики — off_topic."
                    )
                }]},
                {"role": "user", "content": [{"type": "input_text", "text": user_text}]}
                '''
            ],
            response_format={"type": "json_schema", "json_schema": INTENT_SCHEMA},
            timeout=20.0
        )
        raw = r.output_text
        try:
            return json.loads(raw)
        except Exception as je:
            print("JSON decode error (classify_intent):", repr(je), "RAW:", raw)
            return {"intent": "off_topic", "confidence": 1.0}
    except Exception as e:
        print("OpenAI error (classify_intent):", repr(e))
        return {"intent": "off_topic", "confidence": 1.0}

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
        print("OpenAI: no key (extract_filters)")
        return {"guest_count": 1}

    try:
        r = client.responses.create(
            model=MODEL_FILTERS,
            input=[
                {"role": "system", "content": [{
                    "type": "input_text",
                    "text": "Извлеки фильтры площадки из текста пользователя. Верни ТОЛЬКО JSON по схеме."
                }]},
                {"role": "user", "content": [{"type": "input_text", "text": user_text}]}
            ],
            response_format={"type": "json_schema", "json_schema": FILTER_SCHEMA},
            timeout=20.0
        )
        raw = r.output_text
        try:
            return json.loads(raw)
        except Exception as je:
            print("JSON decode error (extract_filters):", repr(je), "RAW:", raw)
            return {"guest_count": 1}
    except Exception as e:
        print("OpenAI error (extract_filters):", repr(e))
        return {"guest_count": 1}

# ---------- helpers: unwrap Firestore REST doc + district normalization ----------
def _unwrap_firestore_rest(doc: dict) -> dict:
    """Принимает либо нормальный словарь SDK, либо REST-wire JSON с 'fields'. Возвращает плоский dict."""
    if not doc:
        return {}
    if "fields" not in doc or not isinstance(doc["fields"], dict):
        return doc  # уже распакованный SDK-словарь
    def _val(node):
        if "stringValue" in node:   return node["stringValue"]
        if "integerValue" in node:  return int(node["integerValue"])
        if "doubleValue" in node:   return float(node["doubleValue"])
        if "booleanValue" in node:  return bool(node["booleanValue"])
        if "timestampValue" in node:return node["timestampValue"]
        if "arrayValue" in node:
            arr = node["arrayValue"].get("values", []) or []
            return [_val(v) for v in arr]
        if "mapValue" in node:
            f = node["mapValue"].get("fields", {}) or {}
            return {k: _val(v) for k, v in f.items()}
        return node
    return {k: _val(v) for k, v in doc["fields"].items()}

def _norm_district(txt: str | None) -> str | None:
    if not txt:
        return None
    t = txt.strip().lower()
    mapping = {
        # Sabail
        "сабаиль": "Sabail", "сабайыл": "Sabail", "sabail": "Sabail", "sabayil": "Sabail",
        # Khazar
        "хазар": "Khazar", "xezer": "Khazar", "xəzər": "Khazar", "khazar": "Khazar",
        # Nizami (на всякий)
        "низами": "Nizami", "nizami": "Nizami",
        # Yasamal
        "ясамал": "Yasamal", "yasamal": "Yasamal",
        # Binagadi
        "бинагади": "Binagadi", "binagadi": "Binagadi", "binəqədi": "Binagadi",
    }
    return mapping.get(t, None)

# ---------- Поиск в Firestore под твою схему документов ----------
def search_venues_firestore(f: dict) -> dict:
    # входные фильтры
    guests = int(f.get("guest_count") or 0)
    price_max = f.get("price_per_guest_max")
    cuisine = f.get("cuisine")
    req_feats = set(f.get("features") or [])

    district = _norm_district(f.get("district"))
    q = db.collection("venues")

    # вместимость
    if guests:
        q = q.where("capacity_max", ">=", guests).where("capacity_min", "<=", guests)

    # район (только если смогли распознать)
    if district:
        q = q.where("district", "==", district)

    # бюджет/гость: ограничим нижнюю границу по минимальной цене
    if isinstance(price_max, (int, float)) and price_max > 0:
        q = q.where("price_per_person_azn_from", "<=", float(price_max))

    # кухня
    if cuisine:
        # в твоей схеме cuisine — массив строк => array_contains норм
        q = q.where("cuisine", "array_contains", cuisine)

    # Выполняем запрос (ограничим, чтобы не упереться в лимиты)
    docs = list(q.limit(50).stream())

    items = []
    for d in docs:
        raw = d.to_dict() or {}
        v = _unwrap_firestore_rest(raw)  # работает и для SDK-словаря, и для REST-wire

        # features := facilities ∪ services ∪ tags (все массивы строк)
        feat_union = set(v.get("facilities") or []) | set(v.get("services") or []) | set(v.get("tags") or [])
        # доп.фильтрация по фичам (второй array-фильтр Firestore не умеет)
        if req_feats and not req_feats.issubset(feat_union):
            continue

        photos = ((v.get("media") or {}).get("photos") or [])
        price_from = v.get("price_per_person_azn_from")
        price_to   = v.get("price_per_person_azn_to")
        cap_min, cap_max = v.get("capacity_min"), v.get("capacity_max")

        items.append({
            "id": v.get("id") or d.id,
            "name": v.get("name"),
            "district": v.get("district"),
            "capacity": [cap_min, cap_max],
            "price_per_guest": [price_from, price_to],
            "features": sorted(list(feat_union))[:8],
            "cuisine": v.get("cuisine") or [],
            "cover": photos[0] if photos else None,
            "base_rental_fee_azn": v.get("base_rental_fee_azn"),
        })

    # Сортировка: ближе к guest_count, затем по цене/гостю (from)
    def score(it):
        lo, hi = (it.get("capacity") or [0, 10**9])
        try:
            center = ((lo or 0) + (hi or 0)) / 2
        except Exception:
            center = 0
        dist = abs(center - (guests or center))
        pf = (it.get("price_per_guest") or [None, None])[0]
        pf = pf if isinstance(pf, (int, float)) else 10**9
        return (dist, pf)

    items.sort(key=score)
    return {"items": items[:7]}

def make_link(filters: dict) -> str:
    base = "https://evengo.space/search"
    qs = urlencode({k: filters[k] for k in
                    ["city","district","date","guest_count","price_per_guest_max","cuisine"]
                    if filters.get(k) is not None})
    if filters.get("features"):
        qs += ("&" if qs else "") + "features=" + ",".join(filters["features"])
    return f"{base}?{qs}" if qs else base

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
                {"role": "system", "content": [{"type": "input_text", "text": system_scope}]},
                {"role": "user", "content": [
                    {"type": "input_text", "text": user_text},
                    {"type": "input_text", "text": f"[locale={locale or 'auto'}]"}
                ]},
                {"role": "assistant", "content": [{"type": "input_text", "text": json.dumps(result, ensure_ascii=False)}]},
                {"role": "assistant", "content": [{"type": "input_text", "text": json.dumps({"link": link}, ensure_ascii=False)}]}
            ],
            timeout=20.0
        )
        return r.output_text or _fallback_format(result, link)  # <-- ключевая замена
    except Exception as e:
        print("OpenAI error (format_shortlist):", repr(e))
        return _fallback_format(result, link)

def _fallback_format(result: dict, link: str) -> str:
    lines = []
    for i, it in enumerate(result.get("items", [])[:7], 1):
        cap = it.get("capacity") or [None, None]
        price = it.get("price_per_guest") or [None, None]
        lines.append(f"{i}) {it.get('name')} — {it.get('district')} — {cap[0]}–{cap[1]} мест — ~{price[0]}–{price[1]} AZN/гость")
    lines.append(f"Смотреть все: {link}")
    return "\n".join(lines)

    
@app.get("/selftest")
def selftest():
    out = {"has_key": bool(os.getenv("OPENAI_API_KEY")), "openai": None, "firestore": None}
    # OpenAI ping
    try:
        c = get_client()
        if c is None:
            out["openai"] = "missing_key"
        else:
            # лёгкий вызов без токенов — список моделей
            list(c.models.list())  # SDK 1.x поддерживает .models.list()
            out["openai"] = "ok"
    except Exception as e:
        out["openai"] = f"error: {repr(e)}"
    # Firestore ping
    try:
        db.collection("venues").limit(1).stream()
        out["firestore"] = "ok"
    except Exception as e:
        out["firestore"] = f"error: {repr(e)}"
    return jsonify(out), 200

@app.get("/chat")
def chat_info():
    return (
        "<h3>Evengo API</h3>"
        "<p>Use <code>POST /chat</code> with JSON: "
        '<code>{"text": "ваш запрос"}</code>.</p>',
        200,
        {"Content-Type": "text/html; charset=utf-8"},
    )

@app.post("/chat")
def chat():
    try:
        payload = request.get_json(silent=True) or {}
        user_text = (payload.get("text") or "").strip()
        locale = payload.get("locale")

        if not user_text:
            return jsonify({"reply": OFFTOPIC_REPLY}), 200

        # Проверка ключа (не падаем на импорте)
        if get_client() is None:
            return jsonify({"reply": "OPENAI_API_KEY is missing"}), 200

        # 0) Сначала пробуем выжать фильтры / Извлечение фильтров → поиск → линк → форматирование
        filters = extract_filters(user_text)

        # 0.1) Если это очень похоже на запрос площадки — НЕ спрашиваем LLM-классификатор,
        # идём сразу в поиск
        if looks_like_venue_request(user_text, filters):
            data = search_venues_firestore(filters)
            link = make_link(filters)
            reply = format_shortlist(user_text, data, link, locale)
            return jsonify({
                "intent": "venue_search",
                "confidence": 0.9,     # мы уверены правилами
                "filters_used": filters,
                "shortlist": data.get("items", []),
                "link": link,
                "reply": reply
            }), 200
        
        # 1) Иначе — просим LLM классифицировать
        intent = classify_intent(user_text)
        conf = float(intent.get("confidence", 0))

        if intent.get("intent") == "venue_search" and conf >= 0.5:
            data = search_venues_firestore(filters)
            link = make_link(filters)
            reply = format_shortlist(user_text, data, link, locale)
            return jsonify({
                "intent": "venue_search",
                "confidence": conf,
                "filters_used": filters,
                "shortlist": data.get("items", []),
                "link": link,
                "reply": reply
            }), 200
            
        '''    
        # 1) Классификация / оффтоп
        intent = classify_intent(user_text)
        conf = float(intent.get("confidence", 0))
        
        if intent.get("intent") == "off_topic" or conf < 0.6:
            return jsonify({
                "intent": "off_topic",
                "confidence": conf,
                "reply": OFFTOPIC_REPLY,
                "shortlist": [],
                "link": None,
                "filters_used": None
            }), 200
            
        ## 2) Извлечение фильтров → поиск → линк → форматирование
        #filters = extract_filters(user_text)

        # если search_venues_firestore не готов — временно используем мок:
        try:
            data = search_venues_firestore(filters)   # реализуй эту функцию
        except NameError:
            data = search_mock(filters)

        link = make_link(filters)

        # если твоя format_shortlist принимает 3 аргумента — используй так:
        #reply = format_shortlist(user_text, data, link)
        # если уже сделал поддержку locale, раскомментируй следующую строку и закомментируй строку выше:
        reply = format_shortlist(user_text, data, link, locale)

        return jsonify({
            "intent": intent.get("intent", "venue_search"),
            "confidence": conf,
            "filters_used": filters,
            "shortlist": data.get("items", []),
            "link": link,
            "reply": reply
        }), 200
        '''
        
        # 2) Всё остальное считаем оффтопом
        return jsonify({
            "intent": "off_topic",
            "confidence": conf,
            "reply": OFFTOPIC_REPLY,
            "shortlist": [],
            "link": None,
            "filters_used": None
        }), 200

    except Exception as e:
        print("Handler error:", repr(e))
        # Никогда не отдаём 500 наружу
        return jsonify({"reply": "временная ошибка сервиса, попробуйте позже"}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
