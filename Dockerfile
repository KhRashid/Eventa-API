FROM python:3.11-slim
WORKDIR /app

# ускоряем и делаем вывод логов без буферизации
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# зависимости
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# приложение
COPY . .

# Cloud Run передаст переменную PORT, обычно 8080
ENV PORT=8080

# запуск через gunicorn, привязка ко всем интерфейсам и к $PORT
# CMD ["python", "app.py"]
CMD ["gunicorn", "-b", ":$PORT", "app:app"]
