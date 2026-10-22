FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libffi8 fonts-liberation && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python scripts/compile_messages.py && DEBUG=False SECRET_KEY=build python manage.py collectstatic --noinput

EXPOSE 8000

RUN chmod +x scripts/start.sh

CMD ["sh", "scripts/start.sh"]
