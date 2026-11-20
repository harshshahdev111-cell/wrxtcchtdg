FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Kolkata \
    PYTHONUNBUFFERED=1 \
    TIKTOKEN_CACHE_DIR=/app/tokenizer-cache \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production

WORKDIR /app
COPY requirements/base.txt requirements/production.txt /app/requirements/
RUN python -m pip install -r /app/requirements/production.txt
RUN python -c "import tiktoken; tiktoken.get_encoding('o200k_harmony'); tiktoken.get_encoding('cl100k_base')" \
    && chmod -R a+rX /app/tokenizer-cache

RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && mkdir -p /app/backend/staticfiles \
    && chown app:app /app/backend/staticfiles
COPY --chown=app:app backend /app/backend
COPY --chown=app:app pyproject.toml /app/pyproject.toml

USER app
WORKDIR /app/backend
RUN python manage.py collectstatic --noinput
EXPOSE 8000
CMD ["gunicorn", "--config", "config/gunicorn.py", "config.asgi:application"]
