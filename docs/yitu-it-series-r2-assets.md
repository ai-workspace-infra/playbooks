# yitu-it-series R2 assets

This runbook migrates the local Google Drive `自媒体` directory to Cloudflare R2 for the Docusaurus AI Native knowledge base.

## Architecture

```text
GitHub -> Docusaurus -> Cloudflare Pages -> ebook.svc.plus

Google Drive local folder
  -> rclone
  -> Cloudflare R2 bucket: yitu-it-series
  -> R2 custom domain: img.svc.plus
  -> Docusaurus Markdown image URLs
```

## Source and target

```text
Local source:
/Users/shenlan/Library/CloudStorage/GoogleDrive-haitaopanhq@gmail.com/我的云端硬盘/自媒体

R2 bucket:
yitu-it-series

Public asset domain:
https://img.svc.plus
```

## Recommended object layout

```text
yitu-it-series/
├── covers/
├── xiaohongshu/
├── observability/
├── storage/
├── networking/
├── ai-native/
├── security/
├── platform-engineering/
└── ebook-assets/
```

Use stable, semantic paths for published content:

```text
covers/season-1/single-machine-to-platform-cover-v1.png
security/least-privilege/root-to-rootless-v1.png
ai-native/agentic-infra/ai-native-platform-v1.png
ebook-assets/diagrams/cloud-native-to-ai-native-v1.png
```

Prefer versioned object names instead of overwriting an already published image. This keeps Cloudflare CDN behavior predictable and preserves old articles.

## Cloudflare API token

Create two token scopes if possible:

```text
Bootstrap token:
- Account: Cloudflare R2: Edit
- Zone: DNS: Edit, Zone: Read for svc.plus
- Used only for bucket/custom-domain setup

Long-running R2 S3 token:
- R2 Object Read & Write
- Scope limited to bucket yitu-it-series
- Used by rclone sync
```

Required environment variables:

```bash
export CF_ACCOUNT_ID="..."
export CF_ZONE_ID="..."
export CLOUDFLARE_API_TOKEN="..."
export R2_ACCESS_KEY_ID="..."
export R2_SECRET_ACCESS_KEY="..."
```

## Commands

From the playbooks directory:

```bash
cd /Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks
chmod +x scripts/sync-yitu-it-series-r2.sh

scripts/sync-yitu-it-series-r2.sh doctor
scripts/sync-yitu-it-series-r2.sh create-bucket
scripts/sync-yitu-it-series-r2.sh configure-rclone
scripts/sync-yitu-it-series-r2.sh dry-run
scripts/sync-yitu-it-series-r2.sh copy
scripts/sync-yitu-it-series-r2.sh check
scripts/sync-yitu-it-series-r2.sh tree
scripts/sync-yitu-it-series-r2.sh configure-custom-domain
```

Use `copy` for the first production migration when preserving all historical remote files matters. Use `sync` for steady-state mirroring after the source layout is stable.

## Performance profile

Default large AI image profile:

```bash
export RCLONE_TRANSFERS=16
export RCLONE_CHECKERS=32
export RCLONE_S3_UPLOAD_CUTOFF=128M
export RCLONE_S3_CHUNK_SIZE=128M
```

Many small images:

```bash
export RCLONE_TRANSFERS=32
export RCLONE_CHECKERS=64
```

Large source files such as PSD/video:

```bash
export RCLONE_TRANSFERS=4
export RCLONE_CHECKERS=16
export RCLONE_S3_UPLOAD_CUTOFF=256M
export RCLONE_S3_CHUNK_SIZE=256M
```

## Incremental sync

Install a macOS launchd sync job:

```bash
cd /Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks
scripts/sync-yitu-it-series-r2.sh install-launchd
launchctl list | grep yitu-it-series
```

Remove it:

```bash
scripts/sync-yitu-it-series-r2.sh uninstall-launchd
```

## R2 custom domain

Target:

```text
img.svc.plus -> R2 bucket yitu-it-series
```

The script calls the Cloudflare R2 custom domain API:

```bash
scripts/sync-yitu-it-series-r2.sh configure-custom-domain
```

Recommended Cloudflare cache rule:

```text
If hostname equals img.svc.plus:
- Cache eligible
- Edge TTL: 30 days or longer
- Browser TTL: 7-30 days, or respect origin
```

## Docusaurus references

Markdown:

```md
![AI Native 基础设施演进](https://img.svc.plus/ai-native/ai-native-infra-cover-v1.png)
![最小权限演进](https://img.svc.plus/security/least-privilege-cover-v1.png)
```

MDX:

```mdx
<img
  src="https://img.svc.plus/platform-engineering/platform-engineering-roadmap-v1.png"
  alt="Platform Engineering Roadmap"
  loading="lazy"
/>
```

Front matter:

```md
---
title: AI Native 基础设施演进
description: 从云原生到 AI Native 的平台工程知识库
image: https://img.svc.plus/covers/ai-native-infra-cover-v1.png
---
```

## AI Native knowledge-base practices

- Keep Docusaurus focused on Markdown, MDX, navigation, SEO, and search.
- Keep heavy generated images and ebook assets in R2.
- Reference published assets with absolute `https://img.svc.plus/...` URLs.
- Keep object names immutable after publication; publish revisions with `-v2`, `-v3`.
- Run `rclone check` before replacing local Markdown image references.
- Keep raw generation artifacts separate from article-ready assets when possible.
- Use topic directories that match the ebook taxonomy so future RAG/vector indexing can attach image context to chapters.
