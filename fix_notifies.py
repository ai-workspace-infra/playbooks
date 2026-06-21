import os

with open('roles/vhosts/gateway_openclaw/tasks/main.yml', 'r') as f:
    c = f.read().replace('notify: Restart openclaw gateway', 'notify: Restart openclaw')
with open('roles/vhosts/gateway_openclaw/tasks/main.yml', 'w') as f:
    f.write(c)

with open('roles/vhosts/xworkmate_bridge/tasks/main.yml', 'r') as f:
    c = f.read().replace('notify: Reload bridge', 'notify: Restart bridge')
with open('roles/vhosts/xworkmate_bridge/tasks/main.yml', 'w') as f:
    f.write(c)

print("Done")
