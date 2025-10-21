# Evengo API — PLAYBOOK

Этот документ — единый источник правды по проекту **Evengo API**: назначение, API, внутренняя логика (LLM + Firestore), структура данных, конфигурация, деплой/обновление, тестирование и устранение неполадок.

---

## 1) Назначение и архитектура

**Задача:** принять запрос на естественном языке (RU/AZ/EN), определить намерение пользователя, извлечь фильтры (район, гости, бюджет, кухня, особенности), найти площадки в Firestore и вернуть шорт‑лист + ссылку на поиск.

**Компоненты:**
- **Flask API** (Python 3.11) — контейнер в **Cloud Run**.
- **OpenAI SDK 1.x** (`gpt-4o-mini`) — классификация, извлечение фильтров, форматирование текста-ответа.
- **Fallback‑парсер** (регулярки) — дешёвый/надежный бэкап для извлечения фильтров.
- **Firestore** (`venues`) — хранилище карточек площадок.
- **Firebase Hosting** — домен `evengo.space`, правило rewrite/redirect на Cloud Run.

---

## 2) Эндпоинты

### 2.1. `GET /`
Проверка живости контейнера. **Ответ:** `200 OK` с текстом `ok`.

### 2.2. `GET /chat`
HTML‑подсказка как вызывать `POST /chat`.

### 2.3. `GET /selftest`
Самотест окружения.

**Пример ответа:**
```json
{
  "has_key": true,
  "openai": "ok",
  "firestore": "ok"
}
```

### 2.4. `POST /chat`
Главный метод.

**Тело (JSON):**
```json
{
  "text": "Хазар, 80 гостей, до 50 AZN, озеро и детская зона",
  "locale": "ru"
}
```

**Алгоритм:**
1. Извлечение фильтров: fallback‑регулярки + (опционально) LLM → мердж.
2. Если фраза очевидно про поиск площадки (правила), сразу идём в поиск (без LLM‑классификатора). Иначе — LLM‑классификация.
3. Поиск по Firestore (1 range‑фильтр server‑side) + client‑side фильтры.
4. Сортировка и формирование ответа (`shortlist`, `reply`, `link`).

**Пример ответа:**
```json
{
  "confidence": 0.9,
  "filters_used": {
    "guest_count": 80,
    "price_per_guest_max": 50.0
  },
  "intent": "venue_search",
  "link": "https://evengo.space/search?guest_count=80&price_per_guest_max=50.0",
  "reply": "1) Old City Caravanserai — Icherisheher — 15–150 мест — ~30–58 AZN/гость\n2) Cottage Lake Gala — Khazar — 15–120 мест — ~22–48 AZN/гость\n...\nСмотреть все: https://evengo.space/search?guest_count=80&price_per_guest_max=50.0",
  "shortlist": [
    {
      "id": "old_city_caravanserai",
      "name": "Old City Caravanserai",
      "district": "Icherisheher",
      "capacity": [15, 150],
      "price_per_guest": [30, 58],
      "features": ["Arches", "Courtyard", "Decor", "..."],
      "cuisine": ["Azeri", "Oriental"],
      "cover": "https://.../old_city_caravanserai01.jpg",
      "base_rental_fee_azn": 1600
    }
  ]
}
```

**Поля:**
- `intent` — класс намерения: `venue_search`, `off_topic`, `vendor_question`, `booking_payment`, `pricing_menu`, `policy_rules`, `logistics`, `about_service`, `smalltalk_in_scope`.
- `confidence` — уверенность (0..1).
- `filters_used` — итоговые фильтры.
- `shortlist` — до 7 карточек площадок (структура настраивается).
- `link` — ссылка с теми же фильтрами (для UI).
- `reply` — короткая текстовая сводка (LLM/фолбэк).

---

## 3) Извлекаемые фильтры

`extract_filters()` возвращает:

| Поле                  | Тип             | Описание |
|-----------------------|-----------------|----------|
| `guest_count`         | integer (≥1)    | Кол-во гостей |
| `district`            | string          | Район, нормализованный (напр., `Хазар` → `Khazar`) |
| `price_per_guest_max` | number          | Бюджет «до … AZN/гость» |
| `cuisine`             | string          | Кухня (`Azeri`, `BBQ`, `European`, …) |
| `features`            | string[]        | Особенности: `Lakeside`, `Kids zone`, … |
| `date`                | YYYY-MM-DD      | (опц.) |
| `city`                | string          | (опц.) |

**Мердж‑логика:** LLM > fallback. Пустые/`None`/`[]` поля удаляются.

---

## 4) Firestore: модель и поиск

Коллекция **`venues`**. Документ в распакованном виде содержит:
- `id`, `name`, `district`
- `capacity_min`, `capacity_max`
- `price_per_person_azn_from`, `price_per_person_azn_to`
- `base_rental_fee_azn`, `address`, `location_lat`, `location_lng`
- `cuisine` (array), `services` (array), `facilities` (array), `tags` (array)
- `media.photos` (array), `media.videos` (array)
- `policies`, `menu`, `suitable_for` …

**Почему только один range‑фильтр в запросе?** Firestore без композитных индексов поддерживает 1 range‑условие. В проекте — это `capacity_min <= guest_count`. Остальное фильтруется **на клиенте**:
- `capacity_max >= guest_count`
- `price_per_person_azn_from <= price_per_guest_max`
- соответствие `features` (объединение `facilities ∪ services ∪ tags`)

**Сортировка:** по близости вместимости (среднее `(min+max)/2`) → по минимальной цене/гость.

---

## 5) Состав репозитория

- `app.py` — сервер и бизнес‑логика
- `requirements.txt` — зависимости
- `Dockerfile` — сборка контейнера
- `firebase.json` — правила Hosting (rewrite на Cloud Run)
- (опц.) `public/` — статика для Hosting

### 5.1. requirements.txt (пример)
```
flask
openai==1.51.0
firebase-admin==6.6.0
gunicorn==21.2.0
httpx==0.27.2
```

### 5.2. Dockerfile (пример)
```dockerfile
FROM python:3.11-slim
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
CMD ["sh", "-c", "gunicorn -b 0.0.0.0:$PORT app:app"]
```

### 5.3. firebase.json (пример)
```json
{
  "hosting": {
    "site": "evengo",
    "public": "public",
    "ignore": ["firebase.json", "**/.*", "**/node_modules/**"],
    "rewrites": [
      {
        "source": "/chat",
        "run": { "serviceId": "evengo-api", "region": "europe-west1" }
      }
    ]
  }
}
```

---

## 6) Как изменить формат ответа

### 6.1. Верхний уровень (`/chat`)
Меняйте итоговый словарь перед `jsonify(...)` в `chat()`:
```python
payload = {
  "intent": intent,
  "confidence": conf,
  "filters_used": filters,
  "shortlist": data.get("items", []),
  "link": link,
  "reply": reply,
  # Доп. поля:
  "version": 1,
  "total": len(data.get("items", []))
}
return jsonify(payload), 200
```

### 6.2. Карточка площадки
Редактируйте блок `items.append({...})` в `search_venues_firestore()`:
```python
items.append({
  "id": v.get("id") or d.id,
  "name": v.get("name"),
  "district": v.get("district"),
  "capacity": [v.get("capacity_min"), v.get("capacity_max")],
  "price_per_guest": [v.get("price_per_person_azn_from"), v.get("price_per_person_azn_to")],
  "features": sorted(list(feat_union))[:8],
  "cuisine": v.get("cuisine") or [],
  "cover": photos[0] if photos else None,
  "base_rental_fee_azn": v.get("base_rental_fee_azn"),
  # Примеры доп. полей:
  # "address": v.get("address"),
  # "location": {"lat": v.get("location_lat"), "lng": v.get("location_lng")},
})
```

### 6.3. Текстовый `reply`
Формируется в `format_shortlist()` через LLM, иначе — `_fallback_format()`.  
Чтобы **выключить** текст — не добавляйте `reply` в итоговый JSON.

---

## 7) Деплой и обновление

### 7.1. Сборка контейнера
```bash
gcloud builds submit --tag gcr.io/ai-event-bot/evengo-api:revXX
```

### 7.2. Деплой в Cloud Run
```bash
gcloud run deploy evengo-api \
  --image gcr.io/ai-event-bot/evengo-api:revXX \
  --region europe-west1 \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars OPENAI_API_KEY="sk-..." 
```

Проверка:
```bash
curl -s https://<SERVICE>.europe-west1.run.app/selftest
```

### 7.3. Привязка домена через Firebase Hosting
```bash
npm i -g firebase-tools
firebase login --no-localhost
firebase use ai-event-bot
firebase deploy --only hosting
```
Проверьте `https://evengo.space/chat`.

---

## 8) Тестирование

### 8.1. cURL
```bash
curl -i -X POST https://www.evengo.space/chat \
  -H "Content-Type: application/json" \
  -d '{"text":"Хазар, 80 гостей, до 50 AZN за гостя, озеро и детская зона"}'
```

### 8.2. Postman
- Method: `POST`
- URL: `https://www.evengo.space/chat`
- Body: `raw` → JSON (UTF‑8)

---

## 9) Логи и диагностика

### 9.1. Логи Cloud Run
```bash
gcloud run services logs read evengo-api --region europe-west1 --limit=200
```

### 9.2. Частые причины пустого `shortlist`
- Слишком общий запрос → мало/нет фильтров.
- В БД нет документов, подходящих под `capacity_max`/цену/`features`.
- Район не распознан/не нормализован (расширьте `_norm_district`).
- Неверно настроили range‑фильтры на серверной стороне.

### 9.3. Быстрые проверки
```bash
# env
gcloud run services describe evengo-api --region europe-west1 \
  --format='value(spec.template.spec.containers[0].env)'
# selftest
curl -s https://<SERVICE>.europe-west1.run.app/selftest
# health
curl -s https://<SERVICE>.europe-west1.run.app/
```

---

## 10) Производительность и стоимость

- Правила (`looks_like_venue_request`) часто позволяют **избежать** LLM‑классификатора → экономия токенов.
- Fallback‑регулярки дают ответ при недоступности LLM.
- При росте БД рассматривайте:
  - композитные индексы для дополнительных server‑side фильтров;
  - кеширование популярных запросов.

---

## 11) FAQ / Рецепты

**Добавить поле `address` в карточку?** — Допишите его в `items.append({...})` (см. §6.2).  
**Убрать `reply`?** — Не включайте поле в итоговый JSON.  
**Добавить `version`?** — Добавьте ключ в `payload` перед `jsonify`.  
**Расширить словарь районов?** — Дописывайте соответствия в `_norm_district`.  
**Поменять сортировку?** — Измените функцию `score()` в `search_venues_firestore`.

---

## 12) Контроль версий (обновление сервиса)

1. Изменили `app.py` / `requirements.txt` / `firebase.json` → commit.
2. `gcloud builds submit --tag gcr.io/ai-event-bot/evengo-api:revYY`
3. `gcloud run deploy evengo-api ... --image gcr.io/...:revYY`
4. `firebase deploy --only hosting` (если менялись правила).
5. Проверьте `GET /selftest` и пару кейсов `POST /chat`.

---

_Контакты/заметки команды можно хранить в конце файла или в `README.md`._
