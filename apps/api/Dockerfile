FROM python:3.11-slim
WORKDIR /app

# ускоряем и делаем вывод логов без буферизации
#ENV PYTHONDONTWRITEBYTECODE=1
#ENV PYTHONUNBUFFERED=1

ENV CATALOG_BASE_URL=https://evengo.space

# зависимости
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# приложение
#COPY . .
COPY src/ ./src

# Cloud Run передаст переменную PORT, обычно 8080
ENV PORT=8080

# запуск через gunicorn, привязка ко всем интерфейсам и к $PORT
#CMD ["sh", "-c", "gunicorn -b 0.0.0.0:${PORT:-8080} app:app"]
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
