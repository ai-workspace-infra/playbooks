# Free Proxy 大文件上传配置验收 case（2026-10-18）

## 目的与验收范围

对三个 Free Selfhost PROD 节点执行真实文件上传，验证当前 `policy.levels.0.connIdle=3600` 配置下的传输正确性，并记录完整链路耗时、吞吐和失败阶段。所有 payload 均为随机合成二进制内容，不包含业务文件。

本 case 验证 VLESS/XHTTP 到受控 HTTPS 接收端的链路，不代表 ChatGPT 应用端上传或某个客户端 App 的验收；没有更改配置前后的配对试验，不能据此量化 `connIdle` 调整带来的收益。

## 固定矩阵

- 文件阶梯：1、2、4、8、16、32、64、128、256、512 MB；`1 MB = 1,000,000 bytes`。
- 代理模式：`auto`、`packet-up`；每个节点、模式、文件大小各执行一次，共 60 项。同一客户端两种模式复用同一批文件与 hash。
- 三条节点链路同时执行，各链路内按文件大小递增；不重试。
- 单项总超时 900 秒；连续 60 秒速率低于 8192 bytes/s 时终止该项；完整执行所有阶梯，逐项记录失败，不自动重跑。
- 全部执行的合成上传量为 6,138 MB。吞吐受跨区域链路和并发负载影响，不作为单节点带宽上限。

| 被测代理 | 客户端主机 | HTTPS 接收主机 |
| --- | --- | --- |
| `jp-free-selfhost-prod-v2.svc.plus` | `us-free-selfhost-prod-v2.svc.plus` | `sg-free-selfhost-prod-v2.svc.plus` |
| `sg-free-selfhost-prod-v2.svc.plus` | `jp-free-selfhost-prod-v2.svc.plus` | `us-free-selfhost-prod-v2.svc.plus` |
| `us-free-selfhost-prod-v2.svc.plus` | `sg-free-selfhost-prod-v2.svc.plus` | `jp-free-selfhost-prod-v2.svc.plus` |

每条真实路径：客户端 curl → 本机临时 SOCKS5 → 临时 Xray 客户端 → 被测节点 HTTPS/XHTTP `/split` → 被测 Xray VLESS → 另一节点公网 HTTPS 接收路径 → Caddy → 仅监听 loopback 的流式校验接收端。

## 客户端与接收端合同

客户端复用已获授权的管理测试身份，不新增账户、不修改 entitlement。节点凭据只在运行时读取，临时配置为 `0600`，结束后删除。该测试覆盖 Free 节点传输能力，不覆盖普通 Free 用户的登录、授权和账单规则。

测试客户端 Core 为 Xray `26.3.27`。以下为脱敏配置；`RUNTIME_UUID`、`NODE_PUBLIC_IPV4`、`NODE_FQDN` 和 SOCKS 端口均须在临时目录中注入。Linux 测试进程绑定 `eth0`；Mac 对照样本绑定 `en0`，避免与已连接的 VPN 叠加。

```json
{
  "log": {"loglevel": "warning"},
  "inbounds": [{
    "listen": "127.0.0.1", "port": 19080, "protocol": "socks",
    "settings": {"auth": "noauth", "udp": false}
  }],
  "outbounds": [{
    "protocol": "vless",
    "settings": {"vnext": [{
      "address": "NODE_PUBLIC_IPV4", "port": 443,
      "users": [{"id": "RUNTIME_UUID", "encryption": "none"}]
    }]},
    "streamSettings": {
      "network": "xhttp", "security": "tls",
      "sockopt": {"interface": "eth0"},
      "tlsSettings": {"serverName": "NODE_FQDN", "alpn": ["h2"], "fingerprint": "chrome"},
      "xhttpSettings": {"path": "/split", "mode": "auto"}
    }
  }],
  "policy": {"levels": {"0": {"connIdle": 3600}}}
}
```

第二轮仅把客户端 mode 改成 `packet-up`。生产节点服务和用户配置在测试期间保持运行，临时客户端为独立进程。

HTTPS 接收端使用三节点现有证书；每次运行创建独有路径和临时认证 token。Caddy 只临时增加匹配该 host/path 的 route，反代到本机 loopback 随机端口。接收端逐块读取 body、计算 SHA-256，只保留字节数、hash、耗时和出口信息，不保存上传内容。最大 body 为 512,000,000 bytes。

成功响应合同：

```json
{"run_id":"运行标识","received_bytes":512000000,"sha256":"合成文件 SHA-256","elapsed_s":0,"egress_ip":"被测代理公网出口"}
```

单项请求使用普通 HTTPS POST 和文件流；节点凭据不出现在命令行。下面为等效复现命令，`RUNTIME_CURL_CONFIG` 是通过 `0600` 文件提供一次性接收端认证 header 的 curl config。

```sh
curl --config "$RUNTIME_CURL_CONFIG" \
  --noproxy '' --socks5-hostname "127.0.0.1:$SOCKS_PORT" --http2 \
  --request POST --upload-file "$PAYLOAD" --header 'Expect:' \
  --connect-timeout 20 --max-time 900 --speed-limit 8192 --speed-time 60 \
  --retry 0 --output "$RECEIPT_JSON" --write-out '%{json}' "$RECEIVER_URL"
```

## 指标与通过条件

每项保留 curl exit、HTTP 状态、SOCKS 连接时间、目标 TLS 完成时间、首字节时间、总耗时、实际上传字节数和接收端校验结果。curl `time_total` 为该文件请求完整链路耗时，不含文件生成和临时 Core 进程启动。每个 curl 请求新建 SOCKS/目标 TLS 会话；同模式 Core 持续运行，XHTTP 底层连接池可能复用。吞吐换算为 `speed_upload_bytes_s × 8 / 1,000,000` Mbps。首字节是 curl 原始计时，可能包含 HTTP 信息响应，不作为上传完成时间。

每项同时满足以下条件才通过：curl exit 0、HTTP 200、发送和接收长度均与文件长度相等、SHA-256 一致，且接收端看到的出口属于被测代理。失败定位分别记录为 SOCKS 连接、代理传输/目标 TLS、上传流、目标 HTTP/响应、内容完整性或出口不符；主动取消的基线样本不计为节点失败。

通过率只表示本轮冒烟矩阵的通过项数/执行项数；每项仅一次，不推导生产 SLA 或长期稳定性。性能指标记录实测值，本 case 未定义最低 Mbps 的业务 SLO。

## Mac 本机对照样本

初次检测到系统路由指向 `utun6`，因此停止叠加 VPN 的基线运行。随后仅为测试客户端绑定物理接口 `en0`，并检查到三个临时 Xray 的 TLS socket 源地址确实来自 `en0`。

物理接口对照中，Mac 直连 SG HTTPS 接收端上传 1 MB 用时 29.602 秒（约 0.270 Mbps），经 JP `auto` 上传 1 MB 用时 24.680 秒（约 0.324 Mbps）。当时还有其他测试流量，这两项不能作为公平的协议快慢对比；它们表明本机到外网路径也存在低吞吐，不能把本机慢上传直接归因于服务端 Caddy/Xray 参数。完整阶梯验收使用上表的 Linux 客户端路径。

## 先前中断样本与修复

首轮远程矩阵 `smoke-20261018T040909Z-92a47842` 执行 58 项，53 成功、5 失败，未完成完整矩阵。北京时间 12:16 左右各节点 Agent 配置同步并重启 Xray；运行配置随后回退到缓存的旧模板，Caddy 进程和临时接收路由仍存在。

| 节点 | 模式 | 文件 MB | 失败耗时 s | 已发送 bytes | 失败阶段/证据 |
| --- | --- | ---: | ---: | ---: | --- |
| SG | packet-up | 64 | 2.9099 | 4538367 | curl 18，HTTP/2 流提前关闭 |
| SG | packet-up | 128 | 0.0676 | 0 | curl 35，代理传输/目标 TLS 错误 |
| SG | packet-up | 256 | 0.0730 | 0 | curl 35，代理传输/目标 TLS 错误 |
| US | auto | 512 | 140.5515 | 300646100 | curl 56，上传中途 EOF |
| JP | packet-up | 256 | 3.3754 | 10616532 | curl 56，上传中途 EOF |

上述尺寸与时间各不相同，不构成固定文件大小上限的证据。原始 SG TLS 样本的自动阶段标签过于宽泛，以 curl 35 错误定位为准；原始 JSON 保留原标签。已重载 Agent 使其读取新模板，并在 Playbooks 补上模板变更通知及幂等对齐入口。详情见 [配置优化记录](../env/xconnect-proxy-large-upload-timeout-2026-10-18.md)。

另外一次运行 `smoke-20261018T042558Z-8010b47f` 在 3 项成功后由操作人停止，以按最新要求先完成 Playbook 应用；不计入节点失败或本轮通过率。中断运行均已清理临时资源。首轮原始结果、服务生命周期和配置回退证据见 [中断样本 JSON](evidence/xconnect-free-proxy-upload-2026-10-18-interrupted-baseline.json)。

## 本轮结果

运行 `smoke-20261018T042856Z-cf2597e6`；北京时间 2026-10-18 12:28:56 至 13:05:31。完整矩阵 **58/60 通过（96.67%）**；实际发送合成 payload 5,901.363 MB。

| 节点 | 模式 | 通过率 | 512 MB 总耗时（s） | 512 MB 吞吐（Mbps） | 全阶梯加权吞吐（Mbps） |
| --- | --- | --- | ---: | ---: | ---: |
| JP | auto | 10/10（100%） | 139.832 | 29.292 | 28.053 |
| JP | packet-up | 9/10（90%） | 140.270（失败） | — | 21.849 |
| SG | auto | 10/10（100%） | 174.570 | 23.463 | 22.603 |
| SG | packet-up | 10/10（100%） | 172.421 | 23.756 | 22.778 |
| US | auto | 10/10（100%） | 230.356 | 17.781 | 17.257 |
| US | packet-up | 9/10（90%） | 235.333 | 17.405 | 15.018 |

全阶梯加权吞吐为实际发送字节总和 / 请求耗时总和，包含失败请求的部分发送量，不表示接收端确认的有效吞吐。失败的 512 MB 项不展示完成吞吐。

### auto 逐级实测

成功单元格为完整链路秒数 / Mbps；FAIL 标明耗时及失败阶段。

| 文件（MB） | JP | SG | US |
| ---: | ---: | ---: | ---: |
| 1 | 2.100 / 3.809 | 2.602 / 3.075 | 3.026 / 2.644 |
| 2 | 2.068 / 7.735 | 2.780 / 5.754 | 3.366 / 4.753 |
| 4 | 1.919 / 16.671 | 3.054 / 10.478 | 3.260 / 9.816 |
| 8 | 2.903 / 22.049 | 4.397 / 14.554 | 4.781 / 13.386 |
| 16 | 5.339 / 23.973 | 6.888 / 18.583 | 7.999 / 16.002 |
| 32 | 9.218 / 27.771 | 12.047 / 21.250 | 14.498 / 17.658 |
| 64 | 17.646 / 29.015 | 23.251 / 22.021 | 31.338 / 16.338 |
| 128 | 36.342 / 28.177 | 44.627 / 22.946 | 59.531 / 17.201 |
| 256 | 74.369 / 27.538 | 87.866 / 23.308 | 116.097 / 17.640 |
| 512 | 139.832 / 29.292 | 174.570 / 23.463 | 230.356 / 17.781 |

### packet-up 逐级实测

成功单元格为完整链路秒数 / Mbps；FAIL 标明耗时及失败阶段。

| 文件（MB） | JP | SG | US |
| ---: | ---: | ---: | ---: |
| 1 | 1.928 / 4.150 | 2.515 / 3.181 | 2.908 / 2.751 |
| 2 | 2.180 / 7.340 | 2.728 / 5.864 | 3.601 / 4.443 |
| 4 | 1.893 / 16.909 | 3.162 / 10.119 | 3.240 / 9.877 |
| 8 | 2.937 / 21.790 | 4.353 / 14.704 | 4.799 / 13.335 |
| 16 | 5.210 / 24.567 | 6.869 / 18.635 | 8.160 / 15.686 |
| 32 | 9.388 / 27.270 | 12.496 / 20.487 | FAIL 83.070s: upload_stream |
| 64 | 17.683 / 28.954 | 22.847 / 22.410 | 29.683 / 17.249 |
| 128 | 36.178 / 28.304 | 43.967 / 23.290 | 56.923 / 17.989 |
| 256 | 71.543 / 28.626 | 87.933 / 23.291 | 115.367 / 17.752 |
| 512 | FAIL 140.270s: upload_stream | 172.421 / 23.756 | 235.333 / 17.405 |

### 完整性、失败位置与清理

- HTTP、长度、hash、代理出口同时通过：58/60；失败项：2。

| 节点 | 模式 | MB | 耗时 s | 已发送 bytes | curl/HTTP | 失败位置 | 错误 |
| --- | --- | ---: | ---: | ---: | --- | --- | --- |
| jp | packet-up | 512 | 140.2704 | 278855380 | 56/0 | upload_stream | curl: (56) Recv failure: Connection reset by peer |
| us | packet-up | 32 | 83.0699 | 28507861 | 28/0 | upload_stream | curl: (28) Operation too slow. Less than 8192 bytes/sec transferred the last 60 seconds |

- 测试后两份模板和两份运行配置的 connIdle：JP 全部为 3600, SG 全部为 3600, US 全部为 3600。
- 三个临时接收 route 均已移除，三个接收进程和三个客户端进程均停止，临时凭据配置和约 3 GB payload 已删除。
- 测试前与延迟复查的 Agent、Xray、Xray-TCP、Caddy 服务 PID/启动时间/自动重启计数对比：JP 发生变化，见 JSON, SG 发生变化，见 JSON, US 发生变化，见 JSON。
- 上传实际时间范围：2026-10-18T04:29:09Z 至 2026-10-18T04:46:17Z；清理回执晚于最后一次上传，SSH 控制耗时不计入 curl 请求耗时。
- 脱敏 journal 检索显示：两次失败时段未记录 Xray/Xray-TCP 服务重启；两项失败的根因尚未确定。延迟复查观察到 UTC 05:16（北京时间 13:16）Accounts 同步后重启，发生在最后一次上传及清理之后，不能解释 UTC 04:38/04:39 的失败。其后四份 connIdle 仍为 3600，说明参数未再次回退。
- auto 本轮 30/30；packet-up 28/30。本 case 不判定全部通过，也不通过反复重跑覆盖失败。尚需针对 JP packet-up 大文件 reset 和 US packet-up 短时停顿定位；单次结果不能证明协议普遍优劣。
- 单轮、每项一次、三条并发链路；不据此推导长期稳定性、实际用户网络速度或协议模式的普遍优劣。

逐项字段与原始记录：[CSV](evidence/xconnect-free-proxy-upload-2026-10-18.csv)、[完整 JSON](evidence/xconnect-free-proxy-upload-2026-10-18.json)、[Mac 对照与主动取消样本](evidence/xconnect-free-proxy-upload-2026-10-18-mac-baseline.json)。JSON 包含 payload hash manifest、服务状态快照和清理回执；不包含节点 UUID、认证 token 或 SSH 私钥。
