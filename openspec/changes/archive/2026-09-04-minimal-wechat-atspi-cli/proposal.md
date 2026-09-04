## Why

当前仓库只有最小 Go 模块，尚未验证 Linux 微信 4.1.1.8 在 Docker 图形环境中是否能够通过 AT-SPI 读取聊天消息。需要先建立一个可复现、范围受控的最小 CLI，用于完成登录并观测私聊/群聊文本消息，为后续机器人业务层提供事实依据。

## What Changes

- 固定使用 `linux/amd64` 架构的 Linux 微信 4.1.1.8 安装包，并在 Docker 构建时校验版本与 SHA-256。
- 新增单容器运行环境，包含微信、Xvfb、D-Bus session bus、AT-SPI 和 noVNC。
- 新增最小 Go CLI，用于启动 AT-SPI 探针并以 JSON Lines 输出可观察到的控件树和消息候选节点。
- 新增私聊/群聊文本消息的最小事件归一化结构与去重逻辑。
- 支持登录状态、运行日志和探针诊断输出通过 Docker volume 持久化。
- 本次不实现发送命令、LLM、数据库读取、内存读取、Hook、ptrace、协议模拟、OCR 或完整风控策略。

## Capabilities

### New Capabilities

- `wechat-runtime`: 提供固定版本微信、虚拟显示、D-Bus、AT-SPI 和 noVNC 运行环境。
- `wechat-atspi-cli`: 提供登录后控件树探测、当前会话文本消息观测、事件 JSONL 输出和基础去重。

### Modified Capabilities

无。

## Impact

- 新增 Dockerfile、Compose 配置、运行入口脚本和 AT-SPI 探针脚本。
- 新增 Go CLI、统一事件模型及相关单元测试。
- 构建依赖本地 `artifacts/WeChatLinux_4.1.1.8_x86_64.deb` 文件；该安装包不提交 Git。
- 需要 Docker Desktop 或 Linux Docker 环境支持 amd64 容器、Xvfb 和 noVNC 访问。
- 由于 AT-SPI 控件树结构尚未在本环境实测，消息字段提取能力以探针诊断结果为准；无法读取正文时只输出阻塞证据，不切换到数据库或 OCR 方案。
