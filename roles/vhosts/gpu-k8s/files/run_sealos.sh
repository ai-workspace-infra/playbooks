#!/bin/bash
set -e

REGISTRY="$1"
K8S_VERSION="$2"
CILIUM_VERSION="$3"
HELM_VERSION="$4"
MASTERS="$5"
NODES="$6"
SSH_USER="$7"
ANS_USER="$8"
CMD_ENV=$(echo "$9" | base64 -d)
KUBEADM_CMD=$(echo "${10}" | base64 -d)

sudo sealos run \
  ${REGISTRY}/kubernetes:${K8S_VERSION} \
  ${REGISTRY}/cilium:${CILIUM_VERSION} \
  ${REGISTRY}/helm:${HELM_VERSION} \
  --masters ${MASTERS} \
  --nodes ${NODES} \
  --user ${SSH_USER} \
  --pk /home/${ANS_USER}/.ssh/id_rsa \
  --env "${CMD_ENV}" \
  --cmd "${KUBEADM_CMD}"
