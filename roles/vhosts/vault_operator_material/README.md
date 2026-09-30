# Vault migration operator files

`node-operator-prep` creates `/etc/vault.d/operator` (root, `0700`), two
root-readable examples (`vault-token.example`, `unseal-share.example`), and
empty live `vault-token` and `unseal-share` files (root, `0600`). Reruns do not
overwrite existing files. The role checks metadata without reading secret
contents. It succeeds only after both live files are regular, nonempty,
root-owned, and mode `0600`.

An authorized operator fills the files directly on `vault-shared-0` through
the approved secure node access path, for example in an interactive root
session:

```bash
sudoedit /etc/vault.d/operator/vault-token
sudoedit /etc/vault.d/operator/unseal-share
```

Each live file contains one value and a trailing newline. Use an approved
operator token, not a new root token. Use one existing unseal share from the
source cluster; a joining Raft node must not be initialized. Do not paste
either value into Git, inventory, workflow inputs, or chat. The operator
uses the files locally for manual unseal and peer inspection; CI checks only
file metadata.
