import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "node_deploy"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location(
    "vault_migration_observation", SCRIPT_DIR / "vault_migration_observation.py"
)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)

NEW = ("vault-0", "vault-1", "vault-2")
OBSERVED = {"dns_switched_at": "2020-01-01T00:00:00+00:00", "hours": 24}


def contract():
    nodes = [
        {
            "id": node_id,
            "private_address": f"10.79.0.{index + 1}",
            "address": f"35.1.2.{index}",
            "groups": ["vault_shared_nodes", "vault_shared_peers"],
        }
        for index, node_id in enumerate(NEW)
    ]
    nodes.append(
        {
            "id": "legacy",
            "address": "jp.example.net",
            "private_address": "10.79.0.10",
            "overlay_address": "10.79.0.10",
            "groups": ["vault_legacy_source", "vault_shared_leader", "vault_single_node"],
        }
    )
    return {"spec": {"nodes": nodes}}


def running(storage, initialized=True, sealed=False, standby=False):
    return {
        "reachable": True,
        "storage_type": storage,
        "health": {
            "initialized": initialized,
            "sealed": sealed,
            "standby": standby,
            "cluster_id": "cluster-1",
        },
        "units": {"vault": "active"},
        "vault_enabled": "enabled",
    }


def empty():
    return {
        "reachable": True,
        "health": None,
        "units": {"vault": "inactive"},
        "vault_enabled": "disabled",
    }


def probes(legacy, new=None):
    states = {node_id: (new or {}).get(node_id, empty()) for node_id in NEW}
    states["legacy"] = legacy
    return states


def decide(states, dns_on_legacy=True, observation=None):
    return module.decide(
        contract(),
        states,
        dns_on_legacy=dns_on_legacy,
        backup_declared=True,
        observation=OBSERVED if observation is None else observation,
    )


class EquivalentRecommendationTests(unittest.TestCase):
    def test_postgresql_source_recommends_convert_only_when_unsealed(self):
        self.assertEqual(decide(probes(running("postgresql")))["recommended_stage"], "migrate-convert")
        self.assertIn("unseal", decide(probes(running("postgresql", sealed=True)))["blocked"])

    def test_raft_nodes_join_one_at_a_time_and_stop_on_a_sealed_peer(self):
        self.assertEqual(decide(probes(running("raft")))["recommended_stage"], "migrate-join")
        joined = {"vault-0": running("raft", standby=True), "vault-1": running("raft", sealed=True)}
        result = decide(probes(running("raft"), joined))
        self.assertEqual(result["recommended_stage"], "")
        self.assertIn("raft list-peers", result["blocked"])

    def test_cutover_requires_every_new_node_unsealed_and_source_active(self):
        joined = {node_id: running("raft", standby=True) for node_id in NEW}
        self.assertEqual(decide(probes(running("raft"), joined))["recommended_stage"], "migrate-cutover")

    def test_remove_waits_for_dns_and_observation_window(self):
        joined = {node_id: running("raft", standby=node_id != "vault-1") for node_id in NEW}
        source = running("raft", standby=True)
        self.assertIn("Switch the service DNS", decide(probes(source, joined))["blocked"])
        self.assertIn("Observing", decide(probes(source, joined), False, {})["blocked"])
        self.assertEqual(decide(probes(source, joined), False)["recommended_stage"], "migrate-remove")

    def test_unreachable_stopped_and_retired_sources_fail_closed(self):
        self.assertIn("unreachable", decide(probes({"reachable": False}))["blocked"])
        stopped = {
            "reachable": True,
            "health": None,
            "units": {"vault": "failed"},
            "vault_enabled": "enabled",
        }
        self.assertIn("not answering", decide(probes(stopped))["blocked"])
        active_new = {node_id: running("raft", standby=node_id != "vault-1") for node_id in NEW}
        self.assertIn("Migration complete", decide(probes(empty(), active_new))["blocked"])

    def test_rollback_is_never_recommended(self):
        scenarios = [
            probes(running("postgresql")),
            probes(running("raft")),
            probes(running("raft", sealed=True)),
        ]
        for states in scenarios:
            for dns_on_legacy in (True, False):
                self.assertNotEqual(decide(states, dns_on_legacy)["recommended_stage"], "migrate-rollback")


class ObservationContractTests(unittest.TestCase):
    def test_observation_probes_every_node_and_projects_non_secret_facts(self):
        states = probes(running("raft"))
        original_probe = module.probe
        original_dns = module.dns_points_at_legacy
        calls = []
        try:
            module.probe = lambda node, key, known_hosts, gateway_state: calls.append(node["id"]) or states[node["id"]]
            module.dns_points_at_legacy = lambda service, legacy: True
            decision, facts = module.observe(
                contract(),
                Path("key"),
                Path("known_hosts"),
                "https://vault.svc.plus",
                True,
                {},
                "/runner/gateway.json",
            )
        finally:
            module.probe = original_probe
            module.dns_points_at_legacy = original_dns
        self.assertEqual(calls, [*NEW, "legacy"])
        self.assertEqual(decision["recommended_stage"], "migrate-join")
        self.assertEqual(set(facts["nodes"]), {*NEW, "legacy"})
        self.assertEqual(facts["service_host"], "vault.svc.plus")
        self.assertNotIn("gateway", facts["nodes"]["legacy"])

    def test_output_is_single_line_json_for_composite_action(self):
        decision = {"recommended_stage": "", "blocked": "manual gate"}
        facts = {"schema": 1, **decision}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            module.write_outputs(output, decision, facts)
            values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        self.assertEqual(values["blocked"], "manual gate")
        self.assertEqual(values["facts"], '{"blocked":"manual gate","recommended_stage":"","schema":1}')


if __name__ == "__main__":
    unittest.main()
