FROM node:24-bookworm-slim AS db-vendo-builder
ARG DB_VENDO_COMMIT=c228bffaa2796b814f1884237262c16d90f7df96
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init db-vendo-client \
    && cd db-vendo-client \
    && git remote add origin https://github.com/public-transport/db-vendo-client.git \
    && git fetch --depth 1 origin "${DB_VENDO_COMMIT}" \
    && git checkout --detach FETCH_HEAD \
    && npm ci --omit=dev --ignore-scripts \
    && npm install --omit=dev --ignore-scripts --no-save --package-lock=false qs@6.15.3 uuid@11.1.1 \
    && npm audit --omit=dev --audit-level=high

FROM node:24-bookworm-slim
ENV NODE_ENV=production
WORKDIR /app
COPY --from=db-vendo-builder /src/db-vendo-client ./vendor
COPY --from=db-vendo-builder /src/db-vendo-client/LICENSE.md /usr/share/licenses/db-vendo-client/LICENSE.md
COPY server.mjs ./server.mjs
USER node
EXPOSE 3001
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3001/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"]
CMD ["node", "/app/server.mjs"]
