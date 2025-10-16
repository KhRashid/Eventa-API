# Eventa Combined (Catalogue + API)

Дата сборки: 2025-10-16 06:45:31

## Состав
- `Eventa-Catalogue/` – динамический каталог (Firebase Hosting). Читает параметры из URL: `?q=&d=&cap=&price=`.
- `Eventa-API/` – Flask API с обновлённой функцией `make_link()`, которая возвращает ссылки на каталог в поле `link`.

## Переменные окружения
В API добавлена переменная `CATALOGUE_BASE_URL` (опционально). По умолчанию: `https://evengo.space`.
Пример:
```bash
export CATALOGUE_BASE_URL="https://evengo.space"
export OPENAI_API_KEY="..."
```

## Локальный запуск API
```bash
cd Eventa-API
pip install -r requirements.txt
export CATALOGUE_BASE_URL="https://evengo.space"
flask --app app.py run -p 8080
# или: python app.py
```

## Быстрый тест
```bash
curl -s -X POST "http://localhost:8080/chat"   -H "Content-Type: application/json"   -d '{"text":"Ищу зал в Nasimi на 80 гостей до 50 азн"}' | jq .
```

В ответе проверьте `link`, например:
```
https://evengo.space/?d=Nasimi&cap=80&price=50
```

## Деплой
- Каталог: `firebase deploy` из папки `Eventa-Catalogue`.
- API: задеплойте как раньше (Cloud Run/Functions), переменную `CATALOGUE_BASE_URL` можно задать в настройках сервиса.

## Примечания
- Если в будущем добавите deep-link на конкретный venue (`?id=...`), можно расширить `make_link()`.
- Путь и домен можно менять без правок кода через `CATALOGUE_BASE_URL`.
