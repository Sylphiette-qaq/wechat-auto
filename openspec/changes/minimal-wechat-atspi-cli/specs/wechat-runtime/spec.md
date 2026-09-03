## ADDED Requirements

### Requirement: 固定版本微信运行环境
系统 SHALL 在 `linux/amd64` 容器中安装本地提供的 Linux 微信 4.1.1.8 Debian 包，并在安装前校验包版本与 SHA-256。

#### Scenario: 构建使用固定安装包
- **WHEN** 构建 Docker 镜像且 `artifacts/WeChatLinux_4.1.1.8_x86_64.deb` 存在
- **THEN** 构建过程校验 Debian 包版本为 4.1.1.8、校验值匹配后才继续安装

#### Scenario: 安装包缺失或校验失败
- **WHEN** 本地安装包缺失、版本不符或 SHA-256 不匹配
- **THEN** Docker 构建失败并输出明确的文件路径或校验错误

### Requirement: 图形和辅助功能会话
运行时 SHALL 启动 Xvfb、D-Bus session bus 和 at-spi2-core，并使微信进程加入同一个图形/会话环境。

#### Scenario: 容器启动
- **WHEN** 运行时容器启动
- **THEN** `DISPLAY`、`DBUS_SESSION_BUS_ADDRESS` 和 AT-SPI 总线可用，微信进程能够创建窗口

### Requirement: 人工登录和状态持久化
运行时 SHALL 通过 noVNC 暴露内部虚拟显示供人工登录，并将微信配置目录挂载到独立 volume。

#### Scenario: 首次登录
- **WHEN** 用户通过 noVNC 访问虚拟显示并完成微信登录
- **THEN** 微信保持运行且登录配置写入挂载目录

#### Scenario: 容器重启
- **WHEN** 容器停止后使用同一配置 volume 重启
- **THEN** 运行时保留微信登录状态或至少保留微信生成的配置文件

### Requirement: 运行诊断输出
运行时 SHALL 将启动日志、AT-SPI 探针输出和错误信息写入挂载的日志目录。

#### Scenario: AT-SPI 不可用
- **WHEN** 探针无法发现微信应用或无法读取文本接口
- **THEN** 日志包含环境信息、窗口/控件树快照和失败原因
