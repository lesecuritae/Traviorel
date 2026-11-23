# Go läuft immer auf der Build-Maschine und baut für die Zielarchitektur: keine Emulation, kein 15-Minuten-arm64-Build.
FROM --platform=$BUILDPLATFORM golang:1.27.4-bookworm@sha256:4fa7c968ba93f1635670522dbb01a2c78150443e1385746924566079f36570ea AS trvl-builder
ARG TARGETOS=linux
ARG TARGETARCH
ARG TRVL_REF=v1.28.5
ARG TRVL_VERSION=1.28.5
# Tags are mutable; a tag or full commit must resolve to the reviewed commit.
ARG TRVL_COMMIT=79d8ad49a3bf9bee1ad75890bc15a89feba9ebca
# WITH_TRVL=0 baut ein Image ohne trvl (und ohne dessen Noncommercial-Lizenz); Traviorel läuft dann mit den Direktwegen.
ARG WITH_TRVL=1
RUN printf '%s' "$TRVL_REF" | grep -Eq '^(v[0-9]+\.[0-9]+\.[0-9]+|[0-9a-f]{40})$' \
    && if [ "$WITH_TRVL" = "1" ]; then printf '%s' "$TRVL_COMMIT" | grep -Eq '^[0-9a-f]{40}$' || { echo "TRVL_COMMIT must be a full 40-character commit hash" >&2; exit 2; }; fi
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN if [ "$WITH_TRVL" = "1" ]; then git init trvl \
    && cd trvl \
    && git remote add origin https://github.com/MikkoParkkola/trvl.git \
    && git fetch --depth 1 origin "$TRVL_REF" \
    && git checkout --detach FETCH_HEAD \
    && actual="$(git rev-parse HEAD)" \
    && if [ "$actual" != "$TRVL_COMMIT" ]; then echo "trvl ${TRVL_REF} resolved to ${actual}, expected ${TRVL_COMMIT}" >&2; exit 3; fi \
    && echo "$actual" > /tmp/TRVL_COMMIT; \
    else mkdir -p trvl/internal/models && : > trvl/internal/models/airports.go \
    && echo "trvl is not part of this image" > trvl/LICENSE && echo none > /tmp/TRVL_COMMIT; fi
WORKDIR /src/trvl
RUN if [ "$WITH_TRVL" = "1" ]; then GOTOOLCHAIN=local go mod download; fi
# Upstream regressions run without provider/network access or operator credentials.
# Fake-browser tests still resolve example.com through the real safety guard.
# This public-address fixture exists only in this network-disabled build step;
# neither the runtime image nor the host DNS configuration receives it.
# The hotel suite exercises retry backoffs and needs more than three minutes.
RUN --network=none --mount=type=bind,source=trvl-test-hosts,target=/etc/hosts \
    if [ "$WITH_TRVL" = "1" ]; then GOTOOLCHAIN=local GOMAXPROCS=2 GOMEMLIMIT=2GiB go test -p=1 -count=1 -timeout=360s ./internal/flights/afklm ./internal/flights ./internal/hotels ./internal/providers ./internal/ground ./internal/telemetry; fi
RUN --network=none if [ "$WITH_TRVL" = "1" ]; then export TRVL_NO_TELEMETRY=1 TRVL_DISABLE_UPDATE_CHECK=1; GOOS="${TARGETOS}" GOARCH="${TARGETARCH:-$(go env GOARCH)}" CGO_ENABLED=0 GOTOOLCHAIN=local go build -trimpath -ldflags "-s -w -X main.Version=${TRVL_VERSION}" -o /out/trvl ./cmd/trvl \
    && if [ "${TARGETARCH:-$(go env GOARCH)}" = "$(go env GOARCH)" ]; then /out/trvl version | grep -F "trvl ${TRVL_VERSION}" >/dev/null && /out/trvl hotels --help | grep -F -- "--enrich-rooms" >/dev/null && /out/trvl hotels --help | grep -F -- "--checkin" >/dev/null && /out/trvl flights --help | grep -F -- "--provider" >/dev/null && /out/trvl flights --help | grep -F -- "--return" >/dev/null && /out/trvl ground --help | grep -F -- "--provider" >/dev/null && /out/trvl airport-transfer --help | grep -F -- "--arrival-after" >/dev/null && /out/trvl route --help | grep -F -- "--arrive-by" >/dev/null; fi \
    && cp /tmp/TRVL_COMMIT /out/TRVL_COMMIT && echo "${TRVL_REF}" > /out/TRVL_REF; \
    else mkdir -p /out && printf '#!/bin/sh\nexit 127\n' > /out/trvl && chmod +x /out/trvl \
    && cp /tmp/TRVL_COMMIT /out/TRVL_COMMIT && echo none > /out/TRVL_REF; fi

FROM python:3.13-slim-bookworm
ARG WITH_TRVL=1
ENV TRVL_NO_TELEMETRY=1 \
    TRVL_DISABLE_UPDATE_CHECK=1 \
    TRVL_ENABLED=${WITH_TRVL} \
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
