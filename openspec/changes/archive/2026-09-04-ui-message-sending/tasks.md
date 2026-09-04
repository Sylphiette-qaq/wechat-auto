## 1. OpenSpec 与接口

- [x] 1.1 创建中文 proposal、design、spec 与 tasks，明确当前会话、stdin、剪贴板粘贴和模拟键盘边界
- [x] 1.2 定义 send 模式参数、结果 JSON、错误码和环境变量契约

## 2. Python UI 发送器

- [x] 2.1 增加唯一微信窗口查找与激活逻辑
- [x] 2.2 增加当前会话输入框候选定位、唯一性校验和 AT-SPI 聚焦验证
- [x] 2.3 增加文本 CLIPBOARD 读取、写入、恢复与生命周期管理
- [x] 2.4 增加 xdotool Ctrl+V、Enter 模拟及命令失败处理
- [x] 2.5 增加粘贴后输入框校验、发送后清空/消息回显验证和超时
- [x] 2.6 增加容器内发送文件锁，保证发送临界区串行

## 3. Go CLI 与脚本入口

- [x] 3.1 扩展 CLI 支持 `--mode send`，读取 stdin 并校验空白输入
- [x] 3.2 将发送参数传递给探针，解析 send_result 并设置退出码
- [x] 3.3 增加 `scripts/wechat.sh send`，透传 `SEND_KEY`/`SEND_TIMEOUT`

## 4. Docker 与文档

- [x] 4.1 在 Dockerfile 安装 xclip、xdotool 依赖
- [x] 4.2 更新 README、AGENTS 与 compose/脚本配置说明

## 5. 测试与实机验证

- [x] 5.1 增加 Go 参数、stdin、结果和错误路径测试
- [x] 5.2 增加 Python 发送辅助逻辑的离线单测（不依赖 gi/Xvfb）
- [x] 5.3 运行 gofmt、go test、go vet 与 Python 语法检查
- [x] 5.4 在 noVNC 中验证群聊中文发送、Enter 和发送后回显
