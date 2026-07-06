import json

filepath = 'roles/docker/observability-server/files/dashboard.json'
with open(filepath, 'r') as f:
    dashboard = json.load(f)

templating = dashboard.get('templating', {}).get('list', [])

# Check if 'user' variable already exists
user_var_exists = any(v.get('name') == 'user' for v in templating)

if not user_var_exists:
    user_var = {
      "allValue": ".*",
      "current": {
        "selected": True,
        "text": "All",
        "value": "$__all"
      },
      "datasource": "Prometheus",
      "definition": "label_values(xray_traffic_downlink_bytes_total{dimension=\"user\"}, target)",
      "description": "Select Xray User",
      "error": None,
      "hide": 0,
      "includeAll": True,
      "label": "User",
      "multi": True,
      "name": "user",
      "options": [],
      "query": "label_values(xray_traffic_downlink_bytes_total{dimension=\"user\"}, target)",
      "refresh": 1,
      "regex": "",
      "skipUrlSync": False,
      "sort": 1,
      "type": "query"
    }
    templating.append(user_var)

# Now we need to update the panels to USE the $user variable!
# Basically anywhere `dimension="user"` is queried, we should add `target=~"$user"`
for panel in dashboard.get('panels', []):
    for target in panel.get('targets', []):
        expr = target.get('expr', '')
        if 'dimension="user"' in expr and 'target=~"$user"' not in expr:
            # Replace dimension="user" with dimension="user",target=~"$user"
            target['expr'] = expr.replace('dimension="user"', 'dimension="user",target=~"$user"')

with open(filepath, 'w') as f:
    json.dump(dashboard, f, indent=2)

print("Dashboard variables patched successfully")
