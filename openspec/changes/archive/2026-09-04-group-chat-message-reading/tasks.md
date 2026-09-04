## 1. 解析模块（scripts/atspi_parse.py）

- [x] 1.1 `Chats`/`Messages` 容器定位与直接子行提取
- [x] 1.2 `parse_chat_row`：剥未读/提及/时间 token（IGNORECASE，中英文案可扩展），群聊（`sender: ` 段）与私聊（无段）两种行解析，残留进 `residue`
- [x] 1.3 会话窗标题定位：允许 editable、必须与 chats 行会话名一致（`find_pane_title(..., chat_names)`）
- [x] 1.4 `split_message_rows`：时间行=区段头（可选），内容行/图片表情行产出
- [x] 1.5 `is_mention_of(text, bot_name)`：`@昵称` 边界判定（保留 U+2005 正文）
- [x] 1.6 `extract_group_events`：群聊判定、sender 按“最新一条+正文一致”绑定（按位置不按文本）、增量 seen、`message_id`（含时间）

## 2. 探针改造（scripts/atspi_probe.py）

- [x] 2.1 `--bot-name`（env `WECHAT_BOT_NAME`）与 `--chat-type auto|direct|group`（默认 auto）
- [x] 2.2 `watch` 接入群聊分支：chats 上下文 + Messages 内容行事件输出，`scan_state` 诊断
- [x] 2.3 dump 模式行为不变

## 3. 离线回归

- [x] 3.1 `scripts/testdata/` fixture：16:25 提及早于最新、同文本两时段、私聊打开、标题 toast 干扰等
- [x] 3.2 `scripts/test_parse.py`：行解析/提及/身份/增量/兜底等断言，宿主机 `python scripts/test_parse.py` 全绿
- [x] 3.3 `go test ./...`、`go vet ./...` 通过（Go 零改动回归确认）

## 4. 实机验证（容器 2026-09-03）

- [x] 4.1 打开群聊 + `WECHAT_BOT_NAME=小半夏`：`group_open=true`、标题正确
- [x] 4.2 发送 `@小半夏 你好666` → 事件 `is_mention=true`、正文正确、`message_time` 正确
- [x] 4.3 最新 `@小半夏 你还是不招吗` → `sender_name=老冯`（预览绑定）、`message_time=16:52`（chats 权威时间，区段头滞后时仍绑定）
- [x] 4.4 打开私聊 → 无事件，`reason=open_chat_not_group`
- [x] 4.5 重启容器（compose 环境变量注入）后验证 `WECHAT_BOT_NAME` 透传
