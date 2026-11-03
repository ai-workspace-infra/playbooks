import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT / "roles/vhosts/xray-exporter/files/patch_xray_metrics.py"
SPEC = importlib.util.spec_from_file_location("patch_xray_metrics", PATCHER)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class XrayMetricsConfigRepairTests(unittest.TestCase):
    def test_restart_handler_stops_services_and_guards_stale_socket_cleanup(self):
        handler = (ROOT / "roles/vhosts/xray-exporter/handlers/restart-xray-services.yml").read_text()
        self.assertLess(handler.index("Stop Xray services"), handler.index("Remove stale XHTTP Unix socket"))
        self.assertLess(handler.index("Require Xray processes to stop"), handler.index("Remove stale XHTTP Unix socket"))
        self.assertLess(handler.index("Remove stale XHTTP Unix socket"), handler.index("Start Xray services"))
        self.assertIn("xray_exporter_xhttp_socket.stat.issock", handler)
        self.assertIn("xray_exporter_xhttp_socket.stat.islnk", handler)

    def test_adds_loopback_stats_api_and_system_counters(self):
        source = {
            "inbounds": [{"tag": "proxy", "listen": "0.0.0.0", "port": 1443}],
            "outbounds": [{"tag": "direct", "protocol": "freedom"}],
            "routing": {"rules": [{"type": "field", "ip": ["geoip:private"], "outboundTag": "blocked"}]},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            patched, changed = MODULE.patch_config(path, 10086)

        self.assertTrue(changed)
        self.assertEqual(patched["inbounds"][0], {
            "listen": "127.0.0.1", "port": 10086, "protocol": "dokodemo-door",
            "settings": {"address": "127.0.0.1"}, "tag": "api",
        })
        self.assertEqual(patched["api"], {"tag": "api", "services": ["StatsService"]})
        self.assertEqual(patched["routing"]["rules"][0]["outboundTag"], "api")
        self.assertEqual(patched["policy"]["system"], {
            "statsInboundUplink": True, "statsInboundDownlink": True,
            "statsOutboundUplink": True, "statsOutboundDownlink": True,
        })
        self.assertTrue(patched["policy"]["levels"]["0"]["statsUserUplink"])
        self.assertTrue(patched["policy"]["levels"]["0"]["statsUserDownlink"])

    def test_is_idempotent_for_already_repaired_config(self):
        source = {
            "inbounds": [{"tag": "api", "listen": "127.0.0.1", "port": 10086,
                          "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}}],
            "api": {"tag": "api", "services": ["StatsService"]},
            "stats": {},
            "routing": {"rules": [{"inboundTag": ["api"], "outboundTag": "api", "type": "field"}]},
            "policy": {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
                       "system": {"statsInboundUplink": True, "statsInboundDownlink": True,
                                  "statsOutboundUplink": True, "statsOutboundDownlink": True}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            patched, changed = MODULE.patch_config(path, 10086)

        self.assertFalse(changed)
        self.assertEqual(patched, source)

    def test_refuses_non_loopback_api_listener(self):
        source = {"inbounds": [{"tag": "api", "listen": "0.0.0.0", "port": 10086}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "non-loopback"):
                MODULE.patch_config(path, 10086)


if __name__ == "__main__":
    unittest.main()
