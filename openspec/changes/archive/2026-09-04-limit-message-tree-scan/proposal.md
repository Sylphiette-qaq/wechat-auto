## Why

`watch` 当前每秒从微信应用根节点完整遍历 AT-SPI 控件树。随着消息历史增长，Messages 列表中的远程节点访问越来越多，轮询开销升高，而机器人只需要及时处理最新消息。性能优先时，窗口外的历史消息可以明确放弃。

## What Changes

- `watch` 采用固定大小的消息窗口，只读取 Messages 容器末尾的最新 10 个直接子节点。
- 局部扫描在 AT-SPI 远程子节点访问阶段截断，不再先完成全量树遍历后再切片。
- 当前会话标题和 Chats 会话上下文继续保留，确保群聊判定、提及识别和最新消息发送者绑定不变。
- 若局部定位或读取 Messages 容器失败，本轮记录诊断并忽略，不执行昂贵的全量回退扫描。
- `dump` 模式继续输出完整控件树，作为诊断能力不变。

## Capabilities

### New Capabilities

- `bounded-message-scanning`: watch 仅扫描 Messages 列表最新固定窗口，并以性能优先策略处理局部树访问失败。

### Modified Capabilities

- 无

## Impact

- 修改 `scripts/atspi_probe.py` 的 watch 扫描路径、命令行参数和诊断字段。
- 新增局部扫描的离线单元测试，使用 fake AT-SPI 节点验证远程访问数量和窗口边界。
- 更新 README、运维脚本和容器环境说明；不改变 HTTP 接口格式或 Go 事件模型。
