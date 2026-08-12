# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

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
