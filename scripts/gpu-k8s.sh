#!/bin/bash
set -e

# Install offline packages required for GPU support
install_all_offline_packages() {
  echo "Installing GPU driver and toolkit packages"
  # Implementation assumes packages are available locally
  sudo apt-get update
  sudo apt-get install -y nvidia-driver-535 nvidia-headless-535 nvidia-container-toolkit
}

# Deploy the NVIDIA GPU operator
deploy_plugin() {
  helm repo add nvidia https://helm.ngc.nvidia.com/nvidia || true
  helm upgrade --install gpu-operator nvidia/gpu-operator \
    --namespace gpu-operator \
    --create-namespace \
    --set nodeSelector.kubernetes.io/gpu="true" \
    --set driver.enabled=true \
    --set toolkit.enabled=true \
    --set devicePlugin.enabled=true \
    --set operator.runtimeClass="nvidia-container-runtime" \
    --set operator.defaultRuntime=containerd \
    --set containerRuntime.socketPath=/var/run/containerd/containerd.sock
}

install_all_offline_packages
deploy_plugin
