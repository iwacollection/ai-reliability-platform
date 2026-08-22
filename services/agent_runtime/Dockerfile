FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser \
    && pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services ./services

RUN uv sync --frozen --no-dev

USER appuser

EXPOSE 8000

CMD ["uvicorn", "services.agent_runtime.app.server:app", "--host", "0.0.0.0", "--port", "8000"]
