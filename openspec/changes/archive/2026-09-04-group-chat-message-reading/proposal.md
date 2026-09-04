## Why

一期 CLI 只验证「能不能通过 AT-SPI 读到文本节点」；实测发现在真实 Linux 微信 4.1.1.8 里：
- 打开会话窗的标题节点被标为 `editable=True`，旧 `infer_chat_name` 一律跳过 → 群聊 `chat_name` 为空 → Go 侧因 `chat_name` 必填把整条群消息丢弃。
- `Chats` 会话列表行自带群名/未读/提及聚合/发送者/最新正文/时间，是可靠的会话上下文来源。
- `[You were mentioned]` 是聚合标记：提及消息可能不是最新一条（16:25 实测：chats 预览指向 `半夏: 123321`，真正的 @机器人 消息是更早的 `@小半夏 6666`），因此**正文必须以 `Messages` 内容行为准**，不能拿 chats 预览顶替。

## What Changes

- 新增纯解析模块 `scripts/atspi_parse.py`（不依赖 gi）：`Chats` 行解析、会话窗标题定位、`Messages` 内容行切分、`@昵称` 提及判定、事件与 `message_id` 组装。
- `scripts/atspi_probe.py` `watch` 改为只读**当前打开且为群聊**的会话：chats 行提供上下文，Messages 内容行逐条输出事件，正文含 `@<WECHAT_BOT_NAME>` 即 `is_mention=true`；新增 `--bot-name`（env `WECHAT_BOT_NAME`）与 `--chat-type auto|direct|group`（默认 auto）。
- 事件 `chat_id`/`message_id` 由探针在输出边界生成并含区段时间（`derived-*`），同文本不同时间可区分；Go 层只做 JSONL 解码、字段映射和去重。
- 新增离线 fixture 与 `scripts/test_parse.py` 单测（宿主机 `python scripts/test_parse.py` 可跑）。

## Capabilities

### New Capabilities

- `group-chat-message-reading`：以 `Chats` 列表为会话上下文、`Messages` 列表为权威正文，正确读取当前打开群聊的消息，并准确识别含 `@机器人昵称` 的提及消息。

### Modified Capabilities

- `wechat-atspi-cli`：`watch` 输出语义从「消息候选文本节点」升级为「打开的群聊消息事件」；`--chat-type` 增加 `auto`；新增 `--bot-name`。

## Impact

- 新增 `scripts/atspi_parse.py`、`scripts/test_parse.py`、`scripts/testdata/*.jsonl`；修改 `scripts/atspi_probe.py`。
- `README.md`、`docker-compose.yml`（`WECHAT_BOT_NAME`/`WECHAT_CHAT_TYPE`/`WECHAT_ACCOUNT_ID` 透传）、`scripts/wechat.sh`（透传环境变量）。
- Go 处理层保持纯粹（探针自算身份，Go Deduper 只消费已完整事件，避免误并同文本不同时间的两条）。
- 仍只使用 AT-SPI 控件树，不引入数据库/内存读取/Hook/OCR。
