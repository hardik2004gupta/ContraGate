FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8002

# Healthcheck: /health returns 200 only when all three agents loaded
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -sf http://localhost:8002/health || exit 1

CMD ["python", "-m", "uvicorn", "agents.main:app", "--host", "0.0.0.0", "--port", "8002"]
