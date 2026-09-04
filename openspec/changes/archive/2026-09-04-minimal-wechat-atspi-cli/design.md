## Context

仓库当前只有一个空的 Go module。第一阶段的目标不是完成机器人业务，而是验证 Linux 微信 4.1.1.8 在 Docker 中是否能启动、登录，并通过 AT-SPI 读取当前前台会话中的文本消息。实现必须保持在 UI 自动化边界内，不读取微信数据库、进程内存，也不使用 Hook、ptrace、协议或 OCR。

## Goals / Non-Goals

**Goals:**

- 在 `linux/amd64` Docker 容器中运行固定的微信 4.1.1.8。
- 通过 Xvfb、D-Bus session bus 和 AT-SPI 提供可观测的图形会话。
- 通过 noVNC 完成人工登录，并将登录状态保存到 volume。
- 提供最小 Go CLI，输出 AT-SPI 控件树和当前会话文本节点。
- 将可识别的文本节点输出为 JSON Lines，并完成基础去重。
- 在无法读取正文时保留控件树、角色、名称和错误日志作为诊断证据。

**Non-Goals:**

- 不实现发送消息命令。
- 不实现 LLM、白名单、限流、冷却、审计服务或多账号。
- 不支持后台最小化监听、复杂消息类型或 OCR fallback。
- 不访问微信数据库、数据库密钥、进程内存或微信协议。

## Decisions

1. **固定本地安装包，而不是 Docker 构建时下载浮动 URL。**
   构建上下文必须提供 `artifacts/WeChatLinux_4.1.1.8_x86_64.deb`，Dockerfile 同时验证 SHA-256 和 Debian 包版本。这样可以复现构建并避免上游地址内容变化。

2. **第一阶段使用单容器。**
   微信、Xvfb、D-Bus、AT-SPI、noVNC 和 Go CLI 放在同一个容器，减少跨容器显示和 session bus 配置变量。后续验证成功后再拆分 runtime 与 agent。

3. **Go CLI 与 AT-SPI 探针通过 JSON Lines 连接。**
   CLI 负责进程生命周期、事件读取和输出；AT-SPI 访问先使用系统提供的 Python/GObject 绑定完成探针，以便快速验证控件树。业务层只依赖 JSON 事件，不依赖 AT-SPI 具体 API。

4. **事件发现采用事件优先、轮询兜底。**
   探针尝试订阅 AT-SPI children/text/focus 变化；当前会话无法稳定产生事件时，以低频控件树轮询输出新文本节点。两条路径共用内容哈希去重。

5. **第一版只观察前台当前会话的普通文本。**
   不承诺最小化或切换窗口后的后台监听，也不处理图片、语音、文件、引用、撤回和系统通知。无法识别的节点仍写入原始诊断输出。

## Risks / Trade-offs

- [AT-SPI 控件树不暴露消息正文] → 输出完整树快照、角色/name/text 读取错误和环境变量，明确报告阻塞；不切换到数据库或 OCR。
- [微信包下载地址或校验值不可用] → 构建前要求本地 artifact；缺失时构建直接失败并提示放置路径。
- [Python AT-SPI 绑定与 Go CLI 存在进程边界] → 只把 JSON Lines 作为临时探针协议，后续可替换为 Go 原生 D-Bus 实现而不改变事件模型。
- [Docker Desktop amd64 模拟性能较低] → 第一阶段只支持 amd64，并将轮询频率保持在低频以便完成可用性验证。

## Migration Plan

1. 放置并校验微信 4.1.1.8 x86_64 安装包。
2. 构建并启动单容器，使用 noVNC 手工登录。
3. 重启容器确认登录 volume 保留。
4. 在当前私聊和群聊中发送普通文本，观察 CLI JSONL 输出。
5. 如果正文不可读，保留诊断产物并停止在 AT-SPI 验证阶段。

## Open Questions

- 微信 4.1.1.8 在实际 Docker 图形环境中暴露的 accessible role/name/text 结构仍需实测。
- 是否能从 accessible 属性得到稳定的微信内部 ChatID/MessageID，需由探针结果决定；否则使用可解释的派生哈希。
