helm repo add coroot https://coroot.github.io/helm-charts
helm repo update coroot
helm install --namespace coroot --create-namespace --set corootCE.service.type=NodePort coroot coroot/coroot
export NODE_PORT=$(kubectl get --namespace coroot -o jsonpath="{.spec.ports[0].nodePort}" services coroot)
export NODE_IP=$(kubectl get nodes --namespace coroot -o jsonpath="{.items[0].status.addresses[0].address}")
echo http://$NODE_IP:$NODE_PORT
curl -sfL https://raw.githubusercontent.com/coroot/coroot-node-agent/main/install.sh | \
  COLLECTOR_ENDPOINT=http://35.75.12.83:35412 \
  API_KEY=8npswdyt \
  SCRAPE_INTERVAL=15s \
  sh -
