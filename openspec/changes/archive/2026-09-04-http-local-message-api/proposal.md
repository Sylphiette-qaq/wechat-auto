## Why

Amadeus 需要通过本机 HTTP 调用现有微信 UI 自动化能力，而当前程序只有 CLI/JSONL 入口，无法直接建立稳定的发送和接收连接。现在补充一个仅绑定本机回环地址的最小 HTTP 模式，将已有发送链路和观测事件暴露给单机上的 Amadeus 使用。

## What Changes

- 新增 Go 标准库 `net/http` HTTP 运行模式，并保留现有 `probe`、`observe`、`send` 模式。
- 新增 `POST /v1/messages/send`，接收 JSON 文本并发送到当前已打开的微信会话。
- 新增 `GET /v1/messages/receive`，以 SSE 推送观测到的新消息事件。
- HTTP 服务仅通过 Docker 将容器端口映射到宿主机 `127.0.0.1:8090`。
- Docker 默认启动 HTTP 模式；不增加数据库、消息队列、认证、历史回放或健康检查接口。
- 增加接口、错误状态码、运行配置和最小单元测试文档。

## Capabilities

### New Capabilities

- `local-http-message-api`: 为本机 Amadeus 提供最小的消息发送 HTTP 接口和 SSE 接收接口。

### Modified Capabilities

无。现有 AT-SPI 观测和 UI 发送的业务边界不变，仅增加 HTTP 传输适配层。

## Impact

- 修改 `cmd/wechat-cli/main.go` 或新增同包 HTTP 服务代码，编排长期观测和发送请求。
- 修改 `docker/entrypoint.sh`、`docker-compose.yml`，新增 HTTP 启动参数和 `127.0.0.1:8090` 端口映射。
- 修改 `README.md`、`AGENTS.md` 或运维脚本，补充接口文档和调用示例。
- 增加 Go HTTP handler、SSE、请求校验、未就绪和错误映射测试。
- 继续只使用 Go 标准库以及现有 Python AT-SPI/UI 探针，不引入第三方依赖。
