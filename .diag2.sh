#!/bin/bash
orig=$(awk '/^\[sinks\.prometheus_remote\.auth\]/{f=1;next} /^\[/{f=0} f&&/^password/{print}' /etc/vector/vector.toml | head -1 | sed 's/^password = "//; s/"$//')
boot=$(grep '^VECTOR_AUTH_PASSWORD=' /etc/vector/remote-write-auth.json | sed 's/^VECTOR_AUTH_PASSWORD=//')
echo "orig len=${#orig}  boot len=${#boot}"
if [ -n "$orig" ] && [ "$orig" = "$boot" ]; then echo "✅ 凭据完全一致"; else echo "❌ 仍不一致"; fi
