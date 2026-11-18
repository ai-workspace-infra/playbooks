 cd /Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks && ansible-playbook \
  -i "xworkmate-bridge.svc.plus," \
  --user ubuntu \
  -e "xworkspace_console_hosts=xworkmate-bridge.svc.plus" \
  -e "xworkspace_console_local_dashboard_dir=/home/ubuntu/xworkspace/dashboard" \
  -e "ansible_become_pass=XXXXXXXXX" \
  setup-xworkspace-console.yaml
