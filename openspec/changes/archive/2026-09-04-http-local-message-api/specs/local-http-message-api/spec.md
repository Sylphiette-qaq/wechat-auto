## ADDED Requirements

### Requirement: Go HTTP 运行模式

系统 SHALL 在现有 Go CLI 中新增 `http` 运行模式，使用 Go 标准库 `net/http` 同时提供消息发送和接收接口，并 MUST 保留现有 `probe`、`observe` 和 `send` 模式的行为。Docker 容器在未显式传入 CLI 参数时 SHALL 默认启动 `http` 模式。

#### Scenario: 默认启动 HTTP 服务

- **WHEN** Docker 容器在未传入额外 CLI 参数的情况下启动
- **THEN** 容器中的 `wechat-cli` 以 `--mode http` 运行，启动 HTTP 服务并在后台消费持续观测消息流

#### Scenario: 原有 CLI 模式保持可用

- **WHEN** 调用方显式选择 `probe`、`observe` 或 `send` 模式
- **THEN** CLI 仍按该模式原有契约执行，不启动 HTTP 服务

### Requirement: 本机端口边界

HTTP 服务 SHALL 在容器内监听 `0.0.0.0:8090`，Docker Compose MUST 仅将该端口映射为宿主机 `127.0.0.1:8090:8090`，使 Amadeus 通过 `http://127.0.0.1:8090` 访问且不将业务端口暴露给局域网或公网。

#### Scenario: 宿主机本机访问

- **WHEN** 容器以 Docker Compose 配置启动且 Amadeus 从同一宿主机访问 `127.0.0.1:8090`
- **THEN** 请求被转发到容器内的 HTTP 服务

#### Scenario: 端口不绑定宿主机所有网卡

- **WHEN** 检查 Docker Compose 的 HTTP 端口发布配置
- **THEN** 宿主机绑定地址为 `127.0.0.1` 而不是 `0.0.0.0` 或未指定地址

### Requirement: 当前会话文本发送接口

系统 SHALL 在 `POST /v1/messages/send` 接收 `Content-Type: application/json` 的 JSON 对象，且请求对象 MUST 仅包含一个字符串字段 `text`。系统 SHALL 复用现有 UI 发送链路，把全部 `text` 作为一条普通文本消息发送到当前已打开且可确认的微信会话，MUST NOT 搜索或切换会话。

#### Scenario: 成功发送 JSON 文本

- **WHEN** 微信自动化链路已就绪，当前会话可确认，且客户端提交 `{"text":"hello\\nworld"}`
- **THEN** 系统将 `hello\nworld` 作为一条消息发送到当前会话，并以 HTTP `200` 返回 `accepted=true` 的 JSON `send_result`

#### Scenario: 不接受目标会话参数

- **WHEN** 请求 JSON 除 `text` 外还包含 `chat_id`、`chat_name` 或其他字段
- **THEN** 系统以 HTTP `400` 拒绝请求，且不执行任何微信 UI 操作

### Requirement: 发送请求校验

发送接口 MUST 拒绝非 JSON 内容、无法解析的 JSON、缺失或非字符串的 `text`、零长度或纯空白的 `text`，以及超过 1 MiB 的请求体。校验失败时 MUST NOT 启动发送探针或操作微信 UI。

#### Scenario: 拒绝非 JSON 请求

- **WHEN** 客户端以非 `application/json` 内容类型或无法解析的 JSON 调用发送接口
- **THEN** 系统以 HTTP `400` 返回 JSON 错误结果且不发送消息

#### Scenario: 拒绝空白文本

- **WHEN** `text` 为空字符串或仅包含空格、制表符和换行
- **THEN** 系统以 HTTP `400` 返回 `error_code=invalid_input` 的 JSON `send_result`，且不发送消息

#### Scenario: 拒绝超限请求体

- **WHEN** 发送请求体大于 1 MiB
- **THEN** 系统以 HTTP `400` 拒绝请求，且不继续读取或发送该消息

### Requirement: 发送结果 HTTP 映射

发送接口 SHALL 以 JSON `send_result` 返回现有发送链路的结果，并 MUST 使用以下 HTTP 状态码：发送成功为 `200`；请求校验失败为 `400`；`send_busy` 为 `409`；观测或微信自动化链路未就绪为 `503`；`send_timeout` 为 `504`；其他发送探针执行失败为 `502`。

#### Scenario: 发送超时映射

- **WHEN** 现有 UI 发送链路返回 `error_code=send_timeout`
- **THEN** HTTP 接口保留该 `send_result` 错误信息并返回状态码 `504`

#### Scenario: 其他发送失败映射

- **WHEN** 发送探针返回不属于校验、忙、未就绪或超时的结构化失败
- **THEN** HTTP 接口保留该 `send_result` 错误信息并返回状态码 `502`

### Requirement: SSE 新消息接收接口

系统 SHALL 在 `GET /v1/messages/receive` 以 Server-Sent Events 长连接推送现有 `observe` 流中已归一化和去重的消息事件。成功连接 MUST 使用 `Content-Type: text/event-stream`；每条消息 MUST 写为 `event: message`，其 `data` MUST 是一行完整的统一事件 JSON，并在写入后立即刷新到客户端。

#### Scenario: 推送新观测消息

- **WHEN** Amadeus 已建立接收 SSE 连接，且随后 `observe` 流产生一条新的去重消息事件
- **THEN** 服务以 `event: message` 和该统一事件的 JSON `data` 立即推送给 Amadeus

#### Scenario: 发送 SSE 心跳

- **WHEN** SSE 连接在 15 秒内没有新消息事件
- **THEN** 服务向客户端写入并立即刷新一条 SSE 注释心跳 `: heartbeat`

### Requirement: 仅推送连接后的新事件

接收接口 SHALL 仅推送 SSE 连接建立后观测到的新消息。系统 MUST NOT 保存、查询、回放或补发历史消息，客户端断开期间的消息 MUST NOT 在重连后补发。

#### Scenario: 新连接不回放旧消息

- **WHEN** 一条消息在 Amadeus 建立 SSE 连接之前已被观测
- **THEN** 新建立的 SSE 连接不推送该消息

#### Scenario: 断线后不补发

- **WHEN** Amadeus 断开 SSE 连接、断线期间产生新消息，然后 Amadeus 重新连接
- **THEN** 服务不补发断线期间的消息

### Requirement: 就绪状态与不可用响应

HTTP 服务 SHALL 在启动后立即开始监听，并仅在持续观测探针可用时将微信自动化链路视为已就绪。探针未成功启动或已退出时，发送和接收接口 MUST 在建立 SSE 响应之前返回 HTTP `503`。

#### Scenario: 启动期间请求未就绪接口

- **WHEN** HTTP 端口已开始监听但持续观测探针尚未成功启动
- **THEN** `POST /v1/messages/send` 和 `GET /v1/messages/receive` 均返回 HTTP `503`

#### Scenario: 观测探针退出

- **WHEN** 持续观测探针在 HTTP 服务运行期间退出
- **THEN** HTTP 服务保持监听但将自动化链路标记为未就绪，后续发送和接收请求返回 HTTP `503`

### Requirement: 最小单机运行边界

系统 SHALL 以单机、单 HTTP 服务实例和单 Amadeus SSE 客户端为支持边界。实现 MUST NOT 增加多客户端广播协调、消息队列、消费确认、持久化、认证或健康检查接口，且 MUST 仅使用 Go 标准库和项目已有探针代码。

#### Scenario: 单客户端本机运行

- **WHEN** 一个 Amadeus 实例从同一宿主机连接 SSE 接口并调用发送接口
- **THEN** 系统在一个 HTTP 服务进程内提供两个接口，不依赖外部队列、数据库或第三方 Go 包

#### Scenario: 不提供额外 HTTP 能力

- **WHEN** 客户端请求 `/healthz` 或任何除两个消息接口外的未定义路径
- **THEN** 服务返回 HTTP `404`，且不暴露额外的业务、认证或健康检查接口

#### Scenario: 本机调用无需认证

- **WHEN** Amadeus 从宿主机回环地址调用已定义的发送或接收接口且未提供认证凭据
- **THEN** 服务按接口业务契约处理请求，不要求 API key、token 或其他认证信息
