# Linux Docker 微信 AT-SPI 最小 CLI

这是第一阶段验证工程：固定 Linux 微信 4.1.1.8（`linux/amd64`），在 Docker 中启动 Xvfb、D-Bus、AT-SPI 和 noVNC，并通过 CLI 输出当前前台会话的 AT-SPI 控件树或文本消息候选。

## 准备安装包

将微信安装包放到：

```text
artifacts/WeChatLinux_4.1.1.8_x86_64.deb
```

构建会校验架构、版本和 SHA-256。校验值和检查命令见 [artifacts/README.md](artifacts/README.md)。

## 启动

```bash
docker compose build
docker compose up -d
```

浏览器打开 `http://127.0.0.1:6080/vnc.html`，在虚拟显示中完成微信登录。

默认容器启动命令为：

```text
wechat-cli --mode observe \
  --probe python3 \
  --probe-arg /app/scripts/atspi_probe.py \
  --probe-arg watch \
  --probe-arg --poll-interval \
  --probe-arg 1
```

## 手工探针

推荐使用项目封装的快捷命令：

```bash
# 查看当前控件树
./scripts/wechat.sh tree

# 持续查看经过 Go 标准化和去重后的消息事件
./scripts/wechat.sh watch

# 查看 Runtime 日志
./scripts/wechat.sh logs

# 查看所有命令
./scripts/wechat.sh help
```

如果只想查看控件树中的关键节点，可以配合 `jq`：

```bash
./scripts/wechat.sh tree | jq -c '
  select(
    .name == "Chats" or
    .name == "Messages" or
    .role == "list item" or
    .role == "image" or
    .editable == true
  )
'
```

以下为底层完整命令，仅用于排查环境变量或 AT-SPI 连接问题。

进入容器后可执行控件树输出：

```bash
docker compose exec wechat-runtime \
  /app/wechat-cli --mode probe \
  --probe python3 \
  --probe-arg /app/scripts/atspi_probe.py \
  --probe-arg dump \
  > /data/diagnostics/tree.jsonl
```

消息观测输出为 JSON Lines，诊断日志写入容器日志目录和标准错误。

## 当前限制

- 仅支持 `linux/amd64`、微信 4.1.1.8、当前前台会话和普通文本。
- 发送命令尚未实现。
- 本机 macOS 无法直接验证真实 AT-SPI；必须在 Docker Linux 图形环境中登录后验收。
- 如果微信 accessible tree 不暴露正文，探针只保留树快照和诊断日志，不切换到数据库或 OCR。
