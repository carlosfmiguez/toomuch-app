#!/bin/sh
# Inicialização do container em produção (Railway, Fly, VPS):
# migra o banco, garante os dados iniciais e o usuário admin, e sobe o servidor.
# Tudo idempotente: pode rodar a cada deploy.
set -e

echo "[start] migrações"
python manage.py migrate --noinput

echo "[start] dados iniciais (parâmetros, impressoras, canais, cores)"
python manage.py seed_base

echo "[start] usuário admin (se DJANGO_SUPERUSER_* estiver definido)"
python manage.py ensure_superuser

echo "[start] gunicorn na porta ${PORT:-8000}"
exec gunicorn config.wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --threads 2 \
  --timeout 180 \
  --access-logfile - \
  --error-logfile -
