FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DESTINATIONS_DATA_PATH=/app/data/destinations.yaml \
    NA_MALDIVY_DATA_DIR=/app/data

WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev

COPY backend/app ./app
COPY data/destinations.yaml data/ATTRIBUTION.md ./data/
COPY data/sources/airports-MIT-LICENSE.txt ./data/sources/airports-MIT-LICENSE.txt
COPY data/routes ./data/routes
COPY data/trains ./data/trains
COPY data/hotels ./data/hotels
COPY data/hotel_reviews ./data/hotel_reviews
COPY data/hotel_profiles ./data/hotel_profiles
COPY data/cache ./data/cache

RUN useradd --create-home --uid 1000 agent \
    && chown -R agent:agent /app/data/cache
USER agent

EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=10 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)"]
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
