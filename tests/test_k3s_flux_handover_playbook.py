from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_handover_playbook_is_declarative_active_and_vault_become():
    play = yaml.safe_load((ROOT / 'k3s_flux_handover.yml').read_text())[0]
    assert play['hosts'] == 'k3s' and play['become'] is True
    assert play['vars']['k3s_platform_flux_bootstrap_mode'] == 'declarative_active'
    names = [t.get('ansible.builtin.include_role', {}).get('tasks_from') for t in play['pre_tasks']]
    assert names[:2] == ['from_declaration.yml', 'become_from_vault.yml']
    assert play['pre_tasks'][2]['ansible.builtin.set_fact']['k3s_platform_git_commit'] == '{{ k3s_identity_gitops_sha }}'
    assert play['roles'] == [{'role': 'vhosts/flux_gitops'}]
