import json
import sys

def patch_config(filepath):
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return

    data['stats'] = {}
    data['api'] = {
        "tag": "api",
        "services": ["StatsService"]
    }
    data['policy'] = {
        "levels": {
            "0": {
                "statsUserUplink": True,
                "statsUserDownlink": True
            }
        },
        "system": {
            "statsInboundUplink": True,
            "statsInboundDownlink": True,
            "statsOutboundUplink": True,
            "statsOutboundDownlink": True
        }
    }

    # Add api inbound if not exists
    has_api_inbound = False
    for ib in data.get('inbounds', []):
        if ib.get('tag') == 'api':
            has_api_inbound = True
            break
    if not has_api_inbound:
        data.setdefault('inbounds', []).insert(0, {
            "listen": "127.0.0.1",
            "port": 8080,
            "protocol": "dokodemo-door",
            "settings": {
                "address": "127.0.0.1"
            },
            "tag": "api"
        })

    # Add routing rule for api
    has_api_routing = False
    routing = data.setdefault('routing', {})
    rules = routing.setdefault('rules', [])
    for rule in rules:
        if 'api' in rule.get('inboundTag', []):
            has_api_routing = True
            break
    if not has_api_routing:
        rules.insert(0, {
            "inboundTag": ["api"],
            "outboundTag": "api",
            "type": "field"
        })

    try:
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"Patched {filepath}")
    except Exception as e:
        print(f"Error writing {filepath}: {e}")

patch_config('/usr/local/etc/xray/config.json')
patch_config('/usr/local/etc/xray/templates/xray.xhttp.template.json')
patch_config('/usr/local/etc/xray/templates/xray.tcp.template.json')
