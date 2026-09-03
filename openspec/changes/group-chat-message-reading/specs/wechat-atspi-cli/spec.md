## ADDED Requirements

### Requirement: 当前打开群聊的消息正确读取
CLI SHALL 只读当前打开且为群聊的会话，以 `Chats` 会话列表行为上下文（群名、群类型、未读、聚合提及、发送者、时间），以 `Messages` 列表内容行为逐条权威正文输出 message 事件，正文含 `@<WECHAT_BOT_NAME>` 的消息标记为提及消息（`is_mention=true`）。

#### Scenario: 提及消息早于最新消息
- **WHEN** 打开的群聊中，`Chats` 预览指向更新的非 @ 消息（如 `半夏: 123321`），而 `Messages` 列表存在更早的 @机器人 消息（如 `@小半夏 6666`），机器人昵称配置为 `小半夏`
- **THEN** 输出的提及事件正文为 `@小半夏 6666` 且 `is_mention=true`，不把 `123321` 当提及正文

#### Scenario: 群聊普通消息
- **WHEN** 打开的群聊出现不包含 @昵称 的新内容行
- **THEN** CLI 输出该内容行正文的事件，`is_mention=false`，正文来自 Messages 内容行而非 chats 预览

#### Scenario: 发送者归属
- **WHEN** 最新一条内容行正文与 chats 预览正文一致（即使区段头时间滞后）
- **THEN** 该事件 `sender_name` 取 chats 预览发送者；更早内容行 `sender_name` 为空

#### Scenario: 私聊/未打开群聊不产事件
- **WHEN** 当前打开的是私聊（无 `sender: ` 冒号段的 chats 行）或没有打开的群聊
- **THEN** 不输出 message 事件，并输出 `scan_state`/`reason` 诊断

### Requirement: 提及判定与昵称配置
CLI SHALL 通过环境变量 `WECHAT_BOT_NAME`（或探针 `--bot-name`）获取机器人昵称；消息正文含 `@昵称`（后随空白/U+2005/标点或结尾）即视为提及。昵称未配置时输出 `bot_name_required` 类诊断，不做逐行 @ 判定。

#### Scenario: 昵称来自环境变量
- **WHEN** 容器环境设置 `WECHAT_BOT_NAME=小半夏` 且收到正文 `@小半夏 你好666`
- **THEN** 事件 `is_mention=true`

### Requirement: 事件身份与增量输出
CLI SHALL 为每条事件生成含区段时间的身份 `derived-time-<sha256(chat|sender|text|time)>`，使同文本同发送者不同时间的消息可区分；连续轮询不重复输出（seen 键 = chat_name+text+time_block，首扫只记基线）。

#### Scenario: 同文本不同时间
- **WHEN** 同一发送者在不同时间发送相同正文（如 16:10 与 16:19 同一长句）
- **THEN** 两条事件都输出且 `message_id` 不同
