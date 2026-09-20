# Go läuft immer auf der Build-Maschine und baut für die Zielarchitektur: keine Emulation, kein 15-Minuten-arm64-Build.
FROM --platform=$BUILDPLATFORM golang:1.26-bookworm AS trvl-builder
ARG TARGETOS=linux
ARG TARGETARCH
ARG TRVL_REF=v1.21.4
# WITH_TRVL=0 baut ein Image ohne trvl (und ohne dessen Noncommercial-Lizenz); Traviorel läuft dann mit den Direktwegen.
ARG WITH_TRVL=1
RUN case "$TRVL_REF" in v[0-9]*.[0-9]*.[0-9]*) ;; *) echo "TRVL_REF must be a version tag such as v1.21.4" >&2; exit 2 ;; esac
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN if [ "$WITH_TRVL" = "1" ]; then git init trvl \
    && cd trvl \
    && git remote add origin https://github.com/MikkoParkkola/trvl.git \
    && git fetch --depth 1 origin "refs/tags/${TRVL_REF}:refs/tags/${TRVL_REF}" \
    && git checkout --detach "refs/tags/${TRVL_REF}" \
    && git rev-parse HEAD > /tmp/TRVL_COMMIT; \
    else mkdir -p trvl/internal/models && : > trvl/internal/models/airports.go \
    && echo "trvl is not part of this image" > trvl/LICENSE && echo none > /tmp/TRVL_COMMIT; fi
WORKDIR /src/trvl
RUN if [ "$WITH_TRVL" = "1" ]; then TRVL_VERSION="${TRVL_REF#v}" && GOOS="${TARGETOS}" GOARCH="${TARGETARCH:-$(go env GOARCH)}" CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.Version=${TRVL_VERSION}" -o /out/trvl ./cmd/trvl \
    && if [ "${TARGETARCH:-$(go env GOARCH)}" = "$(go env GOARCH)" ]; then /out/trvl version | grep -F "trvl ${TRVL_VERSION}" >/dev/null && /out/trvl hotels --help | grep -F -- "--enrich-rooms" >/dev/null && /out/trvl flights --help | grep -F -- "--provider" >/dev/null && /out/trvl ground --help | grep -F -- "--provider" >/dev/null; fi \
    && cp /tmp/TRVL_COMMIT /out/TRVL_COMMIT && echo "${TRVL_REF}" > /out/TRVL_REF; \
    else mkdir -p /out && printf '#!/bin/sh\nexit 127\n' > /out/trvl && chmod +x /out/trvl \
    && cp /tmp/TRVL_COMMIT /out/TRVL_COMMIT && echo none > /out/TRVL_REF; fi

FROM python:3.13-slim-bookworm
ARG WITH_TRVL=1
ENV TRVL_ENABLED=${WITH_TRVL} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/var/lib/reisevergleich
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl dumb-init \
    && useradd --system --uid 10001 --home /var/lib/reisevergleich --create-home reisevergleich \
    && rm -rf /var/lib/apt/lists/*
COPY --from=trvl-builder /out/trvl /usr/local/bin/trvl
COPY --from=trvl-builder /out/TRVL_COMMIT /usr/share/reisevergleich/TRVL_COMMIT
COPY --from=trvl-builder /out/TRVL_REF /usr/share/reisevergleich/TRVL_REF
COPY --from=trvl-builder /src/trvl/internal/models/airports.go /usr/share/reisevergleich/trvl_airports.go
COPY --from=trvl-builder /src/trvl/LICENSE /usr/share/licenses/trvl/LICENSE
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=reisevergleich:reisevergleich app.py ./app.py
COPY --chown=reisevergleich:reisevergleich db_cffi_bridge.py ./db_cffi_bridge.py
COPY init_db_cffi_token.py ./init_db_cffi_token.py
COPY --chown=reisevergleich:reisevergleich reisevergleich ./reisevergleich
COPY --chown=reisevergleich:reisevergleich ui ./ui
USER reisevergleich
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:8000/api/health >/dev/null || exit 1
ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
