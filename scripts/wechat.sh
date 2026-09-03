#!/usr/bin/env bash

# 微信最小 Demo 的常用运维入口。
#
# 这个脚本只负责封装 Docker 和 AT-SPI 环境变量，不实现业务逻辑。
# 业务读取仍由 /app/wechat-cli 和 /app/scripts/atspi_probe.py 完成。

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
SERVICE="wechat-runtime"

usage() {
  cat <<'EOF'
用法:
  ./scripts/wechat.sh tree       输出当前微信 AT-SPI 控件树
  ./scripts/wechat.sh watch      持续输出文本/图片消息事件
  ./scripts/wechat.sh logs       查看 Runtime 日志
  ./scripts/wechat.sh status     查看容器状态
  ./scripts/wechat.sh shell      进入 Runtime 容器
  ./scripts/wechat.sh rebuild    重建镜像并重新创建容器
  ./scripts/wechat.sh help       显示帮助

环境变量:
  POLL_INTERVAL=1       watch 轮询间隔，单位秒
  MAX_DEPTH=60          控件树最大遍历深度
  COMPOSE_FILE=...      覆盖 docker compose 文件
EOF
}

compose() {
  local compose_file="${COMPOSE_FILE:-${PROJECT_DIR}/docker-compose.yml}"
  docker compose -f "${compose_file}" "$@"
}

runtime_exec() {
  compose exec -T "${SERVICE}" bash -lc '
    exec runuser -u wechat -- env \
      DISPLAY=:99 \
      HOME=/data/wechat \
      DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
      AT_SPI_BUS_ADDRESS=unix:path=/home/wechat/.cache/at-spi/bus_99 \
      QT_ACCESSIBILITY=1 \
      QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 \
      "$@"
  ' -- "$@"
}

tree() {
  runtime_exec /app/wechat-cli \
    --mode probe \
    --probe python3 \
    --probe-arg /app/scripts/atspi_probe.py \
    --probe-arg dump \
    --probe-arg --max-depth \
    --probe-arg "${MAX_DEPTH:-60}"
}

watch_messages() {
  runtime_exec /app/wechat-cli \
    --mode observe \
    --probe python3 \
    --probe-arg /app/scripts/atspi_probe.py \
    --probe-arg watch \
    --probe-arg --poll-interval \
    --probe-arg "${POLL_INTERVAL:-1}"
}

main() {
  local command="${1:-help}"
  case "${command}" in
    tree)
      tree
      ;;
    watch)
      watch_messages
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
