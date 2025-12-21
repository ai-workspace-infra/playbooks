import os
import sys
from jinja2 import Template

# Check if required environment variables are set
required_vars = ['SSH_USER', 'SSH_PRIVATE_KEY']
for var in required_vars:
    if var not in os.environ:
        print(f"{var} is not set. Aborting.")
        sys.exit(1)

# Get the SSH_USER and SSH_PRIVATE_KEY from environment variables
ssh_user = os.environ['SSH_USER']
ssh_private_key = os.environ['SSH_PRIVATE_KEY']

# Check if input is provided
if len(sys.argv) < 2:
    print("No groups and nodes provided. Usage: python dynamic_inventory.py 'group_name:host_name:host_ip'")
    sys.exit(1)

# Parse input groups and hosts
input_data = sys.argv[1]
group_nodes = input_data.split()

# Dictionary to hold groups and their hosts
groups = {}

for group_node in group_nodes:
    group, host_name, host_ip = group_node.split(':')
    if group not in groups:
        groups[group] = []
    groups[group].append({'host_name': host_name, 'host_ip': host_ip})

# Define the inventory template
inventory_template = """
{% for group, hosts in groups.items() %}
[{{ group }}]
{% for host in hosts %}
{{ host.host_name }} ansible_host={{ host.host_ip }}
{% endfor %}
{% endfor %}

[all:vars]
ansible_port=22
ansible_ssh_user={{ ssh_user }}
ansible_ssh_private_key_file=~/.ssh/id_rsa
ansible_host_key_checking=False
"""

# Create the SSH key directory if it doesn't exist
ssh_dir = os.path.expanduser("~/.ssh")
os.makedirs(ssh_dir, exist_ok=True)

# Create the SSH key file
ssh_key_path = os.path.join(ssh_dir, 'id_rsa')
with open(ssh_key_path, 'w') as ssh_key_file:
    ssh_key_file.write(ssh_private_key)
os.chmod(ssh_key_path, 0o400)  # Set permissions to 0400

# Render the inventory file
template = Template(inventory_template)
output = template.render(groups=groups, ssh_user=ssh_user)

# Write to the inventory file
os.makedirs('hosts', exist_ok=True)
with open('hosts/inventory', 'w') as inventory_file:
    inventory_file.write(output)

print("Inventory file created successfully!")
