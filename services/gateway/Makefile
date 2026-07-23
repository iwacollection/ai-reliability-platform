install:
	uv sync

run:
	uv run uvicorn services.gateway.app.main:app --reload

lint:
	uv run ruff check .

format:
	uv run ruff check . --fix

test:
	uv run pytest