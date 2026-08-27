# Multi-stage Dockerfile for adk-runtime (Label Hold ADK Control Service).
# Build context = repo root, so COPY paths are namespaced under apps/adk-runtime/.
FROM python:3.12-slim AS builder
WORKDIR /workspace
COPY apps/adk-runtime/pyproject.toml ./
# Plain pip install (no --target) so entry-point scripts land in /usr/local/bin
RUN pip install --no-cache-dir .

FROM python:3.12-slim
WORKDIR /app
ENV PORT=8080
ENV PYTHONPATH=/app
# Copy the python packages and the entry-point scripts
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
# Copy the application code: main.py, agents/, label_hold/
COPY apps/adk-runtime/main.py ./
COPY apps/adk-runtime/agents/ ./agents/
COPY apps/adk-runtime/label_hold/ ./label_hold/
EXPOSE 8080
CMD ["python", "main.py"]
