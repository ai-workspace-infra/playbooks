# XWorkmate execution workers

This additive role is opt-in. It does not change existing Gateway, Bridge, desktop,
inventory, proxy, or credentials deployment. The route remains
App → Bridge → OpenClaw Gateway → execution worker. Gateway owns the parent task,
approvals and final artifact commitment. DSH ACP and SDK are separate worker
profiles. StdIO is attached to one transient systemd process per canonical run UUID;
there is no ACP/SDK daemon. OpenCode v2 is an authenticated loopback service.

## Release contract

Pinned sources are DSH `639ed015397290b3745d163aafe02ffee4aa3f84` and OpenCode
`35a41b5d53c71ae0337e614ec192fd5d1a5c7eb8` (v2 source, package version 2.0.22).
Deployment requires real immutable HTTPS tar.gz URLs and SHA256 values. Empty
inputs fail closed. Targets never run package-manager installation or build scripts.
Each archive root contains `bin/dsh` or `bin/opencode`, required runtime libraries,
licenses, and `runtime-manifest.json` with exact `engine` and `sourceRevision`.
Archives cannot contain absolute/traversal paths, special files or escaping links.

The repository supplies an actual candidate build entrypoint:

```sh
python3 scripts/build_xworkmate_workers.py \
  --dsh-repo /build/sources/deepseek-harness \
  --opencode-repo /build/sources/opencode \
  --linux-node-archive /build/verified/node-linux.tar.xz \
  --linux-node-sha256 VERIFIED_64_HEX_DIGEST \
  --pnpm /build/tools/bin/pnpm --bun /build/tools/bin/bun \
  --output /build/releases
```

Run this with Python 3.12+ only on a disposable Linux glibc x64/arm64 build host, with reviewed
dependency lifecycle scripts and existing build toolchains (including DSH native
addon build requirements). It uses `git archive` of the exact commits, pnpm 11.7.0
and Bun 1.4.2, frozen lockfiles, DSH's own full build and OpenCode's own CLI compiler.
It bundles a separately verified compatible Linux Node archive for DSH, and selects
the x64 baseline binary where appropriate. Ambient credentials, the caller's home,
working-tree files and existing auth caches are not copied. The manifest includes
the source revision. Actual generated archives and checksums are emitted; the
script neither invents URLs nor publishes them. Upstream dependency/build downloads
still require supply-chain review; this is not a claim of bit-for-bit reproducibility.

The script runs keyless DSH ACP/SDK configuration inspection and OpenCode version
smoke checks. Those checks do not prove real stdio boot, native tool operation,
provider adapter availability offline, cancellation, or inference. Before publishing
any candidate, test a fresh Linux runtime with the final archive and restricted
egress: ACP initialize/newSession/set_config_option/close, SDK initialize/shutdown,
OpenCode authenticated `/api/info` and provider loading, followed by separately
authorized per-model inference. If provider resolution needs registry downloads,
the artifact/cache must be completed in the release pipeline; broad registry egress
must not be added to make runtime installation work. This lane has not built Linux
archives or published a release.

## Inputs and runtime credentials

Supply inventory variables outside source control. This example intentionally has
no real URL, checksum, token, deployment host or private domain:

```yaml
xworkmate_workers_enabled: true
xworkmate_workers_gateway_user: openclaw # existing Gateway account
xworkmate_workers_gateway_tasks_root: /srv/openclaw/workspace/tasks
xworkmate_workers_llm_base_url: https://aggregate.example/v1
xworkmate_workers_llm_model: operator-declared-model-id
xworkmate_workers_model_context_window: 32768 # actual model catalog contract
xworkmate_workers_model_max_tokens: 8192
xworkmate_workers_egress_cidrs: [] # explicit aggregator and DNS CIDRs required
xworkmate_workers_dsh_artifact_url: "" # immutable release URL required
xworkmate_workers_dsh_artifact_sha256: "" # generated archive digest required
xworkmate_workers_opencode_artifact_url: ""
xworkmate_workers_opencode_artifact_sha256: ""
xworkmate_workers_verify_models: false # true: catalog GET, no inference
xworkmate_workers_opencode_shell_allowlist: [] # default deny
```

Model ID and HTTPS `/v1` base URL are mandatory, without embedded credentials,
query or fragment. Exactly the declared `xworkmate` provider/model is configured
for DSH ACP, SDK, OpenCode and the supplied Gateway merge fragment. Every additional
model must be individually declared with real catalog metadata and tested; a model
catalog entry alone is not evidence of tools/streaming/context support.

Vault Agent/operator supplies `/run/xworkmate-workers/model.env` containing only
`XWORKMATE_LLM_API_KEY`, and `/run/xworkmate-workers/opencode.env` containing only
`OPENCODE_PASSWORD`. These are private regular 0600 files, no symlinks. For the
Gateway plugin to read these references directly, render files owned by its UID
and make the parent directory traversable by that account. Root-owned files are
accepted by the systemd launcher but cannot be read by a non-root Gateway plugin.
Use literal one-line values, optionally quoted; no shell expansion or extra env
keys. This role never materializes or reads Vault tokens. Systemd reads env files
before applying the service mount namespace; agents cannot open the credential
directory from inside their runtime. Credentials remain in process environment,
so this is an execution boundary for one trusted account, not tenant isolation.

Fixed adapter contracts are `/var/lib/xworkmate-workers/runs`, port 4097,
`/usr/local/libexec/xworkmate-worker-launch`, and the two `/run` references above.
An incompatible override fails before filesystem changes. The maximum run time
defaults to 900 seconds and cannot exceed the adapter's 900000ms timeout.

## Permissions and isolation

Preinstall Python 3.9+, sudo, systemd, bubblewrap, POSIX ACL tooling and find. The role
does not install a toolchain on production hosts. It requires Linux systemd and
tests kernel cgroup IP deny enforcement with a real reachable listener; missing BPF
support fails deployment. Both runtimes allow only localhost plus explicit CIDRs.
Include the required DNS resolver addresses and all current aggregator addresses;
DNS re-addressing requires explicit revalidation. IP rules do not restrict URL paths
on an allowed address, and localhost may contain other services. This is not an
internet proxy or a secure multi-tenant sandbox.

DSH receives a dedicated run home/workspace/output, one writable namespace, bounded
CPU/memory/process/runtime resources and a control-group kill boundary. Sibling run
directories are hidden. Default DSH vendor providers and plugin-manager surfaces
are disabled. ACP retains permission requests, which the current Gateway adapter
rejects until its approval callback is implemented. SDK uses `approval.policy=never`
with workspace-write: it cannot approve escalation; this does not prohibit safe
workspace tools. MCP servers must not be injected by untrusted clients.

OpenCode's service account can write its own state and only the declared Gateway
`tasks` subtree, not arbitrary host homes. The role grants physical (no symlink
following) access ACLs recursively only inside that explicit share, and directory
default ACLs for future prepared tasks. The share must already be a trusted Gateway
workspace; do not point it at a broad home or checkout. Gateway prepare uses recursive
mkdir and inherits ACLs. Existing task directories are covered by this scoped ACL
operation. This one-account service can access other prepared task directories at
the OS layer; per-session file permissions deny `external_directory`. Use separate
users/containers and per-task services if mutually untrusted tenants are required.

OpenCode uses the statically bundled native provider package
`@opencode/ai/providers/openai-compatible` (upstream `core/src/provider.ts` builtin
registry). The `aisdk:` adapter path would dynamically install an npm package and is
not suitable for the restricted runtime. Keep native provider loading in the Linux
acceptance gate.

Native OpenCode v2 ordered permissions deny all actions, then allow `read`/`edit`
with resource `*` relative to the current prepared task location, while denying
external directories. Shell, git, build, tests, web fetch and MCP default to deny.
To authorize reviewed commands, supply explicit rules:

```yaml
xworkmate_workers_opencode_shell_allowlist:
  - {action: shell, resource: 'git status --short', effect: allow}
```

Shell resources are command strings, not filesystem paths. Broad patterns such as
`go test*` can authorize command variations and executing workspace code; review
them against redirects, shell expansion and repository trust. The role rejects
rules for other actions and the global `*` shell pattern. It cannot make a broad
operator command pattern intrinsically safe. Built-in plugins remain upstream code;
`plugins: []` means no additional configured plugin packages.

## Gateway merge and deployment

The role emits three non-secret merge fragments:

- `/etc/xworkmate-workers/gateway-worker-runtime.json`: merge its `workerRuntime`
  into `plugins.entries.openclaw-multi-session-plugins.config`; includes bounded
  command/env references, prepared-output collection and matching timeout.
- `/etc/xworkmate-workers/gateway-tool-opt-in.json`: union `xworkmate_worker` into
  the existing `tools.allow` list; preserve every other tool setting and allow entry.
  The plugin manifest must declare `contracts.tools` and
  `toolMetadata.xworkmate_worker.optional: true`. Validate that plugin tool discovery
  exposes this tool after opt-in.
- `/etc/xworkmate-workers/gateway-model-service.json`: merge models/secrets and the
  defaults/main model into the existing complete OpenClaw configuration. Match the
  `main` agent by ID; do not replace the entire `agents.list`, its skills, plugins,
  auth, proxy, workspace or unrelated agents. Review/remove inherited vendor model
  fallbacks for this route. The existing Gateway role is not rewritten automatically.

The main-model fragment uses OpenClaw's supported env SecretRef
`{source: env, provider: default, id: XWORKMATE_LLM_API_KEY}`. Add the same Vault-rendered
model env file to the actual Gateway systemd unit's `EnvironmentFile`, using its
system/user service manager as appropriate; validate the merged config with the
pinned OpenClaw runtime before a separately authorized Gateway restart. Existing
provider OAuth caches must not silently become fallbacks. No raw key belongs in
OpenClaw JSON or in this repository. A live model smoke gate must confirm the actual
selected provider for Gateway and each worker profile.

```sh
ansible-playbook -i INVENTORY deploy_xworkmate_workers.yml --syntax-check
ansible-playbook -i INVENTORY deploy_xworkmate_workers.yml --check --diff -l HOST
ansible-playbook -i INVENTORY deploy_xworkmate_workers.yml -l HOST
```

Check mode renders planning but cannot prove remote artifact extraction, credential
availability, kernel enforcement, model capability, or service readiness. Normal
deploy validates env metadata without logging it, downloads with checksum, checks
archive safety/source manifest, proves IP deny, installs restricted sudoers and
starts the service. Readiness requires unauthenticated `/api/info` = 401, then
authenticated identity with version/pid. Optional `--models` / verify_models only
checks the global aggregator catalog. It does not spend inference tokens.

After catalog preflight, explicitly run the real model compatibility matrix (one
model at a time): text/stream/tool call/long context, ACP and SDK run/stop/failure,
OpenCode v2 prompt/cancel/diff, parent-child completion closure and durable artifact
receipts, then App → Bridge → Gateway end-to-end. The role does not call a model
or enable live checks by default. Static macOS validation cannot prove Linux mounts,
BPF, service permissions or upstream runtime boot.

## Artifact and lifecycle contract

Gateway invokes only `sudo -n /usr/local/libexec/xworkmate-worker-launch dsh-acp UUID`
or `dsh-sdk UUID`. Fixed argv count, profile and lowercase canonical UUID are
revalidated inside the root-owned launcher; arbitrary commands/paths are rejected.
Each launch must use a new attempt UUID; namespaces are root-owned and never reused
for privileged initialization. Restoring persisted ACP history across attempts is a
separate recovery contract, not claimed by this deployment launcher. Output artifacts
belong in the run's `workspace/output` directory. After the owned
unit exits, `export PROFILE UUID` returns JSON with `profile`, `runId`, and
`artifacts[]` entries `{relativePath,size,sha256,mimeType,contentBase64}`. Export
rejects active units, symlinks, special files, more than eight files or the configured
raw byte cap (1MiB default; 32MiB maximum). Gateway commits these bytes into the
prepared artifact directory. OpenCode v2 diff/test-log receipts are produced through
the Gateway adapter's versioned HTTP export, not arbitrary DSH filesystem access.

Cancellation uses a separate restricted capability:

```sh
sudo -n /usr/local/libexec/xworkmate-worker-launch cancel dsh-acp CANONICAL_RUN_UUID
```

Replace only the profile/UUID with that owned worker scope (`dsh-sdk` is also
supported). This command does not load model configuration or read any runtime
credentials. It acquires the same root-owned per-scope advisory lock as launch, writes a
root-owned cancellation tombstone, runs only
`systemctl stop xworkmate-PROFILE-UUID.service`, then verifies `is-active` reports
`inactive` or notloaded (`unknown`, exit 3/4). The complete operation is bounded
by 65 seconds, including lock admission. Exit zero emits exactly `{profile,runId,workerStopped:true}`; timeout,
active/failed/deactivating state, unsafe marker or other stop/verification error
returns nonzero and must block artifact export and parent completion. Already
notloaded scopes are idempotently accepted after the fence is present.

Gateway first disposes stdin/launcher and proves wrapper exit, then independently
runs this scoped cancel command even if disposal failed, allowing a 70-second
command budget. It validates the exact receipt scope. Any wrapper/stop cleanup
failure remains a failed closure, even if the other cleanup step succeeded. A
launcher exit alone, including SIGKILL after one second, is not evidence that the
systemd worker cgroup stopped. Runtime credentials expiring/removing cannot block
this cancellation capability.

Launch holds the per-scope advisory lock across repeated tombstone/namespace
admission checks, process creation and reliable unit registration: `systemctl show
LoadState=loaded`, or a successful exit from `systemd-run --wait` for a completed
short task. Registration has a 10-second cap; failure fences/stops the pending
scope before releasing the lock. Cancel acquires this same lock before it fences
and stops, so it cannot return notloaded while an admitted launch is between the
check and systemd registration. This ordering is exercised by a threaded fixture
using real advisory locks and scripted systemd replies.

A hard-killed launcher releases its lock without running cleanup. To cover an
already queued manager request in that case, new DSH transient units also carry a
manager-side negative `ConditionPathExists` for the exact root-owned tombstone.
The marker remains present when a delayed transient start reaches the manager,
so it cannot execute the worker. The launcher also rejects a cancelled UUID before
preflight. The condition is a separate defense; a one-time file check alone does
not close this registration race. Transient conditions are supported by upstream
[systemd's transient settings contract](https://github.com/systemd/systemd/blob/main/docs/TRANSIENT-SETTINGS.md).
Tombstones are retained; retries use new attempt UUIDs. Linux acceptance must
exercise pre-initialize cancellation, delayed registration, hard-killed launchers,
credential removal, timeout and child-process reaping. These local fixture tests
do not prove real systemd behavior.

Cancellation stops the full DSH control group; SDK cancellation abandons the whole
owned runtime because upstream SDK has no per-turn cancel. Never declare parent
completion on a bare `agent_end` before worker exit and artifact collection. Stop:

```sh
ansible-playbook -i INVENTORY deploy_xworkmate_workers.yml -l HOST \
  -e xworkmate_workers_action=stop
```

Stop disables OpenCode and stops managed DSH units; it preserves state, release
directories and prepared artifacts. For rollback, stop/drain first, back up OpenCode
state and verify database/config downgrade compatibility, then redeploy explicitly
recorded previous revision+archive+digest and matching adapter contract. Retain the
previous Gateway config, unit env references and plugin release for coordinated
rollback. No automatic destructive cleanup or schema downgrade is performed.
