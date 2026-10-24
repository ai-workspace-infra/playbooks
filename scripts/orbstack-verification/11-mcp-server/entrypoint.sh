#!/bin/sh
set -e
if [ -f /usr/local/bin/infrastructure-mcp-server ] && [ -x /usr/local/bin/infrastructure-mcp-server ]; then
  echo "Starting Infrastructure MCP Server (stdio transport) in background..."
  /usr/local/bin/infrastructure-mcp-server -config /etc/infrastructure-mcp-server/config.json </dev/null &
  echo "Infrastructure MCP Server started with PID $!"
fi

echo "Starting HTTP health responder on port 8080..."
while true; do
  printf 'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{"status":"ok","service":"infrastructure-mcp-server","version":"0.1.0"}\r\n' | nc -l -p 8080
done
