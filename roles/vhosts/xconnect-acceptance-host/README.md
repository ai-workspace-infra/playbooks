# Isolated acceptance host preparation

Caller: prepare_xconnect_acceptance_host.yml, exactly one IaC CMDB host.
Requires acceptance_only=true, expected instance ID, the provider's exact
/dev/disk/by-id/virtio-* data volume identity and size=25 GiB. Verifies resource,
capacity and existing filesystem before formatting. Existing non-ext4 data is
never overwritten; filesystem force=false. Persistently mounts the volume at
/data. Repeat runs retain data and should report zero changes.

No production service, DNS, cloud or credential changes. The first formatting
operation is authorized only for the newly allocated test volume. Keep the
volume and state for recovery; deletion requires separate precise authorization.
