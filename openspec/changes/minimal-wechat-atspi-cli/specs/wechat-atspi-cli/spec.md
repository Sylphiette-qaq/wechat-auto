## ADDED Requirements

### Requirement: 控件树探测
CLI SHALL 能发现微信 accessible application，并输出窗口、聊天列表、消息候选节点和输入框的 role、name、text（若可读）。

#### Scenario: 输出微信控件树
- **WHEN** 微信已登录且 CLI 运行探针命令
- **THEN** 标准输出或日志中包含微信窗口及其可遍历的 accessible 节点信息

### Requirement: 当前会话文本观测
CLI SHALL 在前台当前私聊或群聊会话中观测新增普通文本节点，并以 JSON Lines 输出统一事件字段。

#### Scenario: 发现私聊文本
- **WHEN** 当前会话为私聊且出现新的普通文本消息
- **THEN** CLI 输出包含聊天名称、文本、创建时间和派生身份字段的 JSON 事件

#### Scenario: 发现群聊文本
- **WHEN** 当前会话为群聊且出现新的普通文本消息
- **THEN** CLI 输出包含群聊名称、文本、发送者（若 accessible 可读）和创建时间的 JSON 事件

### Requirement: 事件去重
CLI SHALL 对同一会话中重复观察到的文本节点执行去重，避免事件流重复输出。

#### Scenario: 轮询重复节点
- **WHEN** 连续两次控件树轮询返回相同的会话、发送者和文本节点
- **THEN** CLI 只输出一次该节点对应的事件

### Requirement: 受限技术边界
CLI MUST 仅使用 AT-SPI 控件树和其事件/轮询接口获取消息，不得读取微信数据库、数据库密钥、微信进程内存，也不得使用 Hook、ptrace、协议或 OCR。

#### Scenario: 消息读取路径审计
- **WHEN** 执行 CLI 的消息观测流程
- **THEN** 所有消息字段来源均可追溯到 AT-SPI accessible 节点或其事件数据
