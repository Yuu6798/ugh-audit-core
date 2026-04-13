FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir ".[server]"

ENV UGH_AUDIT_DB=/data/audit.db
ENV UGH_AUDIT_CACHE_DIR=/data
ENV UGH_META_CACHE_DIR=/data/meta_cache

EXPOSE 8000
CMD ["uvicorn", "ugh_audit.server:app", "--host", "0.0.0.0", "--port", "8000"]
