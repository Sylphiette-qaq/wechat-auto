#!/usr/bin/env bash

# 微信最小 Demo 的常用运维入口。
#
# 这个脚本只负责封装 Docker 和 AT-SPI 环境变量，不实现业务逻辑。
# 业务读取仍由 /app/wechat-cli 和 /app/scripts/atspi_probe.py 完成。

set -Eeuo pipefail

# Git Bash / MSYS2（Windows）在调用原生 docker.exe 前，会把以 "/" 开头的
# 参数（如 /app/wechat-cli）自动改写成 Windows 路径（如 D:/soft/Git/app/
# wechat-cli），再原样传入容器导致 exec 失败。这里关闭自动路径转换；
# 在原生 Linux 上这两个变量无人读取，无副作用。
#
# 关闭转换后需要自己区分两类路径：
#   - 容器内路径（/app/...）必须保持 POSIX 原样，不可转换；
#   - 宿主机文件路径（docker-compose.yml）必须显式转成 Windows 路径
#     （见 hostpath / compose），否则 Windows 版 docker 会把 "/d/..." 误读为
#     当前盘根目录下的 "d\..."。
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
SERVICE="wechat-runtime"

# 仅在 Git Bash/MSYS2 下把宿主 POSIX 路径转成 Windows 路径（供 docker.exe
# 打开宿主文件）；原生 Linux 下 cygpath 不存在，原样返回。
hostpath() {
  # 将宿主机路径转换为当前 Docker 客户端可识别的格式。
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
  else
    printf '%s' "$1"
  fi
}

usage() {
  # 输出所有支持的运维子命令和环境变量说明。
  cat <<'EOF'
用法:
  ./scripts/wechat.sh tree       输出当前微信 AT-SPI 控件树
  ./scripts/wechat.sh watch      持续输出文本/图片消息事件
  ./scripts/wechat.sh send       从 stdin 向当前会话发送一条文本
  本机 HTTP API               http://127.0.0.1:8090（容器默认启动）
  ./scripts/wechat.sh logs       查看 Runtime 日志
  ./scripts/wechat.sh status     查看容器状态
  ./scripts/wechat.sh shell      进入 Runtime 容器
  ./scripts/wechat.sh rebuild    重建镜像并重新创建容器
  ./scripts/wechat.sh help       显示帮助

环境变量:
  POLL_INTERVAL=1       watch 轮询间隔，单位秒
  MAX_DEPTH=60          控件树最大遍历深度
  SEND_KEY=enter        发送快捷键：仅支持 enter
  SEND_TIMEOUT=10s      发送超时时间
  COMPOSE_FILE=...      覆盖 docker compose 文件
EOF
}

compose() {
  # 统一注入 compose 文件路径，再执行传入的 docker compose 子命令。
  local compose_file
  compose_file="$(hostpath "${COMPOSE_FILE:-${PROJECT_DIR}/docker-compose.yml}")"
  docker compose -f "${compose_file}" "$@"
}

runtime_exec() {
  # 以 wechat 用户身份进入运行容器，并补齐图形与 AT-SPI 环境变量。
  # 把宿主机的机器人配置透传给容器（未设置则不附加 -e，容器内用默认值）
  local extra_args=()
  [[ -n "${WECHAT_ACCOUNT_ID:-}" ]] && extra_args+=(-e "WECHAT_ACCOUNT_ID=${WECHAT_ACCOUNT_ID}")
  [[ -n "${WECHAT_BOT_NAME:-}" ]] && extra_args+=(-e "WECHAT_BOT_NAME=${WECHAT_BOT_NAME}")
  [[ -n "${WECHAT_CHAT_TYPE:-}" ]] && extra_args+=(-e "WECHAT_CHAT_TYPE=${WECHAT_CHAT_TYPE}")
  [[ -n "${SEND_KEY:-}" ]] && extra_args+=(-e "SEND_KEY=${SEND_KEY}")
  [[ -n "${SEND_TIMEOUT:-}" ]] && extra_args+=(-e "SEND_TIMEOUT=${SEND_TIMEOUT}")
  local exec_args=(-T)
  if ((${#extra_args[@]} > 0)); then
    exec_args+=("${extra_args[@]}")
  fi
  compose exec "${exec_args[@]}" "${SERVICE}" bash -lc '
    exec runuser -u wechat -- env \
      DISPLAY=:99 \
      HOME=/data/wechat \
      DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
      AT_SPI_BUS_ADDRESS=unix:path=/home/wechat/.cache/at-spi/bus_99 \
      QT_ACCESSIBILITY=1 \
      QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 \
      WECHAT_ACCOUNT_ID="$WECHAT_ACCOUNT_ID" \
      WECHAT_BOT_NAME="$WECHAT_BOT_NAME" \
      WECHAT_CHAT_TYPE="$WECHAT_CHAT_TYPE" \
      SEND_KEY="$SEND_KEY" \
      SEND_TIMEOUT="$SEND_TIMEOUT" \
      "$@"
  ' -- "$@"
}

tree() {
  # 请求 Go CLI 以 probe 模式导出当前 AT-SPI 控件树。
  runtime_exec /app/wechat-cli \
    --mode probe \
    --probe python3 \
    --probe-arg /app/scripts/atspi_probe.py \
    --probe-arg dump \
    --probe-arg --max-depth \
    --probe-arg "${MAX_DEPTH:-60}"
}

watch_messages() {
  # 请求 Go CLI 以 observe 模式持续输出去重后的消息事件。
  runtime_exec /app/wechat-cli \
    --mode observe \
    --probe python3 \
    --probe-arg /app/scripts/atspi_probe.py \
    --probe-arg watch \
    --probe-arg --poll-interval \
    --probe-arg "${POLL_INTERVAL:-1}"
}

send_message() {
  # 从 stdin 读取一条完整消息，发送器不拆分换行。
  runtime_exec /app/wechat-cli \
    --mode send \
    --probe python3 \
    --probe-arg /app/scripts/atspi_probe.py
}

main() {
  # 根据第一个位置参数分派到具体运维操作；缺省显示帮助。
  local command="${1:-help}"
  case "${command}" in
    tree)
      tree
      ;;
    watch)
      watch_messages
      ;;
    send)
      send_message
      ;;
    logs)
      compose logs -f "${SERVICE}"
      ;;
    status)
      compose ps
      ;;
    shell)
      compose exec "${SERVICE}" bash
      ;;
    rebuild)
      compose build
      compose up -d --force-recreate
      ;;
    help|-h|--help)
      usage
      ;;
    *)
      echo "未知命令: ${command}" >&2
      usage >&2
      return 2
      ;;
  esac
}

main "$@"
