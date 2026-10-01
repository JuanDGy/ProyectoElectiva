FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY . .
RUN uv sync --frozen --no-dev
RUN .venv/bin/python -m scripts.prepare_ocr

ENV PATH="/app/.venv/bin:$PATH"
ENV MLFLOW_DISABLE_AGENT_HINT=1 GIT_PYTHON_REFRESH=quiet

CMD ["python", "main.py"]
