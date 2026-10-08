# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd -r codeforge && \
    useradd -r -g codeforge -m -d /home/codeforge codeforge

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

COPY main.py ./
COPY src/ src/

RUN mkdir -p /app/logs /app/workspace /app/data && \
    chown -R codeforge:codeforge /app

USER codeforge

ENTRYPOINT ["python", "main.py"]
