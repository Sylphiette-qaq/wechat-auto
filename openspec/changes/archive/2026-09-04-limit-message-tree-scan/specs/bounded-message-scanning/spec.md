## ADDED Requirements

### Requirement: watch 仅扫描最新消息窗口

`watch` SHALL 在 AT-SPI 远程访问阶段只读取当前 Messages 列表末尾固定窗口内的直接子节点，默认窗口大小为 10；窗口外历史消息不得在本轮被访问或产出事件。

#### Scenario: 历史消息很多时只访问尾部

- **WHEN** Messages 容器有 100 个直接子节点且窗口大小为 10
- **THEN** 扫描器只调用索引 90 至 99 的 `get_child_at_index()`，解析结果最多包含这 10 个节点

#### Scenario: 消息少于窗口大小

- **WHEN** Messages 容器只有 3 个直接子节点且窗口大小为 10
- **THEN** 扫描器读取索引 0 至 2，不访问负索引，也不报错

### Requirement: 局部扫描失败时性能优先

`watch` SHALL 在无法定位或读取 Messages 容器时结束当前扫描轮次并记录结构化诊断，不得为了补偿而回退到完整应用树扫描。

#### Scenario: Messages 容器不可用

- **WHEN** 当前应用树中没有可用的 Messages 列表，或远程对象访问失败
- **THEN** 本轮不产出消息事件，输出现有 `scan_state`/错误诊断，并等待下一轮

### Requirement: 诊断 dump 保持全量

`dump` SHALL 继续使用完整树遍历输出诊断快照，不受 watch 消息窗口限制。

#### Scenario: 导出完整控件树

- **WHEN** 用户执行 dump 模式
- **THEN** 探针按 `max_depth` 遍历应用树并输出所有可访问节点，而不是只输出 Messages 尾部窗口
