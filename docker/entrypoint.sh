#!/usr/bin/env bash
set -Eeuo pipefail

export DISPLAY="${DISPLAY:-:99}"
export SCREEN_SIZE="${SCREEN_SIZE:-1920x1080x24}"
export VNC_PORT="${VNC_PORT:-5900}"
export NOVNC_PORT="${NOVNC_PORT:-6080}"
export HOME="${HOME:-/data/wechat}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-/data/config}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/1000/bus}"
# AT-SPI 使用 at-spi-bus-launcher 创建的独立无障碍总线。
# 微信和探针都指向当前显示器的专用总线，不能依赖不包含无障碍应用的通用 D-Bus。
export AT_SPI_BUS_ADDRESS="${AT_SPI_BUS_ADDRESS:-unix:path=/home/wechat/.cache/at-spi/bus_99}"
# 启用 Qt 无障碍桥接，使 Linux 微信 Qt 界面注册到 AT-SPI。
export QT_ACCESSIBILITY="${QT_ACCESSIBILITY:-1}"
export QT_LINUX_ACCESSIBILITY_ALWAYS_ON="${QT_LINUX_ACCESSIBILITY_ALWAYS_ON:-1}"

mkdir -p /data/wechat /data/config /data/logs /data/diagnostics /run/user/1000
chown -R wechat:wechat /data /run/user/1000
rm -f "/tmp/.X${DISPLAY#:}-lock"

cleanup() {
  # 容器退出时按记录的 PID 清理图形、微信和辅助服务进程。
  set +e
  [[ -n "${NOVNC_PID:-}" ]] && kill "${NOVNC_PID}" 2>/dev/null || true
  [[ -n "${X11VNC_PID:-}" ]] && kill "${X11VNC_PID}" 2>/dev/null || true
  [[ -n "${WECHAT_PID:-}" ]] && kill "${WECHAT_PID}" 2>/dev/null || true
  [[ -n "${XVFB_PID:-}" ]] && kill "${XVFB_PID}" 2>/dev/null || true
  [[ -n "${DBUS_PID:-}" ]] && kill "${DBUS_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 启动虚拟 X 服务，并等待显示器真正可用。
Xvfb "${DISPLAY}" -screen 0 "${SCREEN_SIZE}" -ac +extension RANDR > /data/logs/xvfb.log 2>&1 &
XVFB_PID=$!
for _ in $(seq 1 50); do
  if xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1; then break; fi
  sleep 0.2
done
xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1 || { echo "Xvfb 未能启动" >&2; exit 1; }

# 为 wechat 用户启动 D-Bus session bus，供 Qt 和 AT-SPI 通信。
install -d -m 700 -o wechat -g wechat /run/user/1000
runuser -u wechat -- dbus-daemon --session --address="${DBUS_SESSION_BUS_ADDRESS}" --nofork > /data/logs/dbus.log 2>&1 &
DBUS_PID=$!
for _ in $(seq 1 50); do
  if [[ -S /run/user/1000/bus ]]; then break; fi
  sleep 0.2
done

# 启动独立的 AT-SPI 无障碍总线，探针通过该总线发现微信控件。
runuser -u wechat -- /usr/libexec/at-spi-bus-launcher --launch-immediately > /data/logs/at-spi.log 2>&1 &
for _ in $(seq 1 50); do
  if [[ -S /home/wechat/.cache/at-spi/bus_99 ]]; then break; fi
  sleep 0.2
done
# 提供 VNC/noVNC 访问入口，便于人工完成微信登录和诊断界面。
# 当前容器中的 Xvfb 不提供可用的 MIT-SHM 段，因此关闭共享内存抓屏，
# 确保 x11vnc 能持续服务 noVNC 连接。
runuser -u wechat -- x11vnc -display "${DISPLAY}" -noshm -forever -shared -rfbport "${VNC_PORT}" -nopw > /data/logs/x11vnc.log 2>&1 &
X11VNC_PID=$!
websockify --web=/usr/share/novnc "${NOVNC_PORT}" "localhost:${VNC_PORT}" > /data/logs/novnc.log 2>&1 &
NOVNC_PID=$!

WECHAT_BIN="${WECHAT_BIN:-/usr/bin/wechat}"
# 启动固定版本的微信客户端，并将图形/无障碍环境传递给它。
if [[ ! -x "${WECHAT_BIN}" ]]; then
  echo "未找到微信可执行文件: ${WECHAT_BIN}" >&2
  echo "请设置 WECHAT_BIN，或在 /app/wechat-cli 提供 CLI" >&2
  exit 1
fi
runuser -u wechat -- env DISPLAY="${DISPLAY}" HOME="${HOME}" XDG_CONFIG_HOME="${XDG_CONFIG_HOME}" DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS}" AT_SPI_BUS_ADDRESS="${AT_SPI_BUS_ADDRESS}" QT_ACCESSIBILITY="${QT_ACCESSIBILITY}" QT_LINUX_ACCESSIBILITY_ALWAYS_ON="${QT_LINUX_ACCESSIBILITY_ALWAYS_ON}" "${WECHAT_BIN}" > /data/logs/wechat.log 2>&1 &
WECHAT_PID=$!

if [[ -x /app/wechat-cli ]]; then
  # 未显式传参时，默认进入持续观测模式。
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
