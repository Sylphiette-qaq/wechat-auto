## 1. OpenSpec 与扫描器

- [x] 1.1 新增局部 watch 扫描器，保留 Chats/标题上下文，仅读取 Messages 尾部窗口
- [x] 1.2 增加 `--message-window` 参数与正整数校验，默认 10，并接入 watch
- [x] 1.3 局部定位失败时输出诊断且不回退全量扫描

## 2. 测试与文档

- [x] 2.1 添加 fake AT-SPI 节点测试，验证只访问最后 N 个子节点及小列表边界
- [x] 2.2 更新 README 与 scripts/wechat.sh 的窗口配置说明
- [x] 2.3 运行 Python 编译/单测、Go 测试、构建与静态检查
