import re

filepath = 'roles/vhosts/vector-agent/templates/vector.toml.j2'
with open(filepath, 'r') as f:
    content = f.read()

# Add transform block if not exists
if '[transforms.add_labels]' not in content:
    transform_block = """
[transforms.add_labels]
type = "remap"
inputs = ["xray_metrics", "internal_metrics"]
source = '''
.tags.instance = "{{ inventory_hostname }}"
.tags.job = "xray"
'''
"""
    # Replace the sinks input to use add_labels
    content = content.replace('inputs = ["xray_metrics", "internal_metrics"]', 'inputs = ["add_labels"]')
    # Insert transform before sinks
    content = content.replace('[sinks.prometheus_remote]', transform_block + '\n[sinks.prometheus_remote]')
    
    with open(filepath, 'w') as f:
        f.write(content)
    print("Vector config patched successfully")
else:
    print("Already patched")
