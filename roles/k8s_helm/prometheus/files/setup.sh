#!/bin/bash
helm upgrade --install prometheus prometheus-community/prometheus \
  --namespace chutes \
  --create-namespace \
  --set server.persistentVolume.enabled=false \
  --set alertmanager.persistentVolume.enabled=false \
  --set prometheus-pushgateway.persistentVolume.enabled=false \
  --set prometheus-server.persistentVolume.enabled=false \
  --set alertmanager.persistence.enabled=false \
  --set server.nodeSelector."kubernetes\.io/hostname"=$1 \
  --set alertmanager.nodeSelector."kubernetes\.io/hostname"=$1 \
  --set pushgateway.nodeSelector."kubernetes\.io/hostname"=$1 \
  --set kubeStateMetrics.nodeSelector."kubernetes\.io/hostname"=$1
