#!/usr/bin/env python3
"""Build an offline, pinned invocation manifest for a V5/V4 regression batch.

This planner never invokes Ansible, Docker, cloud APIs, databases, or network probes.
"""
import argparse
import ipaddress
import json
import re
import shlex
import sys
from pathlib import Path


HISTORICAL_PLAYBOOKS_COMMIT = 'e2b584716fd6770f64d2ba2a7c4137fa4117c7ae'
HISTORICAL_RUNNER_SHA256 = {
    'roles/web_saas_generation_fresh/files/regression_cases.py':
        '37888cd5fcdc14178ad626cbca95aac284fb335a3fc6e8ad56294787e905c763',
    'roles/web_saas_generation_fresh/files/postgres_cases.py':
        'a93ab85155f6e4f6ee83f9f4b81274a42b2bf73ccbbc1bf9a6363fe2a03eab3d',
}
HOST_RUNNER = 'roles/web_saas_generation_fresh/files/regression_cases.py'
POSTGRES_RUNNER = 'roles/web_saas_generation_fresh/files/postgres_cases.py'
PUBLIC_HOSTS = ['console.svc.plus', 'accounts.svc.plus', 'billing.svc.plus',
                'xworktech.com', 'www.xworktech.com']
HISTORICAL_KNOWLEDGE_CASE_DIRECTORY = (
    'content/02-iac-devops/cloud-infrastructure-devsecops-baseline/records/'
    'prod-v2-v3-20261019'
)
SOURCE_RECORD_DIRECTORY = (
    'content/02-iac-devops/cloud-infrastructure-devsecops-baseline/records/'
    'prod-v3-v4-20261020'
)
KNOWLEDGE_CASE_PATHS = {
    'public-sni-cert': '20261019-prod-v3-public-sni-repair.zh.md',
    'public-host-completeness': 'README.md',
    'runtime-secret-injection': '20261019-prod-v3-canonical-service-token.zh.md',
    'billing-ingest': '20261020-billing-vector-ingest-p0.zh.md',
    'exporter-vector': '20261020-billing-vector-ingest-p0.zh.md',
    'agent-registration': '20261019-prod-v3-canonical-service-token.zh.md',
    'client-transport': '20261019-prod-v3-vless-path-p0-release.zh.md',
    'auth-callback': 'README.md',
    'schema-data-protection': 'v2-to-v3-snapshot-refresh-20261019.zh.md',
    'repeat-deploy-restore': 'web-saas-generation-snapshot-recovery-template.zh.md',
}

# Historical statuses are reproduced from the dated checklist and baseline,
# not promoted to current V5 results.
HOST_CASES = [
    ('public-sni-cert', 'Trusted TLS and SANs for declared public names and target aliases; certificate mount read-only.', 'PASS', [0], 'PARTIAL', 'Current V4 probe was partial; full pinned case was not rerun.'),
    ('public-host-completeness', 'Exact DNS A-record set equals the supplied stage declaration; www preserves URI.', 'NOT_REUSABLE', [], 'NEEDS_PARAMETERIZATION', 'Old expected routing was tied to V3; no historical exit is asserted for that invalidated expectation.'),
    ('runtime-secret-injection', 'Accounts, Billing and Console process tokens match the canonical runtime value without emitting it.', 'PASS', [0, 0], 'NOT_RUN', 'Current baseline did not recheck all three processes.'),
    ('billing-ingest', 'Missing/invalid tokens reject; management and wrong methods reject; isolated valid event persists once.', 'BLOCKED_AND_FAIL', [2, 1], 'PARTIAL', 'Negative auth boundary passed separately; positive persistence and duplicate fixture remain required.'),
    ('exporter-vector', 'Every selected node delivers fresh isolated usage/ledger data without duplicate posting.', 'BLOCKED', [2], 'BLOCKED', 'Historical full-chain case blocked; database freshness alone is insufficient.'),
    ('agent-registration', 'Isolated edge registers, receives configuration, and establishes a user connection.', 'BLOCKED', [2], 'PARTIAL', 'Status POST evidence is partial; isolated writable edge fixture is required.'),
    ('client-transport', 'Generated VLESS path matches Xray and an isolated client connects.', 'BLOCKED', [2], 'NOT_RUN', 'Historical case blocked; no current candidate client evidence.'),
    ('auth-callback', 'Isolated login/MFA/OAuth and Sandbox callbacks complete with correct event association.', 'BLOCKED', [2], 'NOT_RUN', 'Login-page readiness is not a session or payment callback result.'),
    ('schema-data-protection', 'Pinned full-business comparison protects source and rejects identity/finance/quota conflicts.', 'FAIL_PASS_FAIL', [1, 0, 1], 'BLOCKED', 'b598 candidate recorded FAIL/1 then PASS/0; later 765ea candidate recorded FAIL/1; current comparison must be rerun.'),
    ('repeat-deploy-restore', 'Same-candidate replay avoids unexpected restart/repeated writes; populated restore reconnects application.', 'BLOCKED', [2], 'BLOCKED', 'Historical application-on-restored-database and healthy convergence are not proven.'),
]

POSTGRES_CASES = [
    ('native-initialization', 'TestNativeInitPostgres17', 'MIGRATECTL_NATIVE_TEST_DSN', 'internal/migrate/native_init_test.go', 'PASS', 0),
    ('migration-concurrency-retry', 'TestControlledMigratePostgresIntegration', 'ACCOUNTS_TEST_POSTGRES_DSN', 'internal/migrate/controlled_test.go', 'PASS', 0),
    ('lifecycle-migration-rollback', 'TestSubscriptionLifecycleUpgradePostgres17', 'MIGRATECTL_LIFECYCLE_UPGRADE_TEST_DSN', None, 'PASS', 0),
    ('business-conflict-rollback', 'TestFullBusinessPostgres17', 'MIGRATECTL_FULL_BUSINESS_TEST_DSN', 'internal/migrate/full_business_postgres_test.go', 'PASS', 0),
    ('usage-merge-conflicts', 'TestFullBusinessUsageMergePostgres17', 'MIGRATECTL_USAGE_MERGE_TEST_DSN', 'internal/migrate/usage_merge_postgres_test.go', 'PASS', 0),
    ('late-users-single-backfill', 'TestGenerationUsersPostgres17', 'MIGRATECTL_GENERATION_USERS_SOURCE_TEST_DSN', 'internal/migrate/generation_users_postgres_test.go', 'PASS', 0),
]


def validate_generations(target, source):
    pattern = r'prod-v([1-9][0-9]*)'
    target_match = re.fullmatch(pattern, target or '')
    source_match = re.fullmatch(pattern, source or '')
    if not target_match or not source_match:
        raise ValueError('target and source must use prod-vN generation names')
    if int(target_match.group(1)) != int(source_match.group(1)) + 1:
        raise ValueError('source must be the immediately preceding generation')


def _host_case(row, candidate_commit=None):
    case_id, expectation, historical_status, historical_exits, current_status, note = row
    item = {
        'case_id': case_id,
        'runner_path': HOST_RUNNER,
        'historical_knowledge_case_path': HISTORICAL_KNOWLEDGE_CASE_DIRECTORY + '/' + KNOWLEDGE_CASE_PATHS[case_id],
        'source_record_path': SOURCE_RECORD_DIRECTORY + '/regression-and-closure-checklist-20261021.zh.md',
        'runner_pin': {'commit': HISTORICAL_PLAYBOOKS_COMMIT,
                       'sha256': HISTORICAL_RUNNER_SHA256[HOST_RUNNER]},
        'expected': expectation,
        'checklist_status': current_status,
        'expected_exit_policy': {'PASS': 0, 'FAIL': 1, 'BLOCKED': 2, 'NOT_RUN': None},
        'historical': {'status': historical_status, 'observed_exit_codes': historical_exits,
        'evidence': 'private V4 receipt reference: fresh-native-20261020-233500/cases/<candidate-commit>/<case>-<utc>.json; absolute host path and exact filenames are not in the shared checklist.',
                       'note': note},
        'new_evidence': '<target-checkpoint>/cases/' + (candidate_commit or '<candidate-gitops-sha>') + '/' + case_id + '-<utc>.json',
    }
    if case_id == 'schema-data-protection':
        item['historical_candidate_pins'] = [
            {'candidate_gitops_sha': 'b5987fefc5e591e0fdba33c684ca83766a16ff37',
             'observed_exit_codes': [1, 0]},
            {'candidate_gitops_sha_prefix': '765ea', 'candidate_gitops_sha': None,
             'observed_exit_codes': [1], 'pin_status': 'full SHA absent from the shared source record'},
        ]
    return item


def _postgres_case(row, lifecycle_source_path=None):
    case_id, test_name, dsn_env, source_path, status, exit_code = row
    if case_id == 'lifecycle-migration-rollback' and lifecycle_source_path:
        source_path = lifecycle_source_path
    return {
        'case_id': case_id,
        'runner_path': POSTGRES_RUNNER,
        'source_record_path': SOURCE_RECORD_DIRECTORY + '/regression-and-closure-checklist-20261021.zh.md',
        'baseline_path': SOURCE_RECORD_DIRECTORY + '/prod-v4-runtime-and-snapshot-baseline-20261021.zh.md',
        'runner_pin': {'commit': HISTORICAL_PLAYBOOKS_COMMIT,
                       'sha256': HISTORICAL_RUNNER_SHA256[POSTGRES_RUNNER]},
        'accounts_test': {'function': test_name, 'dsn_environment': dsn_env,
                          'source_path': source_path,
                          'source_path_status': 'unresolved in available Accounts checkout' if source_path is None else 'path recorded; immutable source SHA still required'},
        'expected': 'PASS with the exact test emitting --- PASS:; skipped=false; restarted fixture row fingerprint unchanged.',
        'expected_exit_policy': {'PASS': 0, 'FAIL': 1, 'BLOCKED': 1, 'NOT_RUN': None},
        'historical': {'status': status, 'exit_code': exit_code, 'skipped': False,
                       'evidence': 'private V4 receipt reference: fresh-native-20261020-233500/postgres-cases-20261020T162919330798Z.json',
                       'accounts_source_sha': None,
                       'pin_gap': 'the shared checklist does not publish the historical Accounts source SHA, integration binary digest, SQL digests, or PostgreSQL image digest'},
        'new_evidence': '<target-checkpoint>/postgres-cases-<utc>.json and <target-checkpoint>/<case>-<fixture-run>.log',
    }


def build_batch(args):
    validate_generations(args.target_generation, args.source_generation)
    missing = []
    required = {
        'candidate_gitops_sha': args.candidate_commit,
        'playbooks_runner_sha': args.playbooks_runner_sha,
        'accounts_source_sha': args.accounts_source_sha,
        'lifecycle_test_source_path': args.lifecycle_test_source_path,
        'run_id': args.run_id,
        'target_ip': args.target_ip,
        'postgres_image_digest': args.postgres_image_digest,
        'integration_binary_path': args.integration_binary_path,
        'integration_binary_sha256': args.integration_binary_sha256,
        'billing_sql_path': args.billing_sql_path,
        'billing_sql_sha256': args.billing_sql_sha256,
        'lifecycle_sql_path': args.lifecycle_sql_path,
        'lifecycle_sql_sha256': args.lifecycle_sql_sha256,
        'verified_inventory': args.verified_inventory,
        'target_cmdb_instance_id': args.target_cmdb_instance_id,
        'target_postgres_container': args.target_postgres_container,
        'protected_vars_file': args.protected_vars_file,
        'checkpoint': args.checkpoint,
        'schema_version': args.schema_version,
        'schema_sha256': args.schema_sha256,
        'billing_schema_sha256': args.billing_schema_sha256,
        'source_archive_sha256': args.source_archive_sha256,
        'public_route_expectations_file': args.public_route_expectations_file,
        'source_restore_receipt': args.source_restore_receipt,
        'source_writer_fence_evidence': args.source_writer_fence_evidence,
    }
    missing.extend(key for key, value in required.items() if not value)
    routes = None
    if args.public_route_expectations_file:
        route_path = Path(args.public_route_expectations_file)
        try:
            routes = json.loads(route_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError('could not read public route expectation JSON') from error
        if not isinstance(routes, dict) or set(routes) != set(PUBLIC_HOSTS):
            raise ValueError('route JSON must declare exactly the five public hostnames')
        for host, addresses in routes.items():
            if not isinstance(addresses, list) or not addresses or any(not isinstance(address, str) for address in addresses):
                raise ValueError('each declared public host requires at least one A-record address')
            try:
                routes[host] = sorted({str(ipaddress.IPv4Address(address)) for address in addresses})
            except ipaddress.AddressValueError as error:
                raise ValueError('public route values must be IPv4 addresses') from error
    if args.accounts_source_sha and not re.fullmatch(r'[0-9a-f]{40}', args.accounts_source_sha):
        raise ValueError('accounts_source_sha must be a full lowercase Git SHA')
    if args.lifecycle_test_source_path and (
            Path(args.lifecycle_test_source_path).is_absolute() or
            '..' in Path(args.lifecycle_test_source_path).parts or
            not args.lifecycle_test_source_path.endswith('.go')):
        raise ValueError('lifecycle_test_source_path must be a relative Go source path without parent traversal')
    if args.candidate_commit and not re.fullmatch(r'[0-9a-f]{40}', args.candidate_commit):
        raise ValueError('candidate GitOps SHA must be a full lowercase Git SHA')
    if args.playbooks_runner_sha and not re.fullmatch(r'[0-9a-f]{40}', args.playbooks_runner_sha):
        raise ValueError('playbooks_runner_sha must be a full lowercase Git SHA')
    if args.target_ip:
        try:
            ipaddress.IPv4Address(args.target_ip)
        except ipaddress.AddressValueError as error:
            raise ValueError('target_ip must be IPv4') from error
    if args.target_postgres_container and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args.target_postgres_container):
        raise ValueError('target_postgres_container must be an explicit Docker container name')
    if args.run_id and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}', args.run_id):
        raise ValueError('run_id must be a short filesystem-safe identifier')
    if args.schema_version and not re.fullmatch(r'[0-9]+', args.schema_version):
        raise ValueError('schema_version must contain digits only')
    if args.checkpoint and (Path(args.checkpoint).parent.name != args.target_generation or
                            not Path(args.checkpoint).name.startswith('fresh-native-')):
        raise ValueError('checkpoint must be a new fresh-native directory directly below the target-generation directory')
    for key in ('postgres_image_digest',):
        value = getattr(args, key)
        if value and not re.fullmatch(r'ghcr\.io/[A-Za-z0-9_./:-]+@sha256:[0-9a-f]{64}', value):
            raise ValueError(key + ' must be an immutable GHCR image digest')
    for key in ('integration_binary_sha256', 'billing_sql_sha256', 'lifecycle_sql_sha256',
                'schema_sha256', 'billing_schema_sha256', 'source_archive_sha256'):
        value = getattr(args, key)
        if value and not re.fullmatch(r'[0-9a-f]{64}', value):
            raise ValueError(key + ' must be a lowercase SHA-256')

    unresolved_case_inputs = []
    if not args.lifecycle_test_source_path:
        unresolved_case_inputs.append('lifecycle-migration-rollback test source path')
    unresolved_case_inputs.append('writer-ownership executable case path and immutable pin')
    resolved_postgres_cases = [
        _postgres_case(row, args.lifecycle_test_source_path)
        for row in POSTGRES_CASES
    ]

    return {
        'schema': 'playbooks-generation-regression-batch/v1',
        'status': 'BLOCKED' if missing or unresolved_case_inputs else 'PREPARED',
        'target_generation': args.target_generation,
        'source_generation': args.source_generation,
        'candidate_gitops_sha': args.candidate_commit,
        'playbooks_runner_sha': args.playbooks_runner_sha,
        'accounts_source_sha': args.accounts_source_sha,
        'lifecycle_test_source_path': args.lifecycle_test_source_path,
        'schema_version': args.schema_version,
        'schema_sha256': args.schema_sha256,
        'billing_schema_sha256': args.billing_schema_sha256,
        'source_archive_sha256': args.source_archive_sha256,
        'postgres_image_digest': args.postgres_image_digest,
        'integration_binary_sha256': args.integration_binary_sha256,
        'billing_sql_sha256': args.billing_sql_sha256,
        'lifecycle_sql_sha256': args.lifecycle_sql_sha256,
        'private_operational_inputs_present': sorted(key for key, value in required.items() if value),
        'historical_playbooks_case_pin': HISTORICAL_PLAYBOOKS_COMMIT,
        'historical_runner_sha256': HISTORICAL_RUNNER_SHA256,
        'public_route_expectations': routes,
        'cases': [_host_case(row, args.candidate_commit) for row in HOST_CASES] +
                 resolved_postgres_cases,
        'additional_checklist_gates': [
            {'case_id': 'business-runtime-role', 'runner_path': 'tests/data_operations/selfhost/v4_business_role_acceptance.sh',
             'historical_pin': {'commit': '6d6c65a200a342ba8cea819bc0120ebf14fc57c6',
                                'sha256': '304fc0e14f06dcc656cde620ea19d65039b5c6a52c88700920af60d9ee8d6d2e'},
             'execution_pin': 'playbooks_runner_sha from this manifest',
             'expected': 'Candidate account_user has INSERT on users, billing_ledger, and traffic_minute_buckets.',
             'coverage_limit': 'This script does not verify password source, SELECT/UPDATE/DELETE grants, task flags, or declaration convergence; those remain separate checklist gates.',
             'expected_exit_policy': {'PASS': 0, 'FAIL': 1, 'BLOCKED': 2},
             'historical_status': 'PARTIAL', 'historical_exit_code': None,
             'historical_evidence': SOURCE_RECORD_DIRECTORY + '/prod-v4-runtime-and-snapshot-baseline-20261021.zh.md §4; current configuration convergence remains open.',
             'new_evidence': 'candidate task stdout/stderr; runner has no dedicated JSON receipt writer',
             'command': ('GENERATION_TARGET_GENERATION=' + shlex.quote(args.target_generation) +
                         ' GENERATION_POSTGRES_CONTAINER=<verified-v5-container>' +
                         ' bash tests/data_operations/selfhost/v4_business_role_acceptance.sh')},
            {'case_id': 'writer-ownership', 'runner_path': None,
             'expected': 'One generation owns business writes; old source is fenced; source/target user baseline is recorded.',
             'historical_status': 'NOT_RUN', 'historical_exit_code': None,
             'evidence': SOURCE_RECORD_DIRECTORY + '/regression-and-closure-checklist-20261021.zh.md row V4-01,22; no executable case or complete receipt is identified.'},
        ],
        'local_fixture_cases': {
            'runner_path': 'tests/test_recovery_public_ingress_idempotency.py',
            'pin': {'commit': '611cc12b443ea669dc1460e6597320acaed7a418',
                    'sha256': 'e98eb58938216c7773f53779d1dd68171dc1a6140fcbdc9d63dab12fc1fcb60d'},
            'command': "<ansible-python> -m unittest discover -s tests -p 'test_recovery_public_ingress_idempotency.py' -v",
            'cases': [
                'test_activation_requires_change_and_explicit_enablement',
                'test_activated_ingress_is_recorded_and_preserved_by_recovery',
                'test_restore_and_fresh_deployment_share_ingress_owner',
                'test_all_sni_hosts_and_ingestion_survive_generation_render',
                'test_deployment_reference_accepts_only_fixed_sha_or_release_tag',
            ],
            'expected_exit_code': 0,
            'scope': 'offline rendered-config and task contract fixtures; not V5 deployment or live ingress acceptance',
        },
        'additional_local_fixture_suites': [
            {'runner_path': 'tests/data_operations/selfhost/v4_acceptance_scripts_test.py',
             'command': "<ansible-python> -m unittest discover -s tests/data_operations/selfhost -p 'v4_acceptance_scripts_test.py' -v",
             'expected_exit_code': 0,
             'scope': 'mock Docker CLI contract fixtures; no real Docker daemon calls'},
            {'runner_path': 'tests/test_prepare_generation_regression_batch.py',
             'command': "python3 -m unittest discover -s tests -p 'test_prepare_generation_regression_batch.py' -v",
             'expected_exit_code': 0,
             'scope': 'offline manifest and input-validation fixtures'},
        ],
        'missing_required_inputs': missing,
        'unresolved_case_inputs': unresolved_case_inputs,
        'known_pin_and_evidence_gaps': [
            'Schema case candidate 765ea has only a short SHA prefix in the shared checklist; its full commit cannot be recovered from that record.',
            'The historical PostgreSQL receipt filename is known, but its exact private per-case logs and Accounts/tool/image/SQL pins are not in the shared record.',
            'The writer-ownership gate has no executable case path or immutable runner pin in the identified source records.',
        ],
        'invocations': [
            {'entrypoint': 'web-saas-generation-regression.yml',
             'command': 'ansible-playbook -i <verified-v5-inventory> web-saas-generation-regression.yml -e @<protected-v5-regression-vars.json>',
             'case_ids': [row[0] for row in HOST_CASES], 'execution_now': False},
            {'entrypoint': 'web-saas-generation-postgres-cases.yml',
             'command': 'ansible-playbook -i <verified-v5-inventory> web-saas-generation-postgres-cases.yml -e @<protected-v5-regression-vars.json>',
             'case_ids': [row[0] for row in POSTGRES_CASES], 'execution_now': False},
            {'entrypoint': 'tests/data_operations/selfhost/v4_business_role_acceptance.sh',
             'command': 'candidate-only read-only acceptance; exact pinned command and variables must be supplied by the runner owner',
             'case_ids': ['business-runtime-role'], 'execution_now': False},
            {'entrypoint': None, 'command': None, 'case_ids': ['writer-ownership'], 'execution_now': False,
             'blocker': 'No executable isolated writer-ownership case is present in the identified batch.'},
        ],
        'execution_boundary': {
            'source_generation_access': 'read-only isolated snapshot only; no source application or database writes',
            'public_dns_or_cloud_changes': False,
            'existing_container_reuse': False,
            'source_mutations': False,
            'operator_note': 'This planner did not run Ansible, Docker, database commands, DNS queries, or network probes.',
        },
    }


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--target-generation', default='prod-v5')
    result.add_argument('--source-generation', default='prod-v4')
    result.add_argument('--candidate-gitops-sha', '--candidate-commit', dest='candidate_commit')
    result.add_argument('--playbooks-runner-sha')
    result.add_argument('--accounts-source-sha')
    result.add_argument('--lifecycle-test-source-path')
    result.add_argument('--run-id')
    result.add_argument('--target-ip')
    result.add_argument('--postgres-image-digest')
    result.add_argument('--integration-binary-path')
    result.add_argument('--integration-binary-sha256')
    result.add_argument('--billing-sql-path')
    result.add_argument('--billing-sql-sha256')
    result.add_argument('--lifecycle-sql-path')
    result.add_argument('--lifecycle-sql-sha256')
    result.add_argument('--verified-inventory')
    result.add_argument('--protected-vars-file')
    result.add_argument('--target-cmdb-instance-id')
    result.add_argument('--target-postgres-container')
    result.add_argument('--checkpoint')
    result.add_argument('--schema-version')
    result.add_argument('--schema-sha256')
    result.add_argument('--billing-schema-sha256')
    result.add_argument('--source-archive-sha256')
    result.add_argument('--public-route-expectations-file')
    result.add_argument('--source-restore-receipt')
    result.add_argument('--source-writer-fence-evidence')
    result.add_argument('--output', type=Path)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    manifest = build_batch(args)
    payload = json.dumps(manifest, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.write_text(payload)
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
