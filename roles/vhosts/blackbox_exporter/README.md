# Blackbox exporter downloads

The role downloads the architecture-specific release from GitHub by default.
It does not require `dl.svc.plus` to resolve on the target host.

For a host with access to the internal mirror, set:

```yaml
blackbox_download_use_mirror: true
blackbox_download_base_url: https://dl.svc.plus/prometheus/blackbox_exporter
blackbox_download_timeout: 30
```

A mirror download error falls back to `blackbox_download_fallback_base_url`.
An upstream failure still fails the role. Existing unpacked files are checked
against the installed binary on every run, so deleting the installed binary
does not require deleting the unpacked cache to repair the installation.
