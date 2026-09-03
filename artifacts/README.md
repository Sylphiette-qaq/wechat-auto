# 微信安装包构建输入

将固定版本的 Linux 微信安装包放置在本目录，文件名必须为：

```text
WeChatLinux_4.1.1.8_x86_64.deb
```

Docker 构建会校验包架构 `amd64`、版本 `4.1.1.8` 和 SHA-256：

```text
c9765e87ee5133bf4bb50d585c1814fafd995e3fb0da62c5ed07259b43dada7b
```

安装包属于外部构建输入，不提交到 Git。构建前可独立执行：

```bash
sha256sum artifacts/WeChatLinux_4.1.1.8_x86_64.deb
dpkg-deb -f artifacts/WeChatLinux_4.1.1.8_x86_64.deb Version Architecture
```
