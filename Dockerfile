FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY . .
RUN uv sync --frozen --no-dev
RUN .venv/bin/python -m scripts.prepare_ocr

ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "main.py"]
