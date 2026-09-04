# Linux Docker 微信 AT-SPI 最小 CLI

这是第一阶段验证工程：固定 Linux 微信 4.1.1.8（`linux/amd64`），在 Docker 中启动 Xvfb、D-Bus、AT-SPI 和 noVNC，并通过 CLI 输出当前前台会话的 AT-SPI 控件树或消息事件。`watch` 模式读取的是**当前打开的群聊**：以 `Chats` 会话列表行为会话上下文（群名/群类型/未读/提及聚合/发送者），以 `Messages` 消息列表内容行为逐条权威正文，正文含 `@<WECHAT_BOT_NAME>` 的消息标记为提及消息（`is_mention=true`）。

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
wechat-cli --mode http \
  --http-addr 0.0.0.0:8090 \
  --probe python3 \
  --probe-arg /app/scripts/atspi_probe.py
```

HTTP 服务只通过宿主机回环地址暴露：`http://127.0.0.1:8090`，供本机 Amadeus 调用。容器内 HTTP 服务会持续观测当前打开会话；原有 `tree`、`watch`、`send` 快捷命令仍可单独执行。

## 本机 HTTP 接口

### 发送消息

`POST http://127.0.0.1:8090/v1/messages/send`

请求头必须包含 `Content-Type: application/json`，请求体只接受 `text` 字段：

```bash
curl -X POST http://127.0.0.1:8090/v1/messages/send \
  -H 'Content-Type: application/json' \
  -d '{"text":"群里提醒"}'
```

消息发送到微信当前已打开的会话，不搜索或切换聊天。成功返回 `200` 和 `send_result` JSON；请求非法返回 `400`，微信/探针未就绪返回 `503`，发送超时返回 `504`，探针执行失败返回 `502`。

### 接收消息（SSE）

`GET http://127.0.0.1:8090/v1/messages/receive`

接口使用 Server-Sent Events 长连接，只推送连接建立后的新消息，不回放历史消息：

```bash
curl -N http://127.0.0.1:8090/v1/messages/receive
```

每条消息以 `event: message` 发送，`data` 为统一事件 JSON（字段包括 `account_id`、`chat_id`、`chat_name`、`chat_type`、`sender_id`、`sender_name`、`message_id`、`text`、`message_type`、`is_mention`、`created_at`、`raw`）。连接空闲时服务端发送 SSE 心跳注释；客户端断开后不会补发断线期间的消息。

## 手工探针

推荐使用项目封装的快捷命令：

```bash
# 查看当前控件树
./scripts/wechat.sh tree

# 持续查看经过 Go 标准化和去重后的消息事件
./scripts/wechat.sh watch

# 从 stdin 向当前已打开会话发送一条文本消息
printf '%s' '自动回复内容' | ./scripts/wechat.sh send

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

## 自动发送消息

`send` 只操作用户已经在 noVNC 中打开的当前会话，不搜索或切换聊天。发送流程会激活唯一微信窗口，通过 AT-SPI 唯一定位并锁定消息输入框，使用 `xclip` 写入剪贴板，再由 `xdotool` 模拟 `Ctrl+V` 和发送快捷键。

stdin 的全部内容作为一条消息原样发送（包括换行）；空输入和纯空白输入会被拒绝。发送键固定为已验证可行的 `Enter`：

```bash
printf '%s' '群里提醒' | ./scripts/wechat.sh send
```

发送成功或失败都会在 stdout 输出一条 `send_result` JSON；失败返回非零退出码并把诊断写入 stderr。发送过程在容器内串行化，不会自动重试。HTTP 发送接口复用同一条 UI 发送链路。

## 群聊消息读取（watch）

`watch` 每次轮询执行：

1. 解析 `Chats` 列表全部行 → 会话上下文（`chat_name`、`chat_type`、`unread_count`、聚合 `mentioned`、最新一条 `sender`/预览正文/时间；群聊行含 `sender: 正文` 冒号段，私聊行没有）。
2. 定位**当前打开的会话**：会话窗标题文本（Messages 列表之前的短文本，允许 editable）必须与某个 chats 行会话名一致，避免把错误提示等状态文案当标题。
3. 仅当打开会话判定为**群聊**时继续：切分 `Messages` 内容行（时间行=可选区段头），逐条输出 message 事件。
4. 每条事件：
   - `text` = 内容行正文（strip 首尾空白，保留正文内 U+2005），**不以 chats 预览顶替**——预览只指向最新一条，提及消息可能更早（此时 chats 行显示的是更新、非 @ 的消息）；
   - `is_mention` = 群聊且正文含 `@<WECHAT_BOT_NAME>`（后随空白/U+2005/标点或结尾）；
   - `sender_name` = 仅当该行是**最新一条**且正文与 chats 预览一致时取预览发送者，否则为空（AT-SPI 内容行不暴露更早消息的发送者）；
   - `message_time` = 区段时间；最新匹配行取 chats 行的权威时间（区段头可能滞后）；
   - `chat_id` = `derived-chat-<sha256(account|chat)>`，`message_id` = `derived-time-<sha256(chat|sender|text|time)>`——身份字段由 Python 探针在输出边界一次生成，Go 只做解码、映射和去重。
5. 增量去重：seen 键 = `(chat_name, text, time_block)`；首扫只记基线，`--emit-existing` 才回放既有行。

依赖环境变量：

| 变量 | 说明 | 默认 |
|---|---|---|
| `WECHAT_BOT_NAME` | 机器人显示昵称，用于识别 `@昵称` 提及消息 | `小半夏` |
| `WECHAT_CHAT_TYPE` | `auto`（自动判定，默认）/ `direct` / `group`（强制） | `auto` |
| `WECHAT_ACCOUNT_ID` | 事件 `account_id` | `default` |

探针同样接受 `--bot-name` / `--chat-type` 显式参数。容器内运行示例：

```bash
docker compose exec -e WECHAT_BOT_NAME=小半夏 wechat-runtime \
  /app/wechat-cli --mode observe \
  --probe python3 --probe-arg /app/scripts/atspi_probe.py --probe-arg watch
```

## 当前限制

- 仅支持 `linux/amd64`、微信 4.1.1.8、**当前打开且为群聊**的会话；私聊/未打开的群聊不产事件（会打 `scan_state` 诊断）。
- HTTP 服务与真实 Docker 图形环境仍需在微信登录后验收；接口仅面向本机 `127.0.0.1`。
- 本机 macOS 无法直接验证真实 AT-SPI；必须在 Docker Linux 图形环境中登录后验收。
- 轮询间隙内出现又消失的消息可能漏报；同一区段时间（HH:MM）内同文本重复会合并为一条（微信未暴露秒级/原生 ID）。
- 更早（非最新）消息的发送者不可得；提及消息滚出可见区时正文读不到（只保留诊断与 chats 上下文）。
- 如果微信 accessible tree 不暴露正文，探针只保留树快照和诊断日志，不切换到数据库或 OCR。
