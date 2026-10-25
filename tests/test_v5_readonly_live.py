"""Opt-in real SSH/Playbook/Role gate effectiveness tests, not business acceptance.

Requires independently cloud-verified temporary inventory and nonsecret variables.
No dispatch, provisioning, migration, service write or restart is performed.
"""
import json
import os
from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def live_inputs():
    names = ('V5_LIVE_INVENTORY', 'V5_LIVE_VARS', 'V5_LIVE_ANSIBLE_CONFIG', 'V5_LIVE_EVIDENCE_DIR')
    if not all(os.environ.get(name) for name in names):
        pytest.skip('explicit verified SSH live inputs absent; NOT acceptance evidence')
    inventory, variables, config, evidence = [Path(os.environ[name]).resolve() for name in names]
    # Force the strict SSH config tested by this explicit manual run.
    text = config.read_text()
    assert 'host_key_checking = True' in text and 'StrictHostKeyChecking=yes' in text
    evidence.mkdir(parents=True, exist_ok=True)
    return inventory, json.loads(variables.read_text()), config, evidence


@pytest.mark.parametrize('case', ['baseline', 'wrong-machine', 'wrong-environment', 'wrong-sha'])
def test_real_role_fails_closed(live_inputs, case):
    inventory, variables, config, evidence = live_inputs
    observation = evidence / (case + '.json')
    assert not observation.exists(), 'do not reuse a prior observation'
    variables['v5_acceptance_output'] = str(observation)
    variables['v5_acceptance_report_dir'] = str(evidence/(case+'-report'))
    variables['v5_acceptance_run_id'] += '-'+case
    if case == 'wrong-machine': variables['v5_acceptance_machine_id'] = '0'*32
    if case == 'wrong-environment': variables['v5_acceptance_environment'] = 'uat'
    if case == 'wrong-sha': variables['v5_acceptance_gitops_sha'] = '0'*40
    var_path = evidence / (case + '-vars.json')
    var_path.write_text(json.dumps(variables)); var_path.chmod(0o600)
    env = dict(os.environ, ANSIBLE_CONFIG=str(config), ANSIBLE_NOCOLOR='1')
    result = subprocess.run(['ansible-playbook', '-i', str(inventory), str(ROOT/'v5-readonly-acceptance.yml'),
                             '-e', '@'+str(var_path)], env=env, capture_output=True, text=True, timeout=150)
    (evidence/(case+'.log')).write_text(result.stdout + result.stderr)
    assert result.returncode == 2, 'expected explicit gate failure, not transport/tool failure or false success'
    if case == 'wrong-environment':
        assert observation.exists(), 'rejected preflight still needs bounded failure evidence'
        assert json.loads(observation.read_text())['checks'] == {}
        assert (Path(variables['v5_acceptance_report_dir'])/'report.zh.md').is_file()
        assert 'Run read-only K3s and database observations over SSH' not in result.stdout
        return
    data = json.loads(observation.read_text())
    report = Path(variables['v5_acceptance_report_dir'])
    assert (report/'report.zh.md').is_file()
    assert (report/'junit.xml').is_file()
    assert json.loads((report/'evidence.json').read_text())['result'] == 'FAIL'
    assert data['result'] == 'FAIL'
    if case == 'wrong-machine':
        assert data['checks'] == {}
        assert data.get('reason') in (None, 'invalid configuration or machine identity')
    elif case == 'wrong-sha':
        assert data['checks']['V5-13']['status'] == 'FAIL'
        assert data['checks']['V5-13']['observation']['source_revision'].split(':')[-1] != '0'*40
    else:
        assert data['environment'] == variables['v5_acceptance_environment']
        assert set(data['checks']) == {f'V5-{n:02}' for n in range(1,24)}
        assert data['checks']['V5-06']['status'] == 'PASS'
        assert data['checks']['V5-08']['status'] == 'PASS'
        assert data['checks']['V5-13']['status'] == 'PARTIAL'
