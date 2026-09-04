## Context

真实环境取证（容器时钟 2026-09-03）：
- `Chats` 行格式：`<会话名> [N unread message(s)] [[You were mentioned]] [sender: ]<正文> <时间>`，各段可选；私聊行无 `sender: ` 段。
- `Messages` 列表：时间行（可选，爆发时可缺省）与内容行交替；内容行尾带 `\n ` 伪影、提及正文内含 U+2005（chats 预览与内容行两侧一致）。
- 会话窗标题文本允许 `editable=True`；错误提示（如 "Unable to send message in an exited group chat"）也出现在 Messages 之前，需要以「标题 == chats 行会话名」交叉校验。
- Messages 区段头时间可能滞后于 chats 行权威时间（新消息仍挂在旧区段下）。

## Goals / Non-Goals

Goals:

- 只读**当前打开且为群聊**的会话；用户保证需要读的群聊处于打开状态。
- 每条消息事件正文以 Messages 内容行为准；`is_mention` 由正文含 `@<WECHAT_BOT_NAME>` 判定。
- 仅最新一条（与 chats 预览正文一致）可归属发送者；其余 sender 为空。
- 同文本不同时间可区分（`message_id` 含时间）；连续轮询不重复输出。
- 解析层纯函数可宿主机离线回归（不依赖 gi）。

Non-Goals:

- 不读私聊；不读未打开的群聊；不做轮询补漏。
- 不做逐条「发送者」归属（AT-SPI 内容行不含）；不做 `@昵称` 之外的多候选消歧。

## Decisions

1. **chats 行 = 会话上下文，Messages 行 = 权威正文，二者按「标题==会话名」绑定。**
   focused 不可靠（实测不稳定），弃用；标题节点即使 `editable=True` 也纳入候选，但必须与 chats 行会话名一致，避免状态文案（toast）当选。
2. **提及判定 = 正文含 `@<昵称>`，昵称来自 `WECHAT_BOT_NAME`/`--bot-name`。**
   不做「chats 聚合提及标记 → 预览行」推断：聚合标记指任意未读提及，预览可能指向更新的非 @ 消息（16:25 实测）。
3. **sender 绑定按「最新一条 + 正文与预览一致」，不要求时间一致。**
   实测区段头时间滞后；绑定按行位置而非文本，避免同文本前一条被误绑。
4. **事件身份由探针自算并含时间。**
   `chat_id = derived-chat-<sha256(account|chat)>`，`message_id = derived-time-<sha256(chat|sender|text|time)>`；Go 只解码完整记录、映射字段并去重。
5. **仅群聊；私聊行即使正文含 "@昵称" 也不兜底成群聊。**
   兜底分支只允许 chat hint 未知/无会话行时使用。
6. **增量 seen = (chat_name, text, time_block)**，首扫记基线（`--emit-existing` 才回放）。

## Risks / Trade-offs

- 同一区段时间内同文本重复合并一条（微信未暴露秒级/原生 ID）。
- 提及消息滚出可见区（虚拟列表回收）时正文不可读 → 诊断 + chats 上下文，不猜。
- 中文 UI 文案（未读/提及标记）为预置项未实测，解析残留走 `residue`/诊断校准。
- 群名自身含 `: ` 属罕见误拆（首个冒号段规则），走诊断。

## Migration Plan

1. 落地 `atspi_parse.py` + `atspi_probe.py` 改造 + fixture 单测。
2. 容器内 `docker cp` 更新脚本，以 `WECHAT_BOT_NAME=小半夏` 实机验证（打开群聊，先后发送 `@小半夏 6666` 与普通文本）。
3. 校准 16:25「提及早于最新」场景与同文本两时段场景。
4. 更新 README / compose / wechat.sh 环境变量说明。

## Open Questions

- 更长正文（数十行）在 chats 预览 accessible name 是否会被截断（当前实测长句为全文，未截断）。
- 微信不同语言/版本下未读、提及、时间文案的变体清单仍需实测补充。
