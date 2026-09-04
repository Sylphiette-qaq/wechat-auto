## ADDED Requirements

### Requirement: 当前会话文本发送

系统 SHALL 只向当前已经打开且可确认的微信群聊或私聊发送一条普通文本消息，不得搜索或切换会话。

#### Scenario: 成功发送当前会话文本

- **WHEN** 用户已登录微信并打开一个群聊或私聊，且输入框可以被唯一定位和聚焦
- **THEN** 系统把 stdin 中的一段文本发送到该当前会话，并输出一条 `send_result` JSON 记录

#### Scenario: 无法确认当前会话

- **WHEN** 微信窗口、当前会话标题或唯一输入框无法确认
- **THEN** 系统不得粘贴或发送，并返回结构化错误和非零退出码

### Requirement: stdin 文本输入

系统 SHALL 从 stdin 读取一次发送正文，保留原始字节内容和换行，不按行拆分；零长度或纯空白输入 MUST 被拒绝。

#### Scenario: 多行文本作为一条消息

- **WHEN** stdin 包含带换行的非空白文本
- **THEN** 系统将全部内容作为一条消息粘贴和发送

#### Scenario: 空白输入拒绝

- **WHEN** stdin 为空或只包含空格、制表符和换行
- **THEN** 系统不操作微信，返回 `invalid_input` 和非零退出码

### Requirement: 输入框锁定与粘贴校验

系统 SHALL 激活唯一微信窗口，唯一定位消息输入框，设置并验证焦点后，使用 X11 `CLIPBOARD` 粘贴正文；粘贴后的可见输入内容不等于原文时 MUST 停止发送。

#### Scenario: 输入框获得焦点后粘贴

- **WHEN** 唯一输入框可被 AT-SPI 定位并报告 focused
- **THEN** 系统通过剪贴板写入正文、模拟 `Ctrl+V`，并验证输入框内容与原文一致

#### Scenario: 输入框候选不唯一

- **WHEN** 可编辑候选为零个或多个
- **THEN** 系统返回 `input_not_found` 或 `input_ambiguous`，不发送任何键盘事件

### Requirement: 模拟键盘发送

系统 SHALL 使用 `xdotool` 模拟键盘完成粘贴和发送；发送快捷键 SHALL 固定为已验证可行的 `Enter`。

#### Scenario: 默认 Enter 发送

- **WHEN** 粘贴校验成功且发送快捷键配置为默认值
- **THEN** 系统只模拟一次 `Enter` 并进入发送结果验证

### Requirement: 发送结果验证

系统 SHALL 在发送后等待输入框清空，并尽量观察当前 Messages 列表出现相同正文；超时或无法确认时 MUST 返回非零退出码且不得自动重试。

#### Scenario: 输入框清空并观察到回显

- **WHEN** 发送快捷键执行后输入框清空且 Messages 列表出现相同正文
- **THEN** 系统返回 `accepted=true`、`verified=true` 以及增强验证级别

#### Scenario: 仅确认输入框清空

- **WHEN** 发送快捷键执行后输入框清空但 Messages 列表暂时无法读取
- **THEN** 系统可返回最低成功证据，并在结果中标记回显未确认

#### Scenario: 发送超时

- **WHEN** 在默认或配置的超时时间内输入框未清空或结果无法确认
- **THEN** 系统返回 `send_timeout` 或 `send_result_unverified`，不再次发送

### Requirement: 发送串行化与剪贴板恢复

系统 SHALL 在容器内串行执行发送临界区，并在完成后尽力恢复原文本剪贴板内容。

#### Scenario: 并发发送请求

- **WHEN** 已有另一个发送流程持有发送锁
- **THEN** 新请求不得操作剪贴板或键盘，并返回 `send_busy`

#### Scenario: 恢复文本剪贴板

- **WHEN** 发送流程结束，无论成功或失败
- **THEN** 系统尝试恢复发送前读取到的文本 `CLIPBOARD`，恢复失败只写诊断

### Requirement: 结构化结果与技术边界

系统 SHALL 在 stdout 输出一条结构化 `send_result` JSON，在 stderr 输出诊断，并在失败时返回非零退出码；发送流程 MUST 仅使用 UI、AT-SPI、X11 剪贴板和键盘事件，不得读取微信数据库、进程内存、协议或使用逆向、Hook、ptrace、OCR。

#### Scenario: 成功结果

- **WHEN** 文本已粘贴且发送结果达到最低成功证据
- **THEN** stdout 包含会话信息、文本长度、发送时间、接受状态和验证级别，且不打印正文

#### Scenario: 技术边界审计

- **WHEN** 执行一次 send 流程
- **THEN** 所有操作均可追溯到 AT-SPI/X11 UI 边界，诊断中不得出现数据库、内存读取或替代技术路径
