fix(vector): 校验时按 systemd 语义重建凭据环境

static-file 模式下 vector.toml 只含占位符, 真值由 systemd 从 EnvironmentFile
注入。校验若不带上同一份环境, 会被 "Missing environment variable" 误判并触发
配置回滚。

用 IFS= read -r 逐行读取凭据文件并只剥掉键名前缀: EnvironmentFile 把整行剩余
部分当作值, 而 "source" 会在 "Bearer xxx" 的空格处断词, 把后半段当命令执行。
既保持与 systemd 一致的语义, 也不依赖 systemd-run 的管道退出码。