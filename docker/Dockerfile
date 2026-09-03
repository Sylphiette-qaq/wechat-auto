FROM --platform=linux/amd64 golang:1.25.5-bookworm AS go-builder

WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' -o /out/wechat-cli ./cmd/wechat-cli

FROM --platform=linux/amd64 ubuntu:22.04

ARG WECHAT_VERSION=4.1.1.8
ARG WECHAT_SHA256=c9765e87ee5133bf4bb50d585c1814fafd995e3fb0da62c5ed07259b43dada7b

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 DISPLAY=:99 HOME=/data/wechat XDG_CONFIG_HOME=/data/config DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates dbus dbus-x11 at-spi2-core gir1.2-atspi-2.0 python3 python3-gi python3-dbus desktop-file-utils \
    x11vnc xvfb x11-utils novnc websockify xauth xdg-utils fonts-noto-cjk libasound2 libnss3 libxss1 \
    libgtk-3-0 libnotify4 libsecret-1-0 libatomic1 libpulse0 libgbm1 libxkbcommon0 libxkbcommon-x11-0 libxcb-xinerama0 libxcb-cursor0 \
    libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 libxcb-xkb1 \
    && rm -rf /var/lib/apt/lists/*

COPY artifacts/WeChatLinux_4.1.1.8_x86_64.deb /tmp/wechat.deb
RUN set -eu; \
    test "$(dpkg-deb -f /tmp/wechat.deb Architecture)" = amd64; \
    test "$(dpkg-deb -f /tmp/wechat.deb Version)" = "${WECHAT_VERSION}"; \
    echo "${WECHAT_SHA256}  /tmp/wechat.deb" | sha256sum --check --strict; \
    apt-get update; apt-get install -y --no-install-recommends /tmp/wechat.deb; \
    rm -rf /var/lib/apt/lists/* /tmp/wechat.deb

RUN useradd --create-home --uid 1000 --shell /bin/bash wechat \
    && mkdir -p /data/wechat /data/config /data/logs /data/diagnostics /run/user/1000 \
    && chown -R wechat:wechat /data /run/user/1000

COPY docker/entrypoint.sh /usr/local/bin/wechat-runtime-entrypoint
COPY scripts/atspi_probe.py /app/scripts/atspi_probe.py
COPY scripts/atspi_parse.py /app/scripts/atspi_parse.py
COPY --from=go-builder /out/wechat-cli /app/wechat-cli
RUN chmod 0755 /usr/local/bin/wechat-runtime-entrypoint
RUN chmod 0755 /app/scripts/atspi_probe.py /app/scripts/atspi_parse.py /app/wechat-cli
WORKDIR /app
ENTRYPOINT ["/usr/local/bin/wechat-runtime-entrypoint"]
