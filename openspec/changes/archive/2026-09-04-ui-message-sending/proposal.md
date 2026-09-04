## Why

当前 CLI 已能通过 AT-SPI 读取当前打开会话中的群聊消息和 @ 消息，但还不能把自动化生成的文本发送回微信。需要补齐一个严格限定在 UI 自动化边界内的发送能力：由用户登录并打开目标会话，程序锁定输入框后通过剪贴板粘贴和模拟键盘快捷键完成一次文本发送。

## What Changes

- 新增一次性文本发送能力，支持当前已打开的群聊或私聊，不搜索、不切换会话。
- 新增 `wechat-cli --mode send`，从 stdin 原样读取一条消息；`scripts/wechat.sh` 增加 `send` 入口。
- 通过 AT-SPI 识别并唯一定位当前微信窗口和消息输入框，聚焦后验证 `focused` 状态。
- 通过 `xclip` 写入 X11 `CLIPBOARD`，通过 `xdotool` 模拟 `Ctrl+V` 与发送快捷键。
- 发送快捷键固定使用已验证可行的 `Enter`；只执行一次，不自动重试。
- 增加粘贴前文本校验、发送后输入框清空/消息回显验证、超时和串行发送保护。
- 发送结束后尽力恢复原文本剪贴板内容，并输出一条结构化 JSON 结果；失败返回非零退出码并写诊断日志。
- Docker 镜像增加发送所需的 X11 键盘和剪贴板运行时依赖。

## Capabilities

### New Capabilities

- `ui-message-sending`: 通过当前微信 UI 的 AT-SPI 定位、剪贴板粘贴和模拟键盘快捷键发送一条文本消息。

### Modified Capabilities

无。现有群聊读取能力保持不变；发送作为独立的一次性能力加入。

## Impact

- 修改 `cmd/wechat-cli/main.go`，增加 send 模式、stdin 读取、参数校验、进程锁和 JSON 结果。
- 修改 `scripts/atspi_probe.py`，增加 send 子流程和发送后验证。
- 可能新增纯解析/发送辅助模块及单元测试。
- 修改 `scripts/wechat.sh`、`docker/Dockerfile`、`README.md` 和运行配置。
- 仍只使用 X11/AT-SPI UI 自动化；不读取微信数据库、进程内存，不使用逆向、Hook、ptrace、协议模拟或 OCR。
