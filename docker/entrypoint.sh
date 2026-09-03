#!/usr/bin/env bash
set -Eeuo pipefail

export DISPLAY="${DISPLAY:-:99}"
export SCREEN_SIZE="${SCREEN_SIZE:-1920x1080x24}"
export VNC_PORT="${VNC_PORT:-5900}"
export NOVNC_PORT="${NOVNC_PORT:-6080}"
export HOME="${HOME:-/data/wechat}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-/data/config}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/1000/bus}"
# AT-SPI uses a dedicated accessibility bus created by at-spi-bus-launcher.
# Point both WeChat and the probe at the per-display bus instead of relying on
# the generic D-Bus session bus (which does not contain accessible apps).
export AT_SPI_BUS_ADDRESS="${AT_SPI_BUS_ADDRESS:-unix:path=/home/wechat/.cache/at-spi/bus_99}"
# Enable Qt's accessibility bridge so the Linux WeChat Qt UI registers with AT-SPI.
export QT_ACCESSIBILITY="${QT_ACCESSIBILITY:-1}"
export QT_LINUX_ACCESSIBILITY_ALWAYS_ON="${QT_LINUX_ACCESSIBILITY_ALWAYS_ON:-1}"

mkdir -p /data/wechat /data/config /data/logs /data/diagnostics /run/user/1000
chown -R wechat:wechat /data /run/user/1000
rm -f "/tmp/.X${DISPLAY#:}-lock"

cleanup() {
  set +e
  [[ -n "${NOVNC_PID:-}" ]] && kill "${NOVNC_PID}" 2>/dev/null || true
  [[ -n "${X11VNC_PID:-}" ]] && kill "${X11VNC_PID}" 2>/dev/null || true
  [[ -n "${WECHAT_PID:-}" ]] && kill "${WECHAT_PID}" 2>/dev/null || true
  [[ -n "${XVFB_PID:-}" ]] && kill "${XVFB_PID}" 2>/dev/null || true
  [[ -n "${DBUS_PID:-}" ]] && kill "${DBUS_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

Xvfb "${DISPLAY}" -screen 0 "${SCREEN_SIZE}" -ac +extension RANDR > /data/logs/xvfb.log 2>&1 &
XVFB_PID=$!
for _ in $(seq 1 50); do
  if xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1; then break; fi
  sleep 0.2
done
xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1 || { echo "Xvfb 未能启动" >&2; exit 1; }

install -d -m 700 -o wechat -g wechat /run/user/1000
runuser -u wechat -- dbus-daemon --session --address="${DBUS_SESSION_BUS_ADDRESS}" --nofork > /data/logs/dbus.log 2>&1 &
DBUS_PID=$!
for _ in $(seq 1 50); do
  if [[ -S /run/user/1000/bus ]]; then break; fi
  sleep 0.2
done

runuser -u wechat -- /usr/libexec/at-spi-bus-launcher --launch-immediately > /data/logs/at-spi.log 2>&1 &
for _ in $(seq 1 50); do
  if [[ -S /home/wechat/.cache/at-spi/bus_99 ]]; then break; fi
  sleep 0.2
done
# Xvfb does not provide a usable MIT-SHM segment in this container; disable
# shared-memory capture so x11vnc stays alive for noVNC connections.
runuser -u wechat -- x11vnc -display "${DISPLAY}" -noshm -forever -shared -rfbport "${VNC_PORT}" -nopw > /data/logs/x11vnc.log 2>&1 &
X11VNC_PID=$!
websockify --web=/usr/share/novnc "${NOVNC_PORT}" "localhost:${VNC_PORT}" > /data/logs/novnc.log 2>&1 &
NOVNC_PID=$!

WECHAT_BIN="${WECHAT_BIN:-/usr/bin/wechat}"
if [[ ! -x "${WECHAT_BIN}" ]]; then
  echo "未找到微信可执行文件: ${WECHAT_BIN}" >&2
  echo "请设置 WECHAT_BIN，或在 /app/wechat-cli 提供 CLI" >&2
  exit 1
fi
runuser -u wechat -- env DISPLAY="${DISPLAY}" HOME="${HOME}" XDG_CONFIG_HOME="${XDG_CONFIG_HOME}" DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS}" AT_SPI_BUS_ADDRESS="${AT_SPI_BUS_ADDRESS}" QT_ACCESSIBILITY="${QT_ACCESSIBILITY}" QT_LINUX_ACCESSIBILITY_ALWAYS_ON="${QT_LINUX_ACCESSIBILITY_ALWAYS_ON}" "${WECHAT_BIN}" > /data/logs/wechat.log 2>&1 &
WECHAT_PID=$!

if [[ -x /app/wechat-cli ]]; then
  if [[ "$#" -eq 0 ]]; then
    set -- --mode observe \
      --probe python3 \
      --probe-arg /app/scripts/atspi_probe.py \
      --probe-arg watch \
      --probe-arg --poll-interval \
      --probe-arg 1
  fi
  exec runuser -u wechat -- env DISPLAY="${DISPLAY}" HOME="${HOME}" XDG_CONFIG_HOME="${XDG_CONFIG_HOME}" DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS}" AT_SPI_BUS_ADDRESS="${AT_SPI_BUS_ADDRESS}" QT_ACCESSIBILITY="${QT_ACCESSIBILITY}" QT_LINUX_ACCESSIBILITY_ALWAYS_ON="${QT_LINUX_ACCESSIBILITY_ALWAYS_ON}" /app/wechat-cli "$@"
fi

wait "${WECHAT_PID}"
