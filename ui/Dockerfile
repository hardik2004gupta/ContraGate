# ContraGate UI Dockerfile — multi-stage Vite build + nginx serve
# Stage 1: build the React app
# Stage 2: serve with nginx

# ── Stage 1: Build ─────────────────────────────────────────────────────────────
FROM node:20-alpine AS build

WORKDIR /app

COPY ui/package.json ./
RUN npm install

COPY ui/ ./

# VITE_API_BASE_URL can be injected at build time for Railway deployment.
# When not set, falls back to the vite.config.js proxy (localhost:8000).
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build

# ── Stage 2: Serve ─────────────────────────────────────────────────────────────
FROM nginx:alpine

# Remove default nginx config and replace with ContraGate SPA config
RUN rm /etc/nginx/conf.d/default.conf

COPY ui/nginx.conf /etc/nginx/conf.d/default.conf

# Copy built assets from Stage 1
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD curl -sf http://localhost:80 || exit 1

CMD ["nginx", "-g", "daemon off;"]
