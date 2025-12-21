#!/bin/bash

sudo mkdir -pv /opt/rancher/k3s
curl -sfL https://get.k3s.io | sudo sh -s - --disable=traefik,servicelb                                   \
        --data-dir=/opt/rancher/k3s                              \
        --kube-apiserver-arg service-node-port-range=0-50000

sudo mkdir -pv ~/.kube/
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown -R ubuntu:ubuntu ~/.kube/

sudo snap install helm --classic

k8s_node=`sudo kubectl  get nodes | awk 'NR>1{print $1}'`

sudo kubectl label node $k8s_node master_controller=enable
sudo kubectl label node $k8s_node tsdb=enable
sudo kubectl label node $k8s_node dfdb=enable

sudo kubectl create ns deepflow || true
sudo kubectl create secret docker-registry deepflow-registry-secret \
  --docker-server=global-images.onwalk.net \
  --docker-username=admin 		   \
  --docker-password=Harbor12345 	   \
  --docker-email=manbuzhe2009@qq.com 	   \
  -n deepflow

sudo kubectl create ns openebs || true
sudo kubectl create secret docker-registry deepflow-registry-secret \
  --docker-server=global-images.onwalk.net \
  --docker-username=admin 		   \
  --docker-password=Harbor12345 	   \
  --docker-email=manbuzhe2009@qq.com 	   \
  -n openebs

/usr/local/deepflow/bin/deepflow-deploy -i
