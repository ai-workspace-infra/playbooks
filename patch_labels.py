import json

filepath = 'roles/docker/observability-server/files/dashboard.json'
with open(filepath, 'r') as f:
    dashboard = json.load(f)

for v in dashboard.get('templating', {}).get('list', []):
    if v.get('name') == 'instance':
        v['label'] = 'Node (节点)'
    if v.get('name') == 'job':
        v['label'] = 'Job'
    if v.get('name') == 'user':
        v['label'] = 'User (用户)'

with open(filepath, 'w') as f:
    json.dump(dashboard, f, indent=2)
