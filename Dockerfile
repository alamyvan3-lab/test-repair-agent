# Web app + CLI image (also used for Nebius Serverless Jobs in the benchmark).
# Serverless containers have no Docker daemon, so this image defaults to the local
# subprocess sandbox; see docs/ARCHITECTURE.md for the isolation trade-off.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SANDBOX_BACKEND=local \
    ALLOW_UPLOADS=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY web ./web
COPY examples ./examples
COPY bench/repos ./bench/repos
COPY bench/manifest.json ./bench/manifest.json

RUN useradd --uid 10001 --create-home app && mkdir -p /app/runs && chown app /app/runs
USER app

EXPOSE 8000
CMD ["uvicorn", "test_repair.server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
