# roles/vhosts/xray-core

Pure L0 foundation role for installing the checksum-verified `xray` binary (`/usr/local/bin/xray`) and geo routing assets (`/usr/local/share/xray/{geoip,geosite}.dat`).

No systemd units, runtime configs, service accounts, or ingress fragments are managed here. Upper-layer runtime roles (`xconnect_proxy`, `xconnect_gateway`) depend on this role and listen to `xray-core binary changed` to restart their own services.
