import importlib.util
from pathlib import Path
from unittest.mock import patch
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('observe', ROOT/'roles/v5_readonly_acceptance/files/observe.py')
observe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observe)


def test_agents_require_full_expected_coverage():
    data = dict(schema='1', dirty=False, orphan_subscriptions=0, positive_buckets_15m=1,
                bytes_15m=42, fresh_agents=1, agent_count=5)
    config = dict(schema='1', expected_agents=5)
    assert observe.assess_db(data, config)['V5-11'] == 'FAIL'
    data['fresh_agents'] = 5
    assert observe.assess_db(data, config)['V5-11'] == 'PARTIAL'
    data['agent_count'] = 6
    assert observe.assess_db(data, config)['V5-11'] == 'FAIL'


def test_live_bytes_and_counts_do_not_prove_business_equality():
    data = dict(schema='1', dirty=False, orphan_subscriptions=0, positive_buckets_15m=1,
                bytes_15m=42, fresh_agents=5, agent_count=5)
    result = observe.assess_db(data, dict(schema='1', expected_agents=5))
    assert result['V5-07'] == result['V5-10'] == 'PARTIAL'
    data['dirty'] = True
    assert observe.assess_db(data, dict(schema='1', expected_agents=5))['V5-06'] == 'FAIL'


def test_database_is_read_only_and_error_stops_sql():
    with patch.object(observe, 'command', return_value={}) as command:
        observe.database(dict(namespace='ns', pod='pg-0', db_user='app', db_name='account'))
    args, sql = command.call_args.args
    assert args[:5] == ['kubectl', '-n', 'ns', 'exec', '-i']
    assert 'ON_ERROR_STOP=1' in args
    assert sql.startswith('BEGIN READ ONLY;') and sql.rstrip().endswith('ROLLBACK;')
    assert 'statement_timeout' in sql


def test_stale_ready_generation_is_rejected():
    obj = dict(metadata=dict(generation=2), status=dict(observedGeneration=1, conditions=[dict(type='Ready', status='True')]))
    assert not observe.ready(obj)


def test_wrong_machine_prevents_any_command():
    with patch.object(Path, 'read_text', return_value='actual'), patch.object(observe, 'command') as command:
        with pytest.raises(ValueError): observe.observe(dict(machine_id='wrong'))
    command.assert_not_called()


def test_role_saves_evidence_before_blocking():
    tasks = yaml.safe_load((ROOT/'roles/v5_readonly_acceptance/tasks/main.yml').read_text())
    assert 'ansible.builtin.copy' in tasks[-2]
    assert tasks[-2]['delegate_to'] == 'localhost'
    assert 'ansible.builtin.assert' in tasks[-1]


def test_eso_ready_uses_refresh_evidence_without_claiming_drift():
    obj = dict(kind='ExternalSecret', status=dict(conditions=[dict(type='Ready', status='True')]))
    assert not observe.ready(obj)
    obj['status']['refreshTime'] = '2026-10-25T09:00:00Z'
    assert observe.ready(obj)


def test_environment_must_match_inventory_before_remote_execution():
    tasks = yaml.safe_load((ROOT/'roles/v5_readonly_acceptance/tasks/main.yml').read_text())
    rules = tasks[0]['ansible.builtin.assert']['that']
    assert any('cmdb_environment' in rule and 'v5_acceptance_environment' in rule for rule in rules)
