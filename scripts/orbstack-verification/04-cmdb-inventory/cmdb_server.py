import http.server
import json
import socketserver

PORT = 8000

class CMDBHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/healthz':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "service": "cmdb-api"}).encode('utf-8'))
        elif self.path == '/api/v1/inventory':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            inventory = {
                "_meta": {
                    "hostvars": {
                        "platform-node-01": {"ansible_host": "127.0.0.1", "role": "control-plane"},
                        "platform-node-02": {"ansible_host": "127.0.0.1", "role": "worker"}
                    }
                },
                "all": {
                    "hosts": ["platform-node-01", "platform-node-02"]
                },
                "control_plane": {
                    "hosts": ["platform-node-01"]
                }
            }
            self.wfile.write(json.dumps(inventory).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    with socketserver.TCPServer(("0.0.0.0", PORT), CMDBHandler) as httpd:
        print(f"CMDB API listening on {PORT}")
        httpd.serve_forever()
