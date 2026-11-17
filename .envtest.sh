#!/bin/bash
T=$(mktemp -d)
printf 'VECTOR_AUTH_PASSWORD=Bearer abc+def=\n' > "$T/env"
cat > "/etc/systemd/system/test-envfile.service" <<U
[Unit]
Description=envfile test
[Service]
Type=oneshot
EnvironmentFile=-$T/env
ExecStart=/bin/sh -c 'echo "PASSWORD=[$VECTOR_AUTH_PASSWORD]"; echo LEN=${#VECTOR_AUTH_PASSWORD}'
U
systemctl daemon-reload
systemctl start test-envfile.service
journalctl -u test-envfile.service --no-pager -o cat -n 5 | tail -3
rm -f /etc/systemd/system/test-envfile.service
systemctl daemon-reload
systemctl reset-failed test-envfile.service 2>/dev/null
rm -rf "$T"
