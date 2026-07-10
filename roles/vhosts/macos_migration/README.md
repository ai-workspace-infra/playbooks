# macos_migration

Backs up, restores, and migrates a macOS user's applications and directories
across macOS versions (and across Intel/Apple Silicon), so a machine can be
rebuilt on a new OS install or a new Mac without redoing setup by hand.

Always runs local-only (`connection: local`, `become: no`) against the Mac
it's invoked on — it never manages a remote host directly, it only pulls
data from one over ssh in the `migrate.yml` case.

## What it captures

- **Homebrew** (`macos_migration_brew_capture_mode`, default `"bundle"`) —
  unrelated to `/Applications`; this is about the Homebrew installation
  itself. Options:
  - `"bundle"`: `brew bundle dump` snapshots taps/formulae/casks/`mas` apps
    into a `Brewfile`; restore runs `brew bundle install` from it. Small, and
    safe across macOS version/CPU architecture changes since everything is
    reinstalled fresh on the new machine.
  - `"directory"`: a full byte-for-byte `ditto` copy of the Homebrew prefix
    directory (`brew --prefix`, e.g. `/opt/homebrew` or `/usr/local` —
    override with `macos_migration_brew_prefix`) — every installed Cellar
    formula, Caskroom app, and download cache, restored instantly with no
    re-fetching. Much larger, and the compiled binaries are **not** portable
    across CPU architecture (often not across macOS major versions either).
  - `"both"`: capture/restore both.
  - `"none"`: skip Homebrew entirely.
- **Apps under `macos_migration_applications_dir`** (default `/Applications`).
  Which ones, via `macos_migration_apps_auto_discover`/`macos_migration_apps`/
  `macos_migration_apps_exclude` (combinable):
  - **Auto-discover** (`macos_migration_apps_auto_discover`, default
    `false`): when `true`, scans the top level of `macos_migration_applications_dir`
    for every `*.app` bundle.
  - **Manual list** (`macos_migration_apps`): explicit names, always added
    on top of auto-discover — not limited to `*.app` bundles, any top-level
    folder under `macos_migration_applications_dir` works (e.g. a
    non-bundle game install folder).
  - **Exclude override** (`macos_migration_apps_exclude`): exact names
    dropped from the final set *after* the two above are combined — use it
    to skip specific huge/unwanted entries that auto-discover picked up.

  What happens to that resolved set is controlled separately by
  `macos_migration_apps_capture_mode`:
  - `"list"` (default, lightweight): only the *names* go into
    `manifest.json` — no bytes are copied. Good for apps you'd reinstall via
    Homebrew/the App Store anyway; `restore.yml` just reports the list.
  - `"copy"`: `ditto`-copies each resolved app's actual bundle/folder into
    the backup (preserves code-signing/xattrs). Heavy — can be many GB per
    app (some non-`.app` installs, e.g. games, are 100+ GB) — counted in the
    pre-flight space check. Use only for apps that can't be trivially
    reinstalled otherwise.
- **Custom directories** (`macos_migration_directories`): arbitrary
  files/directories (app support data, dotfiles, prefs plists, etc.), each
  `{name, path}`, copied with `rsync -a --no-specials` and restored to their
  original `path`. Each entry can also carry `excludes:` (rsync `--exclude`
  patterns), added on top of the global `macos_migration_directories_exclude`
  list. Patterns without a `/` match the basename at any depth (e.g.
  `node_modules` excludes it everywhere under the source, not just at the
  top level) — the defaults cover the common regenerable junk that has no
  business in a config/dotfile backup: `node_modules`, `.build`/`build`,
  `dist`, `.next`, `target`, `.cache`, `__pycache__`, `.venv`, `*.dSYM`, etc.
  Set `macos_migration_directories_exclude: []` to disable. The pre-flight
  space estimate for these entries is excludes-aware (`rsync -an --stats`,
  not `du`), so it reflects what will actually be copied, not the raw
  on-disk size.

## Actions

Select one via `tasks_from` when including the role:

| `tasks_from` | What it does |
| --- | --- |
| `backup.yml`  | Pre-flight space check → capture Brewfile/apps/directories → `manifest.json` → tar into `macos_migration_backup_archive`, under a `<macOS version>-<date>` subdirectory of `macos_migration_backup_root`. |
| `restore.yml` | Pre-flight space check → extract `macos_migration_restore_archive` → `brew bundle install` → restore apps/directories. |
| `migrate.yml` | `rsync` pull `macos_migration_source_archive` from `macos_migration_source_host` (produced by `backup.yml` there), then run `restore.yml`. |

`main.yml` (the default with no `tasks_from`) only asserts Darwin and prints
this table — it takes no destructive action on its own.

## Pre-flight space check

Both `backup.yml` and `restore.yml` refuse to run if the destination
filesystem doesn't have enough free space, computed as
`estimated_payload_size * macos_migration_min_free_space_ratio`
(default `1.5`; restore uses `macos_migration_restore_min_free_space_ratio`,
default `3.0`, since it's estimating off the compressed archive rather than
real directory sizes). Set `macos_migration_skip_space_check: true` to
bypass it.

## Where backups land

`macos_migration_backup_root` (default `~/.local/state/xworkspace/macos-migration`)
is the base directory — point it at an external drive, e.g.
`-e macos_migration_backup_root=/Volumes/Backup`. Each run creates a
`<macOS product version>-<backup date>` subdirectory under it (e.g.
`15.5-20260710`), frozen once at the start of `backup.yml`, so runs from
different machines/OS versions land in visibly separate folders instead of
one flat pile:

```
/Volumes/Backup/
└── 15.5-20260710/
    └── macos-migration-20260710T140529.tar.gz
```

Multiple backups on the same day share the subdirectory but keep distinct
timestamped filenames, so nothing gets silently overwritten.

## Example

```yaml
# group_vars/localhost or -e on the CLI
macos_migration_backup_root: "/Volumes/Backup"
macos_migration_apps_auto_discover: true    # sweep all of /Applications
macos_migration_apps:
  - "Transmit.app"                          # always included, redundant here but harmless
macos_migration_apps_exclude:
  - "Xcode.app"                             # drop specific apps the scan picked up
macos_migration_directories:
  - name: iterm2-prefs
    path: "{{ ansible_env.HOME }}/Library/Preferences/com.googlecode.iterm2.plist"
  - name: ssh
    path: "{{ ansible_env.HOME }}/.ssh"
  - name: workspaces
    path: "{{ ansible_env.HOME }}/workspaces"
    excludes:
      - cloud-neutral-toolkit    # e.g. a known-stale/duplicate checkout
```

```bash
# On the old Mac
ansible-playbook setup-macos-migration-backup.yml

# Copy the resulting archive to the new Mac (or let migrate.yml pull it), then
ansible-playbook setup-macos-migration-restore.yml \
  -e macos_migration_restore_archive=/path/to/macos-migration-*.tar.gz

# Or, straight from the new Mac, pulling from the old one over ssh:
ansible-playbook setup-macos-migration-migrate.yml \
  -e macos_migration_source_host=old-mac.local \
  -e macos_migration_source_archive=/Users/me/.local/state/xworkspace/macos-migration/macos-migration-20260101T000000.tar.gz
```

## Notes

- Restoring across CPU architectures: copied `.app` bundles and a
  `"directory"`-mode Homebrew restore built for the other architecture will
  not run; prefer `"bundle"` mode (and the Brewfile path for apps) when
  moving Intel → Apple Silicon or vice versa. `restore.yml` warns but does
  not block when it detects an arch/OS mismatch.
- `macos_migration_restore_overwrite` (default `false`): existing
  destinations are left untouched and skipped rather than clobbered unless
  set `true`.
- Restoring a `"directory"`-mode Homebrew capture onto a machine that has
  never had Homebrew installed needs to create the prefix's parent (e.g.
  `/opt`) for the first time, which is root-owned by default — that one step
  runs with `become: true` even though the rest of the role is `become: no`,
  matching what the official Homebrew installer does.
- Requires `ansible.posix.synchronize` (already used elsewhere in this repo,
  e.g. `roles/vhosts/ai-workspace/tasks/migrate.yml`) for `migrate.yml`.
