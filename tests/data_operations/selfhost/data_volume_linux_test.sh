#!/usr/bin/env bash
# Disposable GitHub-hosted runner only; never accepted as live PROD evidence.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-}" == github-hosted && "$(id -u)" == 0 ]]
test ! -e /data
root="$(cd "$(dirname "$0")/../../.." && pwd)"
fixture="$(mktemp -d)"
cp /etc/fstab "$fixture/fstab"
disk=''; other=''
link="/dev/disk/by-id/ci-web-saas-data-${GITHUB_RUN_ID:?}"
cleanup() {
  if mountpoint -q /data; then
    actual="$(findmnt -nro SOURCE --mountpoint /data)"
    [[ "$actual" != "$disk" && "$actual" != "$other" ]] || umount /data
  fi
  cp "$fixture/fstab" /etc/fstab
  rm -f "$link"
  [[ -z "$disk" ]] || losetup -d "$disk"
  [[ -z "$other" ]] || losetup -d "$other"
  rmdir /data 2>/dev/null || true
  rm -rf "$fixture"
}
trap cleanup EXIT
truncate -s 128M "$fixture/data.img" "$fixture/other.img"
disk="$(losetup --find --show "$fixture/data.img")"
other="$(losetup --find --show "$fixture/other.img")"
install -d /dev/disk/by-id
ln -s "$disk" "$link"
ROOT="$root" FIXTURE="$fixture" DEVICE_LINK="$link" python3 - <<'PY'
import json,os
from pathlib import Path
p=Path(os.environ['FIXTURE'])
role=os.environ['ROOT']+'/roles/web_saas_data_volume'
(p/'play.yml').write_text(json.dumps([{'hosts':'web-saas-prod','gather_facts':False,'roles':[role]}]))
(p/'vars.json').write_text(json.dumps(dict(web_saas_data_target_host='web-saas-prod',
 web_saas_data_environment='prod',web_saas_data_action='prepare_empty',
 web_saas_data_device_path=os.environ['DEVICE_LINK'],web_saas_data_allow_initialize=True,
 web_saas_data_filesystem_label='ws-prod-data')))
PY
execute() { ansible-playbook -i 'web-saas-prod,' --connection local "$fixture/play.yml" --extra-vars "@$fixture/vars.json"; }
execute
[[ "$(findmnt -nro TARGET --mountpoint /data)" == /data ]]
[[ "$(blkid -s LABEL -o value "$disk")" == ws-prod-data ]]
uuid="$(blkid -s UUID -o value "$disk")"
printf 'retained-data-fixture\n' >/data/retained.txt
execute
[[ "$(blkid -s UUID -o value "$disk")" == "$uuid" ]]
[[ "$(cat /data/retained.txt)" == retained-data-fixture ]]
ln -sfn "$other" "$link"
if execute; then echo 'Wrong disk was accepted at an existing mount' >&2; exit 1; fi
[[ -z "$(wipefs --no-act --noheadings "$other")" ]]
ln -sfn "$disk" "$link"
umount /data
printf 'pre-existing-root-files\n' >/data/root-data.txt
if execute; then echo 'Nonempty root mountpoint was accepted' >&2; exit 1; fi
[[ "$(cat /data/root-data.txt)" == pre-existing-root-files ]]
rm /data/root-data.txt
execute
[[ "$(cat /data/retained.txt)" == retained-data-fixture ]]
echo 'Disposable Linux disk verified: format, mount, retry, retained data and negative guards.'
