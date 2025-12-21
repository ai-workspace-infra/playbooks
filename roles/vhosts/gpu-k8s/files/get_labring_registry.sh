#!/bin/bash
# Determine the appropriate LabRing container registry based on geolocation.
# Defaults to the Chinese mainland registry.
REGISTRY_CN="registry.cn-shanghai.aliyuncs.com/labring"
REGISTRY_INT="labring"
# Query external service for country code; fall back to CN on failure.
COUNTRY=$(curl -fsSL https://ipapi.co/country/ 2>/dev/null || echo "")
if [ "$COUNTRY" = "CN" ]; then
  echo "$REGISTRY_CN"
else
  echo "$REGISTRY_INT"
fi
