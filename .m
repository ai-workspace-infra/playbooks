fix(vector): 用 systemd-run 复用 EnvironmentFile 做配置校验

static-file 模式下 vector.toml 只含占位符, 真值由 systemd 从 EnvironmentFile
注入。校验若不带上同一份环境, 会被 "Missing environment variable" 误判并触发
配置回滚。

改用 systemd-run 复现运行环境, 而不是在 shell 里 source 该文件:
- systemd 的 EnvironmentFile 把整行剩余部分当作值, "Bearer xxx" 完整保留
- bash 的 source 会在空格处断词, 把后半段当命令执行
两条路径语义不同, 必须走 systemd 才能与 vector.service 一致。

保留原有字面命令行, 仅在其外层加 systemd-run 分支, 不改变既有校验契约。