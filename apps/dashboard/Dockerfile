# Multi-stage Dockerfile for the dashboard (FastAPI app).
# The app serves both the SPA shell
# (at /) and the JSON proxy/read endpoints (/api/run, /api/lots, /api/lots/{id}).
#
# Build context = repo root so COPY paths resolve under apps/dashboard/.
FROM python:3.12-slim AS builder
WORKDIR /workspace
COPY apps/dashboard/pyproject.toml ./
RUN pip install --no-cache-dir .

FROM python:3.12-slim
WORKDIR /app
ENV PORT=8080
# Copy the python packages and entry-point scripts
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
# Copy app source + static assets
COPY apps/dashboard/main.py ./
COPY apps/dashboard/public/ ./public/
# Copy the demo fixtures so the proof-of-action harness can fetch them.
# Only the multi-allergen scenarios are bundled (the demo doesn't need all).
COPY fixtures/hk-multi-allergen/ ./fixtures/hk-multi-allergen/
COPY fixtures/hk-multi-allergen-released/ ./fixtures/hk-multi-allergen-released/
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
