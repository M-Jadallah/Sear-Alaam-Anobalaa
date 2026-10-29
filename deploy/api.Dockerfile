FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 SIYAR_MODE=production SIYAR_MODEL_PROFILE=arabic-retrieval-v1
WORKDIR /app
COPY requirements-production.txt /app/requirements-production.txt
RUN pip install --no-cache-dir -r requirements-production.txt && pip check \
    && groupadd --gid 10001 siyar && useradd --uid 10001 --gid siyar --no-create-home siyar \
    && mkdir /state /backups && chown siyar:siyar /state /backups
COPY backend /app/backend
COPY db /app/db
COPY reference/topic_dictionary.csv /app/reference/topic_dictionary.csv
COPY data/source.mARkdown /app/data/source.mARkdown
COPY fixtures/semantic_evaluation.jsonl /app/fixtures/semantic_evaluation.jsonl
COPY deploy/seed /app/deploy/seed
COPY deploy/artifacts/models /opt/siyar/models
COPY deploy/artifacts/semantic /opt/siyar/semantic
COPY docs/MODEL_NOTICES.md /opt/siyar/MODEL_NOTICES.md
COPY docs/licenses /opt/siyar/licenses
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--limit-concurrency", "16", "--proxy-headers", "--forwarded-allow-ips", "*"]
