FROM python:3.11-slim

WORKDIR /app

# Зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Исходники
COPY ./app/* ./

# Логи в stdout (Docker их подхватит)
ENV PYTHONUNBUFFERED=1

CMD ["python", "monitor.py"]