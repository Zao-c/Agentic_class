FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY data/public_sample ./data/public_sample
COPY data/structured ./data/structured
RUN mkdir -p /app/runtime /app/reports /app/data/eval

# A public image contains only synthetic/de-identified sample knowledge.
# docker-compose.yml overrides this with the operator's read-only local corpus.
ENV KNOWLEDGE_ROOT=/app/data/public_sample

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
