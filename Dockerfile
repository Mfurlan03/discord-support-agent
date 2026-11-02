FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/hf_cache SENTENCE_TRANSFORMERS_HOME=/app/hf_cache \
    DB_PATH=/data/agent.db TRACES_DIR=/data/traces

COPY requirements.txt .
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
# Bake the embedding model into the image so the bot starts without downloading it
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY . .
RUN useradd --create-home app && mkdir -p /data && chown -R app /app /data
USER app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

# Discord bot + HTTP API / trace viewer on :8000, one process
CMD ["python", "-m", "bot"]
