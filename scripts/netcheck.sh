#!/usr/bin/env bash
# netcheck.sh — Diagnose DNS / TLS / Route problems for a given target

TARGET=${1:-fonts.gstatic.com}   # 默认检测 fonts.gstatic.com，也可自定义
PROXY=${https_proxy:-""}

if [ -z "$1" ]; then
  echo "Usage: $0 <domain>"
  echo "Example: $0 accounts.google.com"
  echo
  echo "No argument supplied, using default target: $TARGET"
fi

echo "=== 🌐 Network Diagnostic for $TARGET ==="
echo "Time: $(date)"
echo

echo "1️⃣ Checking DNS resolution..."
dig +short "$TARGET" || nslookup "$TARGET"
echo

IP=$(dig +short "$TARGET" | grep -m1 -Eo '([0-9]{1,3}\.){3}[0-9]{1,3}')
if [ -z "$IP" ]; then
  echo "❌ DNS failed — cannot resolve $TARGET"
  exit 1
fi
echo "✅ DNS OK → $TARGET resolved to $IP"
echo

echo "2️⃣ Checking basic connectivity..."
ping -c 3 -W 2 "$IP" >/dev/null 2>&1 && echo "✅ Ping reachable ($IP)" || echo "⚠️  Ping not reachable (may be ICMP blocked)"
echo

echo "3️⃣ Checking route path..."
traceroute -m 15 -w 2 "$IP" || echo "⚠️ Traceroute failed — possibly blocked or proxied"
echo

echo "4️⃣ Testing HTTPS handshake (TLS)..."
if [ -n "$PROXY" ]; then
  echo "Using proxy: $PROXY"
fi

curl -v --connect-timeout 10 -4 -I "https://$TARGET" 2>&1 | egrep "Trying|Connected|SSL|error|subject|issuer|HTTP"
RC=$?
echo

if [ $RC -eq 0 ]; then
  echo "✅ TLS handshake successful — outbound HTTPS working"
else
  echo "❌ TLS handshake failed — outbound 443 likely filtered or intercepted"
fi

echo
echo "5️⃣ Summary:"
if [ $RC -ne 0 ]; then
  echo "→ Problem most likely in:"
  echo "   • DNS (if Step 1 failed)"
  echo "   • Firewall/Proxy (if Step 2/3 OK but Step 4 fails)"
  echo "   • TLS interception (if Step 4 shows certificate mismatch)"
else
  echo "✅ Everything looks fine — network path and TLS normal"
fi
