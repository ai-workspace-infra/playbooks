#!/usr/bin/env python3
"""Pinned Playbooks owner. --check is a local, read-only declaration plan."""
import argparse
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
sys.dont_write_bytecode = True

import yaml

SHA = r"[0-9a-f]{40}"
HASH = r"[0-9a-f]{64}"
IMAGE = r"[a-z0-9][a-z0-9.-]*(?::[0-9]+)?/[a-z0-9][a-z0-9._/-]*(?::[A-Za-z0-9_][A-Za-z0-9_.-]*)?@sha256:[0-9a-f]{64}"
NAME = r"[A-Za-z][A-Za-z0-9_.-]*"
SERVICES = {"accounts": "accounts", "billing-service": "billing", "portal": "console",
            "content-service": "content", "postgresql": "postgres"}


class Refused(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node):
    result = {}
    for key, value in node.value:
        name = loader.construct_object(key)
        require(isinstance(name, str) and name not in result, "YAML keys must be unique strings")
        result[name] = loader.construct_object(value)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def read_yaml(path):
    value = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
    require(isinstance(value, dict), "YAML document must be a mapping")
    return value


def fields(value, required, optional=()):
    require(isinstance(value, dict) and set(required) <= set(value)
            and set(value) <= set(required) | set(optional), "Missing or unknown contract fields")


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30)
    require(result.returncode == 0, "Cannot verify immutable checkout")
    return result.stdout.strip()


def checkout(root, sha):
    require(matches(SHA, sha), "A full lowercase 40-character commit SHA is required")
    require(Path(git(root, "rev-parse", "--show-toplevel")).resolve() == root,
            "Checkout must be the repository root")
    require(git(root, "rev-parse", "HEAD") == sha, "Checkout HEAD differs from the selected SHA")
    require(not git(root, "status", "--porcelain", "--untracked-files=all", "--ignored"),
            "Checkout must be clean including untracked and ignored files")


def inside(root, base, value):
    require(isinstance(value, str) and value and "\x00" not in value, "Invalid checkout file reference")
    candidate = Path(value)
    require(".." not in candidate.parts and ".git" not in candidate.parts,
            "File reference escapes the checkout")
    path = (base / candidate).resolve(strict=True)
    require(root in path.parents and path.is_file(), "File reference must stay inside the checkout")
    git(root, "ls-files", "--error-unmatch", "--", str(path.relative_to(root)))
    return path


def protected(path):
    path = Path(path).resolve(strict=True)
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and info.st_mode & 0o077 == 0, "Runtime vars must be operator-owned protected regular files")
    return path


def protected_ssh_key(path):
    require(isinstance(path, str) and path.startswith('/')
            and not any(char in path for char in ('\n', '\r', '\x00', '{{', '{%', '{#')),
            'SSH key reference must be an absolute local file path')
    try:
        resolved = Path(path).resolve(strict=True)
        info = resolved.stat()
    except OSError:
        require(False, 'SSH key file must be available to the operator')
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and info.st_mode & 0o077 == 0 and os.access(resolved, os.R_OK),
            'SSH key file must be operator-owned and protected')
    return str(resolved)


def merge(left, right):
    result = dict(left)
    for key, value in right.items():
        result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


def resolve_value(value, env):
    require(isinstance(value, str), "Compose value must be a string")
    def replace(match):
        name, operator, default = match.groups()
        require(name in env or operator == ":-", "Compose variable is absent from pinned env files")
        return env.get(name, default or "")
    value = re.sub(r"\$\{([A-Z][A-Z0-9_]*)(?:(:\?|:-)([^}]*))?\}", replace, value)
    require("$" not in value, "Unresolved Compose interpolation")
    return value


def selection(root, target, images, storage=False, listener=False):
    require(matches(NAME, target), "Invalid Doco-CD selection")
    manifest_path = inside(root, root, ".doco-cd." + target + ".yml")
    manifest = read_yaml(manifest_path)
    fields(manifest, ["name", "version", "working_dir", "compose_files", "env_files", "remove_orphans"],
           ["force_image_pull", "timeout"])
    require(matches(NAME, manifest["name"]) and manifest["version"] == "doco.v1"
            and manifest["remove_orphans"] is False,
            "Doco-CD requires a literal project name, doco.v1 and no orphan removal")
    work = (root / manifest["working_dir"]).resolve(strict=True)
    require(root in work.parents and work.is_dir() and ".." not in Path(manifest["working_dir"]).parts,
            "Doco-CD working directory escapes GitOps")
    env = {}
    require(isinstance(manifest["env_files"], list) and isinstance(manifest["compose_files"], list)
            and manifest["compose_files"], "Doco-CD must select explicit files")
    for name in manifest["env_files"]:
        for line in inside(root, work, name).read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, sep, value = line.partition("=")
            require(sep and matches(r"[A-Z][A-Z0-9_]*", key) and key not in env,
                    "Pinned Compose env keys must be unique")
            env[key] = value
    def load_compose(path, stack=()):
        require(path not in stack, "Cyclic Compose extends")
        document = read_yaml(path)
        require(not set(document).intersection({"include", "configs", "secrets", "x-example-only", "example_only"}),
                "Unsupported external Compose input")
        services = document.get("services", {})
        require(isinstance(services, dict), "Compose services must be a mapping")
        resolved = {}
        for name, service in services.items():
            require(isinstance(service, dict), "Invalid Compose service")
            if "extends" in service:
                extension = service["extends"]
                fields(extension, ["file", "service"])
                parent = load_compose(inside(root, path.parent, extension["file"]), (*stack, path))
                require(extension["service"] in parent, "Compose extends service is absent")
                service = merge(parent[extension["service"]], {k: v for k, v in service.items() if k != "extends"})
            resolved[name] = service
        return resolved
    services = {}
    for name in manifest["compose_files"]:
        services = merge(services, load_compose(inside(root, work, name)))
    require(set(services) == {"postgres"} if storage else set(SERVICES.values()) <= set(services),
            "Doco-CD selection lacks required services or storage is not PostgreSQL-only")
    resolved_images = {}
    for name, service in services.items():
        validate_ports(name, service.get('ports', []), env)
        require(not set(service).intersection({"build", "post_start", "pre_stop", "provider", "use_api_socket"})
                and not service.get("privileged") and not set(service).intersection({"pid", "ipc"})
                and ('network_mode' not in service or listener and name == 'migration' and service['network_mode'] == 'service:caddy'),
                "Unsupported candidate Compose execution capability")
        image = resolve_value(service.get("image", ""), env)
        require(matches(IMAGE, image), "All candidate dependency images must be digest pinned")
        resolved_images[name] = image
    for key, service in SERVICES.items():
        if service in services:
            require(resolved_images[service] == images[key], "Selected Compose image differs from release.services")
            files = services[service].get('env_file', [])
            files = [files] if isinstance(files, str) else files
            require(files == ['/etc/xcontrol/web-saas/secrets.env'],
                    'Core service env_file must consume exactly the native runtime owner output')
            explicit = services[service].get('environment', {})
            require(isinstance(explicit, dict) and not set(explicit).intersection({
                'POSTGRES_PASSWORD', 'ACCOUNT_PG_PASSWORD', 'AUTH_TOKEN_PUBLIC_TOKEN', 'AUTH_TOKEN_REFRESH_SECRET',
                'AUTH_TOKEN_ACCESS_SECRET', 'INTERNAL_SERVICE_TOKEN', 'DATABASE_URL', 'SUPABASE_CONNECT_URI', 'SUPABASE_CONNECT_URL'}),
                'Compose must not override native secret or database identity ownership')
    if not storage:
        for name in ("accounts", "billing"):
            flags = services[name].get("environment", {})
            require(isinstance(flags, dict) and str(flags.get("DATABASE_BACKGROUND_WRITERS")).lower() == "false"
                    and flags.get("DATABASE_RUNTIME_ROLE") == "primary",
                    "Candidate applications require managed native schema and disabled background writers")
    volumes = services["postgres"].get("volumes", [])
    bindings = [v for v in volumes if isinstance(v, dict) and v.get("type") == "bind"
                and v.get("target") == "/var/lib/postgresql/data"
                and v.get("bind", {}).get("create_host_path") is False]
    require(len(bindings) == 1 and isinstance(bindings[0].get("source"), str),
            "PostgreSQL requires the declared persistent bind, never an anonymous database volume")
    return {"target": target, "manifest": str(manifest_path), "images": resolved_images,
            "compose_project_name": manifest["name"],
            "postgres_data_path": bindings[0]["source"], **({'services': services} if listener else {})}


def validate_ports(service, ports, env):
    require(isinstance(ports, list), 'Compose ports must be an explicit list')
    for port in ports:
        if service == 'caddy':
            continue  # The only permitted public ingress; all dependencies below.
        if isinstance(port, str):
            value = resolve_value(port, env)
            require(matches(r'(?:127\.0\.0\.1|\[::1\]):[0-9]{1,5}:[0-9]{1,5}(?:/(?:tcp|udp))?', value),
                    'Only Caddy may publish publicly; dependencies require explicit loopback')
        else:
            require(isinstance(port, dict) and port.get('host_ip') in ('127.0.0.1', '::1')
                    and 'published' in port and 'target' in port,
                    'Only Caddy may publish publicly; dependencies require loopback host_ip')


def load_contract(config, gitops, owner, sha):
    checkout(gitops, sha)
    path = inside(gitops, gitops, config)
    contract = read_yaml(path)
    fields(contract, ["schema_version", "kind", "environment", "target", "release", "execution"],
           ["source", "example_only"])
    require(contract.get("example_only", False) is False, "Illustrative examples cannot execute")
    require(type(contract["schema_version"]) is int and contract["schema_version"] == 1
            and contract["kind"] == "selfhost-control-plane" and contract["environment"] in ("sit", "uat", "prod"),
            "Unsupported control-plane schema, kind or environment")
    fields(contract["execution"], ["playbooks_sha", "mode", "initialize"], ['storage'])
    execution = contract["execution"]
    require(execution["mode"] == "candidate" and type(execution["initialize"]) is bool,
            "Only candidate mode with explicit initialize eligibility is supported")
    checkout(owner, execution["playbooks_sha"])
    target, release = contract["target"], contract["release"]
    fields(target, ["host", "generation", "domain_base", "console_domain", "accounts_domain"], ['billing_domain'])
    require(all(matches(NAME, target[k]) for k in ("host", "generation", "domain_base", "console_domain", "accounts_domain")),
            "Target metadata must use literal names, never inventory patterns")
    if 'billing_domain' in target:
        billing_domain = target['billing_domain']
        hostname = r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        require(matches(hostname, billing_domain) and billing_domain.endswith('.' + target['domain_base'])
                and billing_domain not in (target['console_domain'], target['accounts_domain']),
                "Billing host must be a distinct literal hostname under the declared domain base")
    fields(release, ["snapshot_tag", "services", "accounts", "doco_cd"], ['billing'])
    if 'billing' in release:
        fields(release['billing'], ['source_sha', 'schema_file'])
        require(matches(SHA, release['billing']['source_sha']) and
                release['billing']['schema_file'] == 'sql/billing-service-schema.sql', 'Invalid Billing source artifact')
    require(matches(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", release["snapshot_tag"]), "Invalid snapshot tag")
    fields(release["services"], SERVICES)
    require(all(matches(IMAGE, value) for value in release["services"].values()), "Five digest-pinned service images are required")
    accounts = release["accounts"]
    fields(accounts, ["source_sha", "schema_sha256", "billing_schema_sha256", "migration_version", "business_tables"])
    require(matches(SHA, accounts["source_sha"]) and matches(HASH, accounts["schema_sha256"])
            and matches(HASH, accounts["billing_schema_sha256"]) and type(accounts["migration_version"]) is int
            and accounts["migration_version"] > 0, "Accounts source and native schema hashes are required")
    tables = accounts["business_tables"]
    require(isinstance(tables, list) and tables and all(matches(r"[a-z][a-z0-9_]*", t) for t in tables)
            and tables == sorted(set(tables)), "Native business table list must be sorted and unique")
    doco = release["doco_cd"]
    fields(doco, ["image", "repository", "target", "storage_target"])
    require(matches(IMAGE, doco["image"]) and doco["repository"] == "https://github.com/ai-workspace-infra/gitops.git"
            and doco["target"] != doco["storage_target"], "Doco-CD requires the canonical repository and immutable controller image")
    selections = [selection(gitops, doco["storage_target"], release["services"], True),
                  selection(gitops, doco["target"], release["services"])]
    require(selections[0]["compose_project_name"] == selections[1]["compose_project_name"],
            "Storage and application selections must share the Doco-CD Compose project name")
    cmdb = os.environ.get("CONTROL_PLANE_CMDB")
    require(bool(cmdb) and bool(os.environ.get('CONTROL_PLANE_VARS_FILE')),
            'External CONTROL_PLANE_CMDB and reference-only CONTROL_PLANE_VARS_FILE are required')
    cmdb_path = Path(cmdb).resolve(strict=True)
    require(cmdb_path.is_file(), "CMDB must be a regular file")
    document = json.loads(cmdb_path.read_text())
    spec = importlib.util.spec_from_file_location("control_plane_inventory", owner / "inventory/terraform_cmdb.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    records = module._host_records(document)
    record = records.get(target["host"])
    require(isinstance(record, dict) and record.get("ansible_user")
            and (record.get("instance_id") or record.get("resource_id")), "Exact CMDB target, user and resource identity are required")
    cmdb_environment = document.get('environment') or record.get('environment')
    require(cmdb_environment == contract['environment'] if cmdb_environment else
            contract['environment'] in record.get('groups', []), 'CMDB environment or authoritative environment group differs')
    inventory = module.build_inventory(document)
    hostvars = inventory["_meta"]["hostvars"][target["host"]]
    require(not any(k.startswith("ansible_") for k in record.get("host_vars", {})),
            "CMDB host_vars must not override connection identity")
    cmdb_ssh_key = record.get('ssh_private_key_file')
    operator_ssh_key = os.environ.get('ANSIBLE_PRIVATE_KEY_FILE')
    ssh_private_key_file = protected_ssh_key(cmdb_ssh_key) if cmdb_ssh_key is not None else None
    operator_ssh_key = protected_ssh_key(operator_ssh_key) if operator_ssh_key else None
    require(not (ssh_private_key_file and operator_ssh_key and ssh_private_key_file != operator_ssh_key),
            'CMDB and operator SSH key references must agree')
    ssh_private_key_file = ssh_private_key_file or operator_ssh_key
    disk = record.get("storage") or record.get("host_vars", {}).get("control_plane_storage") or execution.get('storage')
    if disk is None and record.get("data_disk"):
        original = record["data_disk"]
        disk = {"profile": "dedicated", "data_path": original["mount_path"].rstrip("/") + "/postgresql",
                "mount_path": original["mount_path"], "device_path": original.get("device_path", "")}
    require(isinstance(disk, dict) and disk.get("profile") in ("root", "dedicated"),
            "CMDB must explicitly select root or dedicated persistent storage")
    fields(disk, ["profile", "data_path"], ["mount_path", "device_path", "filesystem_uuid"])
    require(matches(r"/(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_-]+", disk["data_path"]), "Unsafe PostgreSQL data path")
    if disk["profile"] == "dedicated":
        require(matches(r"/dev/[A-Za-z0-9_./+-]+", disk.get("device_path"))
                and matches(r"/[A-Za-z0-9_/-]+", disk.get("mount_path")), "Dedicated storage requires declared device and mount")
    require(all(s["postgres_data_path"] == disk["data_path"] for s in selections),
            "Pinned PostgreSQL bind differs from the CMDB storage profile")
    source = contract.get("source", {})
    fields(source, [], ["host", "generation"])
    require(all(matches(NAME, value) for value in source.values()), 'Source audit metadata must contain literal names')
    require(not source or source.get("host") != target["host"], "Source and target must differ")
    if source.get("host") in records:
        other = records[source["host"]]
        require((other.get("ip") or other.get("ansible_host")) != hostvars["ansible_host"]
                and (other.get("instance_id") or other.get("resource_id")) != hostvars["cmdb_resource_id"],
                "Source and target CMDB identities alias")
    paths = [protected(os.environ['CONTROL_PLANE_VARS_FILE'])]
    runtime = {}
    for runtime_path in paths:
        values = read_yaml(runtime_path)
        fields(values, ["control_plane_runtime"])
        values = values["control_plane_runtime"]
        require(isinstance(values, dict) and not set(runtime).intersection(values), "Runtime files have overlapping keys")
        runtime.update(values)
    if runtime:
        validate_runtime_policy(runtime, contract['environment'])
    def literal(value):
        if isinstance(value, dict):
            return all(matches(r"[A-Za-z][A-Za-z0-9_]*", k) and literal(v) for k, v in value.items())
        return isinstance(value, str) and not any(x in value for x in ("\n", "\r", "\x00", "{{", "{%", "{#"))
    require(literal(runtime), "Runtime values must be literal single-line strings")
    resolved = {"gitops_sha": sha, "gitops_checkout": str(gitops), "cmdb_file": str(cmdb_path),
                "resource_id": hostvars["cmdb_resource_id"], "data_disk": disk, "selections": selections}
    if ssh_private_key_file is not None:
        resolved['ssh_private_key_file'] = ssh_private_key_file
    return {**contract, "resolved": resolved}, runtime


def plan(contract):
    return {"schema_version": 1, "status": "validated_plan", "host_actions": False,
            "environment": contract["environment"], "target": contract["target"]["host"],
            "resource_id": contract["resolved"]["resource_id"], "gitops_sha": contract["resolved"]["gitops_sha"],
            "playbooks_sha": contract["execution"]["playbooks_sha"], "snapshot_tag": contract["release"]["snapshot_tag"],
            "services": contract["release"]["services"], "initialize": contract["execution"]["initialize"],
            "doco_cd": contract["release"]["doco_cd"], "business_acceptance": False, "cutover": False}


def run_owner(owner, contract, runtime):
    validate_runtime_policy(runtime, contract['environment'])
    billing = billing_artifact(contract)
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    # Preserve only the explicitly provisioned dependency directory, never
    # unrelated caller overrides for SSH validation, callbacks or inventory.
    if os.environ.get('ANSIBLE_COLLECTIONS_PATH'):
        env['ANSIBLE_COLLECTIONS_PATH'] = os.environ['ANSIBLE_COLLECTIONS_PATH']
        env['ANSIBLE_COLLECTIONS_SCAN_SYS_PATH'] = 'False'
    env.update(ANSIBLE_CONFIG=str(owner / "scripts/control_plane/ansible.cfg"),
               ANSIBLE_STDOUT_CALLBACK='control_plane_receipt',
               ANSIBLE_CALLBACK_PLUGINS=str(owner / 'scripts/control_plane/callback_plugins'),
               ANSIBLE_ROLES_PATH=str(owner / "roles"), AI_WORKSPACE_CMDB_JSON=contract["resolved"]["cmdb_file"],
               CMDB_FILE=contract["resolved"]["cmdb_file"], PYTHONDONTWRITEBYTECODE="1")
    if contract['resolved'].get('ssh_private_key_file'):
        env['ANSIBLE_PRIVATE_KEY_FILE'] = contract['resolved']['ssh_private_key_file']
    inventory = str(owner / "inventory/terraform_cmdb.py")
    host = contract["target"]["host"]
    variables = json.dumps({"control_plane_contract": contract, "control_plane_runtime": runtime,
                            "control_plane_billing_artifact": billing})
    playbook = str(owner / "selfhost-control-plane.yml")
    commands = [
        ("controller_dependencies", "ansible-playbook --syntax-check controller dependencies",
         ["ansible-playbook", "-i", "localhost,", str(owner / "scripts/control_plane/ansible_dependency_preflight.yml"), "--syntax-check"], None),
        ("inventory", "ansible-inventory", ["ansible-inventory", "-i", inventory, "--host", host], None),
        ("syntax_check", "ansible-playbook --syntax-check", ["ansible-playbook", "-i", inventory, playbook, "--limit", host, "--syntax-check", "-e", "@/dev/stdin"], variables),
        ("ssh_connectivity", "ansible.builtin.ping", ["ansible", "-i", inventory, host, "-m", "ansible.builtin.ping"], None),
        ("apply", "ansible-playbook", ["ansible-playbook", "-i", inventory, playbook, "--limit", host, "-e", "@/dev/stdin"], variables),
    ]
    for stage, action, command, data in commands:
        stage_env = {**env, 'CONTROL_PLANE_STAGE': stage}
        try:
            result = subprocess.run(command, cwd=owner, env=stage_env, input=data, text=True, capture_output=True)
        except OSError:
            require(False, 'Owner stage could not start: ' + json.dumps(
                {'stage': stage, 'exit_code': None, 'action': action}, sort_keys=True))
        if result.returncode != 0:
            failure = {'stage': stage, 'exit_code': result.returncode, 'action': action}
            if stage == 'controller_dependencies' and result.returncode == 4:
                failure['diagnostic'] = ('Ansible role dependency parsing or module resolution failed; '
                                         'prepare roles/vhosts/common controller dependencies')
            stdout = result.stdout if isinstance(result.stdout, str) else ''
            for line in stdout.splitlines():
                if line.startswith('CONTROL_PLANE_FAILURE='):
                    try:
                        event = json.loads(line.split('=', 1)[1])
                    except (TypeError, ValueError):
                        continue
                    if (isinstance(event, dict) and event.get('stage') == stage
                            and event.get('status') in ('failed', 'unreachable')
                            and isinstance(event.get('action'), str)
                            and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', event['action'])):
                        failure['failure_status'] = event['status']
                        failure['failure_action'] = event['action']
                        task_name = event.get('task_name')
                        if (isinstance(task_name, str) and len(task_name) <= 120
                                and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._:/-]*', task_name)):
                            failure['task_name'] = task_name
                    break
            require(False, 'Owner stage failed: ' + json.dumps(failure, sort_keys=True))
    receipts = [json.loads(line.split('=', 1)[1]) for line in result.stdout.splitlines()
                if line.startswith('CONTROL_PLANE_EVIDENCE=')]
    receipt = receipts[0] if len(receipts) == 1 else None
    require(isinstance(receipt, dict) and receipt.get('status') == 'candidate_runtime_ready'
            and receipt.get('business_acceptance') is False, 'Candidate readiness receipt is absent or invalid')
    return {**plan(contract), "status": "owner_completed", "host_actions": True, 'readiness': receipt}


def billing_artifact(contract):
    declaration = contract['release'].get('billing')
    require(declaration is not None and bool(os.environ.get('BILLING_CHECKOUT')),
            'Execution requires release.billing and the fixed BILLING_CHECKOUT source artifact')
    root = Path(os.environ['BILLING_CHECKOUT']).resolve(strict=True)
    checkout(root, declaration['source_sha'])
    require(git(root, 'remote', 'get-url', 'origin') == 'https://github.com/ai-workspace-services/billing-service.git',
            'Billing artifact must use its canonical owner')
    path = inside(root, root, declaration['schema_file'])
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == contract['release']['accounts']['billing_schema_sha256'],
            'Billing schema bytes differ from the reviewed pin')
    return {'source_sha': declaration['source_sha'], 'schema_sha256': hashlib.sha256(raw).hexdigest(),
            'sql': raw.decode('utf-8')}


SECRET_KEYS = ('POSTGRES_PASSWORD', 'ACCOUNT_PG_PASSWORD', 'AUTH_TOKEN_PUBLIC_TOKEN',
               'AUTH_TOKEN_REFRESH_SECRET', 'AUTH_TOKEN_ACCESS_SECRET', 'INTERNAL_SERVICE_TOKEN')
RUNTIME_KEYS = ('ca_cert_b64', 'server_cert_b64', 'server_key_b64', 'tls_fullchain_b64',
                'tls_key_b64', 'registry_username', 'registry_password')


def validate_runtime_policy(policy, environment):
    fields(policy, ['vault_addr', 'vault_token_env', 'secret_refs'])
    require(matches(r'https://[A-Za-z0-9.-]+(?::[0-9]+)?', policy['vault_addr'])
            and matches(r'[A-Z][A-Z0-9_]*', policy['vault_token_env'])
            and policy['vault_token_env'] != 'INTERNAL_SERVICE_TOKEN', 'Explicit HTTPS Vault and dedicated short-lived identity reference required')
    refs = policy['secret_refs']
    fields(refs, (*SECRET_KEYS, *RUNTIME_KEYS), ['git_access_token'])
    prefix = 'kv/' + environment + '/'
    established_prod_refs = {
        'AUTH_TOKEN_PUBLIC_TOKEN': 'kv/prod-v2/web-saas#AUTH_TOKEN_PUBLIC_TOKEN',
        'AUTH_TOKEN_REFRESH_SECRET': 'kv/prod-v2/web-saas#AUTH_TOKEN_REFRESH_SECRET',
        'AUTH_TOKEN_ACCESS_SECRET': 'kv/prod-v2/web-saas#AUTH_TOKEN_ACCESS_SECRET',
        'ca_cert_b64': 'kv/CICD/domains/svc.plus#tls_ca_pem_b64',
        'server_cert_b64': 'kv/CICD/domains/svc.plus#tls_cert_pem_b64',
        'server_key_b64': 'kv/CICD/domains/svc.plus#tls_key_pem_b64',
        'tls_fullchain_b64': 'kv/CICD/domains/svc.plus#tls_fullchain_pem_b64',
        'tls_key_b64': 'kv/CICD/domains/svc.plus#tls_key_pem_b64',
        'registry_username': 'kv/CICD#GHCR_USERNAME',
        'registry_password': 'kv/CICD#GHCR_TOKEN',
    } if environment == 'prod' else {}
    for key, value in refs.items():
        environment_scoped = matches(re.escape(prefix) + r'[A-Za-z0-9_/-]+#[A-Za-z0-9_]+', value)
        require(environment_scoped or established_prod_refs.get(key) == value,
                'Runtime configuration must contain established field references only')
    require(refs['ACCOUNT_PG_PASSWORD'] == prefix + 'databases#account_pg_password'
            and refs['POSTGRES_PASSWORD'] == prefix + 'databases#postgres_root_password'
            and refs['INTERNAL_SERVICE_TOKEN'] == prefix + 'accounts/runtime#INTERNAL_SERVICE_TOKEN',
            'Database and shared service token must use their canonical owners')


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--config", required=True)
    parser.add_argument("--gitops-ref", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    require(os.environ.get("PLAYBOOKS_CHECKOUT") and os.environ.get("GITOPS_CHECKOUT"), "Both fixed checkout environment variables are required")
    owner = Path(os.environ["PLAYBOOKS_CHECKOUT"]).resolve(strict=True)
    require(Path(__file__).resolve() == owner / "scripts/control_plane/entrypoint.py", "Runner must belong to PLAYBOOKS_CHECKOUT")
    gitops = Path(os.environ["GITOPS_CHECKOUT"]).resolve(strict=True)
    contract, runtime = load_contract(args.config, gitops, owner, args.gitops_ref)
    print(json.dumps(plan(contract) if args.check else run_owner(owner, contract, runtime), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, yaml.YAMLError, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, Refused) else "Invalid contract or unavailable owner dependency"
        print("control-plane: " + message, file=sys.stderr)
        sys.exit(2)
