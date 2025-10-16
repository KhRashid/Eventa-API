# Eventa-API — shortlist deep-link интеграция с Eventa-Catalogue

## Что нового
- При успешном `venue_search` API формирует документ в Firestore в коллекции `shortlists`:
  - поля: `venue_ids` (список id площадок), `token_hash` (SHA-256 от выданного `token`), `created_at`, `expires_at` (+14 дней), `note` (опционально)
- В ответе поле `link` теперь указывает на динамическую страницу каталога:  
  `https://<CATALOGUE_BASE_URL>/?shortlist=<slug>&token=<token>`

## Переменные окружения
- `CATALOGUE_BASE_URL` — базовый URL каталога (по умолчанию `https://evengo.space`)
- `OPENAI_API_KEY` — ключ OpenAI (как и ранее)
- Для Firestore в Cloud Run используются ADC. Локально — `GOOGLE_APPLICATION_CREDENTIALS`

## Smoke-тест локально
```bash
export CATALOGUE_BASE_URL="https://evengo.space"
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/adc.json
flask run -p 8080

curl -s -X POST "http://localhost:8080/chat"   -H "Content-Type: application/json"   -d '{"text":"Нужен зал в Nasimi на 80 гостей до 50 AZN"}' | jq .
```
Проверьте, что `link` имеет формат `/?shortlist=...&token=...` и открывает каталог с нужной подборкой.

## Ветвь
Рекомендуется коммитить изменения в `APIwithCatalogue`.
