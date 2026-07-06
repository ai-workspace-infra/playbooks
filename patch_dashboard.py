import json

filepath = 'roles/docker/observability-server/files/dashboard.json'
with open(filepath, 'r') as f:
    dashboard = json.load(f)

# Find the maximum Y position
max_y = 0
for panel in dashboard.get('panels', []):
    y = panel.get('gridPos', {}).get('y', 0)
    h = panel.get('gridPos', {}).get('h', 0)
    max_y = max(max_y, y + h)

# Add a new panel for User Traffic Total
new_panel_downlink = {
  "type": "bargauge",
  "title": "Total Download Traffic per User",
  "gridPos": {
    "x": 0,
    "y": max_y,
    "w": 12,
    "h": 8
  },
  "datasource": "Prometheus",
  "targets": [
    {
      "expr": "sum(increase(xray_traffic_downlink_bytes_total{job=~\"$job\",instance=~\"$instance\",dimension=\"user\"}[$__range])) by (target)",
      "legendFormat": "{{target}}",
      "refId": "A"
    }
  ],
  "options": {
    "displayMode": "gradient",
    "orientation": "horizontal",
    "reduceOptions": {
      "calcs": [
        "lastNotNull"
      ],
      "fields": "",
      "values": False
    },
    "showUnfilled": True
  },
  "fieldConfig": {
    "defaults": {
      "custom": {},
      "unit": "decbytes",
      "min": 0
    },
    "overrides": []
  }
}

new_panel_uplink = {
  "type": "bargauge",
  "title": "Total Upload Traffic per User",
  "gridPos": {
    "x": 12,
    "y": max_y,
    "w": 12,
    "h": 8
  },
  "datasource": "Prometheus",
  "targets": [
    {
      "expr": "sum(increase(xray_traffic_uplink_bytes_total{job=~\"$job\",instance=~\"$instance\",dimension=\"user\"}[$__range])) by (target)",
      "legendFormat": "{{target}}",
      "refId": "A"
    }
  ],
  "options": {
    "displayMode": "gradient",
    "orientation": "horizontal",
    "reduceOptions": {
      "calcs": [
        "lastNotNull"
      ],
      "fields": "",
      "values": False
    },
    "showUnfilled": True
  },
  "fieldConfig": {
    "defaults": {
      "custom": {},
      "unit": "decbytes",
      "min": 0
    },
    "overrides": []
  }
}

# Also ensure panels list exists
if 'panels' not in dashboard:
    dashboard['panels'] = []

# Increment IDs
max_id = max([p.get('id', 0) for p in dashboard['panels']]) if dashboard['panels'] else 0
new_panel_downlink['id'] = max_id + 1
new_panel_uplink['id'] = max_id + 2

dashboard['panels'].append(new_panel_downlink)
dashboard['panels'].append(new_panel_uplink)

with open(filepath, 'w') as f:
    json.dump(dashboard, f, indent=2)

print("Dashboard patched successfully")
