import json

def patch_file(filepath):
    with open(filepath, 'r') as f:
        data = json.load(f)
    
    data['stats'] = {}
    data['api'] = {"tag": "api", "services": ["StatsService"]}
    data['policy'] = {
        "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
        "system": {"statsInboundUplink": True, "statsInboundDownlink": True, "statsOutboundUplink": True, "statsOutboundDownlink": True}
    }
    
    data.setdefault('inbounds', []).insert(0, {
        "listen": "127.0.0.1", "port": 8080, "protocol": "dokodemo-door",
        "settings": {"address": "127.0.0.1"}, "tag": "api"
    })
    
    data.setdefault('routing', {}).setdefault('rules', []).insert(0, {
        "inboundTag": ["api"], "outboundTag": "api", "type": "field"
    })
    
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=4)
    print(f"Successfully wrote to {filepath}")

patch_file('/usr/local/etc/xray/config.json')
