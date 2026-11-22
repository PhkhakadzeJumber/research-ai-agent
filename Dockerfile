# The Eureka API (FastAPI) as a container: `docker compose up --build` (compose.yaml), and later Cloud Run (step 12).
#
# Secrets are NOT in the image: .env is excluded (.dockerignore); compose passes its values in at run time
# (in the cloud: the host's secret store). On start, the database is migrated to the latest version, then the API
# serves on port 8000.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# the exact pinned versions first: this layer is rebuilt only when requirements.txt changes
COPY requirements.txt ./
RUN pip install -r requirements.txt

# then the app itself (src/ as a package, the migrations config, the command-line scripts)
COPY pyproject.toml alembic.ini README.md ./
COPY src ./src
COPY scripts ./scripts
RUN pip install --no-deps . && useradd --create-home --uid 1000 eureka
USER eureka

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2)"

# migrate, then serve (0.0.0.0 inside the container; compose publishes it on this computer only)
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn src.api.main:app --host 0.0.0.0 --port 8000"]
