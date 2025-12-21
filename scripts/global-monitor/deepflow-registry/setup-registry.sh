#!/bin/bash

https://github.com/containerd/nerdctl/releases/download/v2.0.2/nerdctl-2.0.2-linux-amd64.tar.gz

sudo cp compose.yaml /opt/deepflow-registry/compose.yaml
sudo nerdctl compose -f /opt/deepflow-registry/compose.yaml down
sudo nerdctl compose -f /opt/deepflow-registry/compose.yaml up -d

#运行时为Containerd
sudo erdctl load -i /usr/local/deepflow/registry.tar
sudo CONTAINERD_ADDRESS=/run/k3s/containerd/containerd.sock nerdctl  --namespace k8s.io  compose -f /opt/deepflow-registry/compose.yaml up -d
#nerdctl run -d -e REGISTRY_HTTP_ADDR=0.0.0.0:5000 --net=host -v /usr/local/deepflow/registry:/var/lib/registry --restart=always --name registry hub.deepflow.yunshan.net/dev/registry:latest
