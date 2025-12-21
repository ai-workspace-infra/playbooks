#!/bin/bash
helm upgrade --install kubernetes-dashboard kubernetes-dashboard/kubernetes-dashboard \
  --create-namespace \
  --namespace kubernetes-dashboard \
  --set app.scheduling.nodeSelector."kubernetes\.io/hostname"=$1 \
  --set auth.nodeSelector."kubernetes\.io/hostname"=$1 \
  --set api.nodeSelector."kubernetes\.io/hostname"=$1 \
  --set web.nodeSelector."kubernetes\.io/hostname"=$1 \
  --set metricsScraper.nodeSelector."kubernetes\.io/hostname"=$1 \
  --set kong.nodeSelector."kubernetes\.io/hostname"=$1 \
  --set persistence.enabled=false
