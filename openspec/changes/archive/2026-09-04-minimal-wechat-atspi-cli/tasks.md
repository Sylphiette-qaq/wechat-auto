## 1. Docker 运行环境

- [x] 1.1 添加 `artifacts/README.md` 和 `.gitignore`，约定本地微信 4.1.1.8 x86_64 安装包路径与 SHA-256 校验方式
- [x] 1.2 编写固定 `linux/amd64` 基础镜像的 Dockerfile，安装微信、Xvfb、D-Bus、at-spi2-core、Python AT-SPI 绑定和 noVNC 依赖
- [x] 1.3 编写容器入口脚本，启动 Xvfb、D-Bus session bus、AT-SPI、x11vnc、noVNC 和 CLI
- [x] 1.4 编写 Compose 配置，挂载微信登录状态、日志和诊断输出 volume，并仅暴露 noVNC 端口

## 2. Go 最小 CLI

- [x] 2.1 建立统一 `Event`、`ChatType`、`ChatTarget` 和监听接口的数据模型
- [x] 2.2 实现 CLI 参数解析，支持控件树探针模式和 JSONL 消息观测模式
- [x] 2.3 实现 AT-SPI 探针子进程管理，将探针 JSONL 转发为标准输出并保留退出错误
- [x] 2.4 实现基于会话、发送者和文本的派生事件 ID 与内存去重
- [x] 2.5 为事件模型、去重逻辑和 JSONL 解析补充单元测试

## 3. AT-SPI 探针

- [x] 3.1 编写 Python/GObject 探针，连接桌面 accessible tree 并查找微信应用窗口
- [x] 3.2 实现递归输出窗口、聊天列表、消息列表、输入框的 role/name/text
- [x] 3.3 尝试订阅 children-changed、text-changed 和 focus 事件，并输出原始诊断记录
- [x] 3.4 实现当前前台会话的低频控件树轮询，发现新增普通文本节点
- [x] 3.5 为无法发现微信窗口、无法访问文本接口和正文为空的情况输出可定位日志

## 4. 验证

- [x] 4.1 运行 `gofmt`、`go test ./...` 和 `go vet ./...`
- [x] 4.2 使用本地安装包构建 Docker 镜像并验证版本、架构和 SHA-256 检查失败路径
- [x] 4.3 通过 noVNC 完成首次登录，确认登录状态 volume 写入
- [x] 4.4 重启容器确认登录状态和日志 volume 保留
- [x] 4.5 在前台私聊和群聊中发送普通文本，记录 CLI 是否能输出正文、聊天名和发送者
- [x] 4.6 若正文无法通过 AT-SPI 读取，保存控件树快照和诊断日志，标记 AT-SPI 阻塞，不引入数据库或 OCR
