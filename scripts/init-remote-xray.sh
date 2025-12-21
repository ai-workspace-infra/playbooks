#!/usr/bin/env bash
set -e

NODE_IP="$1"
USER="ubuntu"

ssh $USER@$NODE_IP "
  sudo apt purge curl unzip -y
  curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh -o /tmp/install-release.sh
  sudo bash /tmp/install-release.sh
"

scp /etc/ssl/svc.*   $USER@$NODE_IP:/tmp/
scp config.json      $USER@$NODE_IP:/tmp/

ssh $USER@$NODE_IP "
  sudo cp /tmp/svc.* /etc/ssl/
  sudo cp /tmp/config.json /usr/local/etc/xray/config.json

  sudo chown root:root    /etc/ssl/svc.plus.pem
  sudo chmod 644          /etc/ssl/svc.plus.pem

  sudo chown root:nogroup /etc/ssl/svc.plus.key
  sudo chmod 640          /etc/ssl/svc.plus.key

  sudo chown root:root    /usr/local/etc/xray/config.json
  sudo chmod 644          /usr/local/etc/xray/config.json

  sudo systemctl restart xray
  sudo systemctl status xray
  sudo journalctl -fu xray
"
