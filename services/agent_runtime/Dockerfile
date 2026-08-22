FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev

COPY packages ./packages
COPY services ./services
COPY configs ./configs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uv", "run", "uvicorn", "services.agent_runtime.app.server:app", "--host", "0.0.0.0", "--port", "8000"]
