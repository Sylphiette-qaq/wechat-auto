## Context

当前 `wechat-cli` 仅提供 `probe`、`observe`、`send` 三种命令行模式：`observe` 长期运行 Python `watch` 探针，将 JSONL 记录转换为统一 `wechatmodel.Event` 并在进程内去重；`send` 则为每次调用启动一次 Python `send` 探针，通过现有 AT-SPI/X11 链路向当前已打开会话同步发送文本。Amadeus 与该程序运行在同一台宿主机，需要一个无需额外组件的本机 HTTP 入口复用这两条链路。

本变更跨越 CLI 生命周期、HTTP 传输和 Docker 端口配置，但不改变微信自动化边界。实现仍须只使用 AT-SPI/X11 UI，不得读取数据库、进程内存或协议，也不得引入 OCR。HTTP 层只使用 Go 标准库；运行模型固定为单机、单容器、单个 Amadeus 客户端。

目标数据流如下：

```text
Amadeus (127.0.0.1)
        │
        │ POST /v1/messages/send
        │ GET  /v1/messages/receive (SSE)
        ▼
wechat-cli --mode http (:8090)
        ├── 一次性 Python send 探针 ──AT-SPI/X11──> 当前微信会话
        └── 长期 Python watch 探针 ──AT-SPI──────> Event 去重 ──> SSE
```

## Goals / Non-Goals

**Goals:**

- 新增 `--mode http`，使用 Go 标准库 `net/http` 同时承载同步发送和持续接收。
- 提供 `POST /v1/messages/send`，仅接受 JSON `{"text":"..."}`，发送目标固定为当前已打开的微信会话。
- 提供 `GET /v1/messages/receive`，通过 SSE 实时输出现有统一消息事件。
- 容器内监听 `0.0.0.0:8090`，Docker 只映射到宿主机 `127.0.0.1:8090`，供本机 Amadeus 调用。
- Docker 无显式参数时默认启动 HTTP 模式，同时保留并兼容原有 `probe`、`observe`、`send` 模式和运维命令。
- 为请求校验、发送结果状态码映射和 SSE 输出增加最小的 Go 单元测试，不引入第三方依赖。

**Non-Goals:**

- 不支持指定、搜索或切换目标会话。
- 不支持多机、多实例、多 Amadeus 客户端或多 SSE 订阅者广播。
- 不实现发送排队、自动重试、持久化消息队列、消费确认、历史回放或断线补发。
- 不增加认证、TLS、跨域访问、限流或健康检查接口。
- 不改变消息解析、派生 ID、去重规则、AT-SPI 读取范围或 X11 发送动作。
- 不增加第三方 Go 模块、Redis、数据库或新的后台服务。

## Decisions

### 1. 在现有 CLI 中新增单进程 HTTP 模式

`wechat-cli --mode http` 负责启动一个长期 `watch` 探针、消费并去重统一事件，同时运行 `net/http` 服务。发送请求继续按次启动现有 `send` 探针。HTTP 层是传输适配器，不把 HTTP 类型引入 `internal/wechatmodel`，也不在 Python 探针中实现 Web 服务。

HTTP 模式增加独立监听参数，默认值为 `0.0.0.0:8090`。探针可执行文件、基础参数、发送键和发送超时继续复用现有 CLI 参数及环境变量。

备选方案是新增单独的 HTTP 二进制或 Python HTTP 服务；这会复制探针编排、JSON 解析和事件去重逻辑，增加进程与部署复杂度，因此不采用。

### 2. 复用 observe 流作为 SSE 的唯一事件源

HTTP 模式启动后台 `watch` 子进程，并沿用 `probe.ParseRecord`、`Record.Event()` 和 `wechatmodel.Deduper`。去重后的 `wechatmodel.Event` 通过一个进程内通道交给当前 SSE 响应；该通道只用于同一进程内 goroutine 交接，不提供持久化、消费确认或历史缓存。

新 SSE 连接只接收连接建立后产生的事件。没有客户端连接时事件直接丢弃；客户端断开期间产生的事件不补发。为符合单客户端运行假设，同一时刻只允许一个接收连接，额外连接返回冲突错误，不实现广播。

备选方案是让每个 HTTP 客户端各自启动一个 `watch` 探针，或增加共享队列/订阅中心；前者会重复扫描 AT-SPI 树，后者超出单客户端范围，因此不采用。

### 3. 接收接口使用标准 SSE 帧

`GET /v1/messages/receive` 在后台观测探针处于运行状态时返回 `200`，设置 `Content-Type: text/event-stream`、禁用响应缓存，并将每个统一事件编码为：

```text
event: message
data: {统一 Event JSON}

```

每条事件写入后立即调用 `http.Flusher.Flush()`。连接空闲时每 15 秒输出一次 SSE 注释心跳 `: heartbeat`，只用于保持本机长连接，不产生业务事件。客户端请求取消或连接关闭后立即结束 handler 并释放唯一订阅位置。

备选方案是普通轮询或长轮询 JSON 接口；它们需要额外定义超时、游标和重复消费语义，与现有持续事件流不匹配，因此采用 SSE。

### 4. 发送接口同步复用现有 send 探针

`POST /v1/messages/send` 只接受 `Content-Type: application/json`，请求对象只包含字符串字段 `text`。HTTP 层使用有界请求体（最大 1 MiB）和 `encoding/json` 解码，拒绝空文本、纯空白文本、缺失字段、未知字段、多余 JSON 值及不支持的方法。合法文本保持 JSON 字符串解码后的原始内容，包括正文内换行，并通过 stdin 原样交给一次性 `send` 探针。

handler 同步等待探针完成，解析现有单条 `send_result` JSON，并将该对象作为 JSON 响应返回；不重新定义第二套发送结果模型。目标会话仍由微信当前 UI 状态决定，HTTP 请求不接受 `chat_id` 或 `chat_name`。

本变更不在 HTTP 层增加发送队列或额外并发控制。部署约定只有一个调用方；现有 Python 文件锁继续作为不同 CLI 进程意外重叠时的最终保护。

备选方案是将文本放入后台发送队列后立即返回 `202`；这会改变现有同步验证语义，并引入排队、结果查询和重试问题，因此不采用。

### 5. 使用 HTTP 状态码映射现有发送失败类型

HTTP 响应始终使用 JSON。发送结果按以下最小规则映射：

| 状态码 | 场景 |
|---|---|
| `200` | 探针返回 `accepted: true` |
| `400` | 方法、Content-Type、请求体、JSON 或 `text` 校验失败 |
| `409` | 现有探针返回 `send_busy` |
| `503` | 后台观测探针未运行，或微信/AT-SPI 当前不可用 |
| `504` | 现有探针返回 `send_timeout` |
| `502` | 其他探针启动、协议解析或发送执行失败 |

对于已生成 `send_result` 的失败，响应体保留其 `accepted`、`verified`、`error_code`、`error` 等字段。HTTP 层自身产生的错误也使用相同的 `accepted: false`、`verified: false`、`error_code`、`error` 形状，便于 Amadeus 统一处理。

备选方案是所有业务失败均返回 `200`；这会使连接级、输入级和微信链路级失败难以区分，因此不采用。

### 6. 就绪状态只反映长期观测探针生命周期

HTTP socket 启动后即可接受连接，但只有后台 `watch` 探针成功启动且仍在运行时才标记为就绪。就绪前，两个业务接口均返回 `503`；探针退出后清除就绪状态，已建立的 SSE 连接结束，后续请求继续返回 `503`。HTTP 进程不在内部自动重启探针，以免形成隐藏的重试循环。

该就绪标记不新增 `/healthz`，也不承诺微信已经登录或当前会话一定可发送；这些 UI 条件仍由实际 `send_result` 明确报告并映射为 HTTP 错误。进程终止时取消探针上下文并关闭 HTTP 服务，避免遗留子进程。

备选方案是在收到第一条消息后才标记就绪；无新消息时可能永远无法提供接口，因此不采用。

### 7. Docker 只向本机回环地址发布端口

容器内服务监听 `0.0.0.0:8090`，以便 Docker 端口转发能够连接；`docker-compose.yml` 使用 `127.0.0.1:8090:8090`，确保局域网其他主机不能直接访问。Amadeus 固定调用 `http://127.0.0.1:8090`。

`docker/entrypoint.sh` 无显式参数时默认执行 HTTP 模式，并为后台观测提供现有脚本路径和轮询参数。显式传入参数时保持当前行为，因此 `scripts/wechat.sh tree`、`watch`、`send` 仍可通过 `docker compose exec` 使用原模式诊断。

备选方案是将宿主机端口绑定到 `0.0.0.0`；第一版没有认证或 TLS，这会无意暴露微信发送能力，因此不采用。

## Risks / Trade-offs

- [长期观测探针退出后 HTTP 进程仍存活但不可用] → 清除就绪状态、结束 SSE 并统一返回 `503`；通过现有容器日志诊断，不在第一版增加自动重启或健康接口。
- [单个 SSE 客户端断线期间丢失消息] → 明确实时、无回放契约；Amadeus 负责保持连接，未来确有可靠投递需求时再单独设计持久化游标。
- [HTTP 请求与人工操作微信 UI 发生竞争] → 保持现有“当前已打开会话”前置条件，发送失败直接返回探针错误，不搜索会话、不自动重试。
- [发送时长占用 HTTP handler] → 接口刻意采用同步语义，并受现有 `SEND_TIMEOUT` 限制；第一版不引入异步任务模型。
- [未认证接口具有发送能力] → Docker 只发布到宿主机 `127.0.0.1`；不得改成全网卡绑定，且不宣称该边界可替代多用户环境中的认证。
- [SSE 消费速度影响实时观测] → 单客户端、本机通信是已确认前提；第一版不增加复杂背压、磁盘缓冲或消息队列。
- [HTTP 模式重构现有 run 流程导致 CLI 回归] → 将探针事件消费和发送结果解析提取为可复用内部函数，保留原模式的 stdout、stderr、退出码和参数行为，并运行现有测试。

## Migration Plan

1. 在 Go CLI 中加入 HTTP 模式和标准库 handler，复用现有 observe/send 探针适配逻辑，并补充最小测试。
2. 修改容器默认参数为 HTTP 模式，在 Compose 中增加 `127.0.0.1:8090:8090`，保留 noVNC `127.0.0.1:6080:6080`。
3. 更新接口文档，使用本机 `curl` 验证同步发送和 SSE 接收，再由 Amadeus 连接 `127.0.0.1:8090`。
4. 运行 `gofmt`、`go test ./...`、`go build ./...` 和 `go vet ./...`，并在真实 Docker 图形环境验证登录后的收发链路。
5. 回滚时恢复 entrypoint 默认 `observe` 模式并移除 `8090` 端口映射；原有 `probe`、`observe`、`send` 模式和数据卷不受影响，无数据迁移步骤。

## Open Questions

无。监听地址、接口路径、SSE 语义、请求格式、状态码、单客户端边界和 Docker 默认启动方式均已确认。
