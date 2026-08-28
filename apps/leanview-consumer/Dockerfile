# Multi-stage Dockerfile for leanview-consumer (Python stub).
# Wires the Pub/Sub push handler + Firestore writer.
# Build context = repo root, so COPY paths are namespaced under apps/leanview-consumer/.
FROM python:3.12-slim AS builder
WORKDIR /workspace
COPY apps/leanview-consumer/pyproject.toml ./
# Plain pip install (no --target) so entry-point scripts land in /usr/local/bin
RUN pip install --no-cache-dir .

FROM python:3.12-slim
WORKDIR /app
ENV PORT=8080
# Copy the python packages and the entry-point scripts
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY apps/leanview-consumer/main.py ./
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
