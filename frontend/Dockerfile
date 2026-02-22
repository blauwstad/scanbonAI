# =============================================================================
# ScanbonAI – Frontend Dockerfile (React / Vite -> Nginx)
# =============================================================================
# IMPORTANT: The build context for this Dockerfile is the repo ROOT (.), not
# the frontend/ subdirectory.  The React source lives in src/, package.json,
# index.html, vite.config.ts, etc. at the root.
#
# In docker-compose.yml the frontend service is configured as:
#   build:
#     context: .
#     dockerfile: frontend/Dockerfile
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: builder – install Node deps and compile the Vite production bundle
# ---------------------------------------------------------------------------
FROM node:22-alpine AS builder

WORKDIR /build

# Copy dependency manifests first to exploit layer caching.
# npm ci requires both package.json and package-lock.json.
COPY package.json package-lock.json ./

# Install exact versions from lockfile; skip optional dev tooling
RUN npm ci --prefer-offline

# Copy the rest of the frontend source (src/, public/, index.html, configs…)
COPY index.html ./
COPY tsconfig.json tsconfig.app.json tsconfig.node.json ./
COPY vite.config.ts eslint.config.js ./
COPY src/ ./src/
COPY public/ ./public/

# Build the production bundle into /build/dist
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: runtime – serve the static bundle with Nginx
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# Remove the default Nginx config shipped in the image
RUN rm /etc/nginx/conf.d/default.conf

# Copy the SPA-aware Nginx config
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

# Copy the compiled Vite bundle from the builder stage
COPY --from=builder /build/dist /usr/share/nginx/html

# Expose port 3000 (matches the compose port mapping 127.0.0.1:3000:3000)
EXPOSE 3000

# Nginx runs as the default non-privileged user provided by the alpine image.
# The image already drops to the `nginx` user for worker processes.
CMD ["nginx", "-g", "daemon off;"]
