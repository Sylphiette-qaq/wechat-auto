# AGENTS.md — 本项目给 AI 助手的快速上手指南

> 本文件是给 AI 编码助手/代理的速览入口。人类可读的详细文档见 `README.md` 与 `openspec/`。

## 1. 项目是什么

这是 **Linux Docker 微信 AT-SPI 最小 CLI**（第一阶段验证工程）。目标不是完成机器人业务，而是验证：固定版本的 Linux 微信 4.1.1.8 能否在 Docker 图形环境中启动、登录，并通过 **AT-SPI 无障碍树**读取当前前台会话的文本/图片消息，最终以 **JSON Lines** 输出统一事件。

- 固定微信版本：**4.1.1.8**（`linux/amd64`）
- 通过 AT-SPI 只读观测，并通过 X11 UI 自动化发送文本消息；不调用微信内部接口
- 严格技术边界：只允许 AT-SPI/X11 UI，禁止数据库 / 进程内存 / Hook / ptrace / 协议模拟 / OCR

## 2. 技术栈

| 层 | 技术 |
|---|---|
| CLI | Go 1.25（module `wechat-auto`，无第三方依赖） |
| AT-SPI 探针 | Python 3 + PyGObject（`gi.repository Atspi/GLib`） |
| 运行环境 | Docker + docker compose，Ubuntu 22.04 基础镜像 |
| 图形/无障碍 | Xvfb、D-Bus session bus、at-spi2-core、x11vnc、noVNC |
| 文档/规格 | OpenSpec（spec-driven 变更管理，见 `openspec/`） |

## 3. 目录结构

```
cmd/wechat-cli/main.go        Go CLI 入口：解析参数、拉起探针、转发/归一化/去重、HTTP 服务
internal/probe/jsonl.go       解析探针输出的 JSONL Record，转换为统一 Event
internal/wechatmodel/         Event/ChatType/MessageType 数据模型 + 派生 ID + 去重
scripts/atspi_probe.py        Python AT-SPI 探针：dump 控件树 / watch 消息
scripts/wechat.sh             运维入口封装（tree/watch/logs/shell/rebuild…）
docker/Dockerfile             构建镜像（Go 编译 + 安装微信 + 图形依赖）
docker/entrypoint.sh          容器启动：拉起 Xvfb/D-Bus/AT-SPI/x11vnc/noVNC/微信/CLI
docker-compose.yml            单容器编排 + 状态/日志 volume
artifacts/                    本地微信 .deb 构建输入（不提交 Git，见 artifacts/README.md）
openspec/                     变更提案、设计、任务与规格（能力定义）
```

## 4. 架构与数据流

```
微信(Qt UI) ──AT-SPI──> D-Bus accessibility bus
                              │
                              ▼
              scripts/atspi_probe.py（dump / watch）
                              │  stdout: JSON Lines（kind=node|message）
                              ▼
              cmd/wechat-cli（--mode probe|observe）
                              │  probe: 原样转发
                              │  observe: ParseRecord → Event → 去重 → JSONL
                              │  http: 后台观测 + 本机 HTTP 发送/SSE 接收
                              ▼
                         stdout: 统一事件 JSONL
```

关键点：

- **CLI 与探针通过 JSON Lines 连接**：CLI 只负责进程生命周期 + 事件流读取 + 归一化 + 去重；AT-SPI 的具体访问在 Python 探针内完成。业务层只依赖 JSON，不依赖 AT-SPI API。
- **事件优先、轮询兜底**：探针订阅 `children-changed` / `text-changed` / `focus` 等事件，同时以低频轮询（默认 1s）扫描控件树，两条路径共用内容哈希去重。
- **四种模式**：
  - `--mode probe`：直接把探针 stdout 转发（配合探针 `dump` 导出控件树）。
  - `--mode observe`：消费探针已生成的完整消息事件，经 `ParseRecord` → `Event()`（仅解码与字段映射）→ `Deduper` 去重后输出。
  - `--mode http`：在后台观测消息并启动 `net/http` 服务；默认监听 `0.0.0.0:8090`，提供 `/v1/messages/send` 与 `/v1/messages/receive`。

### 统一事件字段（`internal/wechatmodel/event.go`）

`account_id`、`chat_id`、`chat_name`、`chat_type`（`direct|group`）、`sender_id/sender_name`（可空）、`message_id`、`text`、`message_type`（`text|image`）、`is_mention`、`created_at`、`raw`。

### 派生 ID 规则（`internal/wechatmodel/dedup.go`）

- 探针未提供原生 `MessageID` 时，由 Python 探针用 `derive_message_id(chat_name, sender_name, text, time_block)` 做 SHA-256 生成 `derived-time-*` ID。
- 未提供 `ChatID` 时，由 Python 探针用 `derive_chat_id(account_id, chat_name)` 生成 `derived-chat-*`；Go 只消费完整身份字段。
- 去重键 = `account_id + chat_id + message_id`（进程内内存去重）。

## 5. 常用命令

### 构建 / 测试（在宿主机）

```bash
go build ./...
go test ./...
go vet ./...
gofmt -l .
```

> 无第三方依赖，`go.mod` 仅声明 Go 版本；测试覆盖 `internal/probe` 与 `internal/wechatmodel`。

### 启动运行环境（需要 Docker）

```bash
# 1. 先放置微信安装包并校验（见 artifacts/README.md）
docker compose build
docker compose up -d
# 2. 浏览器打开 http://127.0.0.1:6080/vnc.html 完成人工登录
```

### 运维快捷命令（`scripts/wechat.sh`）

```bash
./scripts/wechat.sh tree      # 输出当前控件树（Go 转发）
./scripts/wechat.sh watch     # 持续输出去重后的消息事件 JSONL
./scripts/wechat.sh logs      # 查看 Runtime 日志
./scripts/wechat.sh status    # 容器状态
./scripts/wechat.sh shell     # 进入容器
./scripts/wechat.sh rebuild   # 重建镜像并重建容器
./scripts/wechat.sh help      # 帮助
```

HTTP 服务地址：`http://127.0.0.1:8090`（仅本机）；环境变量：`POLL_INTERVAL`（watch 轮询秒数，默认 1）、`MAX_DEPTH`（控件树深度，默认 60）、`COMPOSE_FILE`。

### 底层完整命令（仅排查环境问题时用）

```bash
docker compose exec wechat-runtime \
  /app/wechat-cli --mode probe \
  --probe python3 \
  --probe-arg /app/scripts/atspi_probe.py \
  --probe-arg dump \
  > /data/diagnostics/tree.jsonl
```

## 6. 开发约定

- **CLI 参数**：`--probe-arg` 可重复指定，传递给探针；`--mode` 支持 `probe|observe|send|http`，HTTP 模式通过 `--http-addr` 指定监听地址，非法值以退出码 2 报错。
- **探针输出契约**：stdout 只输出业务 JSONL；诊断日志一律写 stderr（`Diagnostics` 类，按错误类型去重抑制重复告警）。Go 侧 `scanner.Buffer` 上限设为 4MB 以容纳长文本节点。
- **JSON 标签**：统一事件字段使用 snake_case；可选字段加 `omitempty`。
- **错误处理**：探针侧所有 AT-SPI 远程调用都用 `safe_*` 包装并捕获异常（远程对象可能过期）；CLI 侧用 `fmt.Errorf` 包装错误并保留退出码。
- **测试命名**：`xxx_test.go` 与实现同目录，覆盖派生 ID、去重、Record 解析等纯逻辑。
- **规格驱动**：新能力先写 `openspec/changes/<name>/`（proposal/design/tasks/specs），变更走 OpenSpec 流程。

## 7. 硬性约束（改动时必须遵守）

1. **仅 AT-SPI 读消息**：不得读取微信数据库、数据库密钥、进程内存；不得使用 Hook、ptrace、协议模拟或 OCR。
2. **固定版本**：微信 4.1.1.8 + `linux/amd64`，构建前校验架构、版本、SHA-256（值见 `docker-compose.yml` / `artifacts/README.md`）。安装包属外部输入，**不提交 Git**（`.gitignore` 已排除 `*.deb`）。
3. **第一版范围**：观测前台当前会话的普通文本/图片，并支持向当前打开会话发送一条普通文本；不做后台最小化监听、LLM、多账号、复杂消息类型或会话搜索切换。
4. **正文读不到时的行为**：只保留控件树快照与诊断日志作为阻塞证据，**不切换到数据库或 OCR**。

## 8. 当前状态（来自 `openspec/.../tasks.md`）

已完成：Docker 环境、Go CLI、探针、事件模型与去重、群聊/@消息读取、UI 发送链路代码、本机 HTTP 发送/SSE 接收接口、单测与构建校验。
待验证（需真实 Docker 图形环境）：noVNC 首次登录、重启后登录态保留、私聊/群聊文本读取、xclip/xdotool 粘贴发送及发送后回显。
