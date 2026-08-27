# Multi-stage Dockerfile for the React SPA frontend.
#
# Stage 1: build the production bundle with Vite.
# Stage 2: serve the bundle with nginx on port 8080 (Cloud Run default).
#
# Build context = repo root (so COPY apps/frontend/src works).

# ---- Stage 1: build ----
FROM node:20-alpine AS build
WORKDIR /workspace

# Build-time API base. Defaults to the deployed dashboard BFF; override with
#   docker build --build-arg VITE_API_BASE=https://your-bff/
# or via cloudbuild.yaml substitution. Empty means same-origin.
ARG VITE_API_BASE=https://dashboard-hnvjxkvfoq-as.a.run.app
ENV VITE_API_BASE=${VITE_API_BASE}

# Install deps first for cache reuse.
COPY apps/frontend/package.json apps/frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund

# Bring in the source.
COPY apps/frontend/ ./
RUN npm run build

# ---- Stage 2: serve ----
FROM nginx:1.27-alpine
WORKDIR /usr/share/nginx/html

# Replace the default nginx config with one that listens on 8080 (Cloud Run).
COPY apps/frontend/nginx.conf /etc/nginx/conf.d/default.conf

# Copy the built bundle.
COPY --from=build /workspace/dist/ ./

# Bundle the demo fixtures so the React app's "Quick presets" buttons can
# fetch them. The dashboard image bundles the same fixtures under /fixtures/
# for its proof-of-action harness.
COPY fixtures/hk-multi-allergen/ /usr/share/nginx/html/fixtures/hk-multi-allergen/
COPY fixtures/hk-multi-allergen-released/ /usr/share/nginx/html/fixtures/hk-multi-allergen-released/
COPY fixtures/hk-raw-tuna/ /usr/share/nginx/html/fixtures/hk-raw-tuna/
COPY fixtures/hk-tree-nuts-mix/ /usr/share/nginx/html/fixtures/hk-tree-nuts-mix/
COPY fixtures/hk-empty-label/ /usr/share/nginx/html/fixtures/hk-empty-label/

# Cloud Run runs as a non-root user; nginx already handles that. Make assets
# readable.
RUN chmod -R a+r /usr/share/nginx/html/

EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
