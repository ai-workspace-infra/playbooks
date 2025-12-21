#!/bin/bash
helm upgrade --install metrics-server metrics-server/metrics-server \
  --namespace kube-system \
  --set nodeSelector."kubernetes\.io/hostname"=$1 \
  --set persistence.enabled=false
