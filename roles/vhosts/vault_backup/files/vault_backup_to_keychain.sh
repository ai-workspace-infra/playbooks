#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Vault 全量只读导出 -> macOS Keychain
#
# 用法:
#   export VAULT_ADDR=https://vault.svc.plus
#   export VAULT_TOKEN=<admin token>        # 或已有 ~/.vault-token
#   ./scripts/backup/vault_backup_to_keychain.sh
#
# 产出:
#   1. Keychain 条目 (service=vault-full-backup, account=<VAULT_ADDR>-<日期>)
#      内容 = gzip + base64 后的完整导出
#   2. 落盘副本 ~/vault-backups/vault-dump-<日期>.json.gz (0600), 供核对后手动删除
#
# 只读: 全程只调用 vault kv list / kv get / policy read / read, 不做任何写操作。
# =============================================================================

STAMP="$(date +%Y%m%d-%H%M%S)"
ADDR="${VAULT_ADDR:-https://vault.svc.plus}"
BACKUP_DIR="${HOME}/vault-backups"
RAW="${BACKUP_DIR}/vault-dump-${STAMP}.json"
GZ="${RAW}.gz"
KC_SERVICE="vault-full-backup"
KC_ACCOUNT="${ADDR#https://}-${STAMP}"

umask 077
mkdir -p "${BACKUP_DIR}"

echo "==> 导出 Vault (只读)"
python3 "$(dirname "$0")/vault_full_export.py" "${RAW}"

echo "==> 校验导出完整性"
secret_count=$(python3 -c "import json;print(json.load(open('${RAW}'))['meta']['secret_count'])")
failed_count=$(python3 -c "import json;print(json.load(open('${RAW}'))['meta']['failed_count'])")
echo "    secrets=${secret_count} failed=${failed_count}"
if [ "${secret_count}" -eq 0 ]; then
  echo "!! 导出到 0 个密钥, 判定为失败, 中止 (不写入 Keychain)" >&2
  exit 1
fi
if [ "${failed_count}" -ne 0 ]; then
  echo "!! 有 ${failed_count} 个路径读取失败, 备份不完整, 中止" >&2
  echo "   请检查 token 权限后重试; 未完整的备份不应被当作可依赖的备份。" >&2
  exit 1
fi

echo "==> 压缩"
gzip -9 -c "${RAW}" > "${GZ}"
rm -f "${RAW}"
sha="$(shasum -a 256 "${GZ}" | awk '{print $1}')"
echo "    ${GZ}  sha256=${sha}"

echo "==> 写入 Keychain (service=${KC_SERVICE}, account=${KC_ACCOUNT})"
# 注意: security 不支持从 stdin 读取 -w, 因此 base64 内容会短暂出现在进程参数中。
# 在单用户 Mac 上风险很低, 但如果你介意, 可以只把 GZ 文件留在磁盘并自行加密保管。
security add-generic-password \
  -a "${KC_ACCOUNT}" \
  -s "${KC_SERVICE}" \
  -j "Vault full export ${STAMP} from ${ADDR}; sha256=${sha}" \
  -w "$(base64 < "${GZ}")" \
  -U

echo "==> 回读校验 (比对 sha256, 不打印内容)"
security find-generic-password -a "${KC_ACCOUNT}" -s "${KC_SERVICE}" -w \
  | base64 -d | shasum -a 256 | awk '{print $1}' > /tmp/.vault_kc_sha
if [ "$(cat /tmp/.vault_kc_sha)" = "${sha}" ]; then
  echo "    ✅ Keychain 内容与源文件一致"
else
  echo "    ❌ 回读校验不一致, 请勿依赖此备份" >&2
  rm -f /tmp/.vault_kc_sha
  exit 1
fi
rm -f /tmp/.vault_kc_sha

echo
echo "完成。"
echo "  Keychain: service=${KC_SERVICE} account=${KC_ACCOUNT}"
echo "  落盘副本: ${GZ}  (核对无误后可自行删除)"
echo
echo "恢复时读取:"
echo "  security find-generic-password -a '${KC_ACCOUNT}' -s '${KC_SERVICE}' -w | base64 -d | gunzip > restore.json"
