# 第一阶段只负责构建无 CGO 依赖的 Go CLI。
FROM --platform=linux/amd64 golang:1.25.5-bookworm AS go-builder

WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal
# 在目标容器架构上编译静态 Go CLI，便于运行阶段不依赖 Go 工具链。
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' -o /out/wechat-cli ./cmd/wechat-cli
# 第二阶段提供固定架构的 Ubuntu 图形运行环境。

FROM --platform=linux/amd64 ubuntu:22.04

ARG WECHAT_VERSION=4.1.1.8
ARG WECHAT_SHA256=c9765e87ee5133bf4bb50d585c1814fafd995e3fb0da62c5ed07259b43dada7b

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 DISPLAY=:99 HOME=/data/wechat XDG_CONFIG_HOME=/data/config DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus

# 安装微信、AT-SPI、Xvfb、VNC 和 Python 探针所需的运行时依赖。
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates dbus dbus-x11 at-spi2-core gir1.2-atspi-2.0 python3 python3-gi python3-dbus desktop-file-utils \
    x11vnc xvfb x11-utils xdotool xclip novnc websockify xauth xdg-utils fonts-noto-cjk libasound2 libnss3 libxss1 \
    libgtk-3-0 libnotify4 libsecret-1-0 libatomic1 libpulse0 libgbm1 libxkbcommon0 libxkbcommon-x11-0 libxcb-xinerama0 libxcb-cursor0 \
    libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 libxcb-xkb1 \
    && rm -rf /var/lib/apt/lists/*

COPY artifacts/WeChatLinux_4.1.1.8_x86_64.deb /tmp/wechat.deb
# 安装前严格校验 deb 的架构、版本和 SHA-256，防止输入包不匹配。
RUN set -eu; \
    test "$(dpkg-deb -f /tmp/wechat.deb Architecture)" = amd64; \
    test "$(dpkg-deb -f /tmp/wechat.deb Version)" = "${WECHAT_VERSION}"; \
    echo "${WECHAT_SHA256}  /tmp/wechat.deb" | sha256sum --check --strict; \
    apt-get update; apt-get install -y --no-install-recommends /tmp/wechat.deb; \
    rm -rf /var/lib/apt/lists/* /tmp/wechat.deb

# 使用固定 UID 的非 root 用户运行微信和探针。
RUN useradd --create-home --uid 1000 --shell /bin/bash wechat \
    && mkdir -p /data/wechat /data/config /data/logs /data/diagnostics /run/user/1000 \
    && chown -R wechat:wechat /data /run/user/1000

# 复制容器入口、Python 探针和已编译 CLI。
COPY docker/entrypoint.sh /usr/local/bin/wechat-runtime-entrypoint
COPY scripts/atspi_probe.py /app/scripts/atspi_probe.py
COPY scripts/atspi_parse.py /app/scripts/atspi_parse.py
COPY --from=go-builder /out/wechat-cli /app/wechat-cli
RUN chmod 0755 /usr/local/bin/wechat-runtime-entrypoint
RUN chmod 0755 /app/scripts/atspi_probe.py /app/scripts/atspi_parse.py /app/wechat-cli
WORKDIR /app
ENTRYPOINT ["/usr/local/bin/wechat-runtime-entrypoint"]
