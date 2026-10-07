import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "node_deploy"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location("verify_vault_stage", SCRIPT_DIR / "verify_vault_stage.py")
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


GATEWAY_KEY = "A" * 43 + "="


def fixture():
    nodes = [
        {"id": "vault-0", "private_address": "10.81.0.2",
         "groups": ["vault_shared_nodes", "vault_shared_leader", "xconnect_gateway"]},
        {"id": "vault-1", "private_address": "10.81.0.3",
         "groups": ["vault_shared_nodes", "vault_shared_peers", "xconnect_one"]},
        {"id": "vault-2", "private_address": "10.81.0.4",
         "groups": ["vault_shared_nodes", "vault_shared_peers", "xconnect_one"]},
    ]
    contract = {"spec": {"nodes": nodes}}
    probes = {
        node["id"]: {
            "reachable": True,
            "sudo": True,
            "swap_kb": 0,
            "health": {"initialized": True, "sealed": False, "cluster_id": "cluster-a", "standby": index != 0},
            "leader": {"is_self": index == 0, "leader_cluster_address": "https://10.81.0.2:8201"},
            "units": {"vault": "active", "node-exporter": "active", "process-exporter": "active", "vector": "active"},
            "gateway": (
                {"exists": True, "enrolled": True, "public_key": GATEWAY_KEY}
                if index == 0 else {"exists": False, "enrolled": False, "public_key": ""}
            ),
            "storage_type": "raft",
            "version": "1.21.4",
            "free_mb": 20000,
            "listeners": [],
        }
        for index, node in enumerate(nodes)
    }
    return contract, probes


def fresh_install(probes):
    for state in probes.values():
        state["health"] = {"initialized": False, "sealed": True, "standby": True}
        state["leader"] = {"errors": ["Vault is sealed"]}


class VerifyVaultStageTests(unittest.TestCase):
    def test_access_requires_ssh_sudo_and_no_swap(self):
        contract, probes = fixture()
        module.verify(contract, ["access"], probes)
        probes["vault-1"]["swap_kb"] = 1024
        with self.assertRaisesRegex(ValueError, "swap"):
            module.verify(contract, ["access"], probes)
        probes["vault-1"]["swap_kb"] = 0
        probes["vault-2"] = {"reachable": False}
        with self.assertRaisesRegex(ValueError, "SSH probe failed"):
            module.verify(contract, ["access"], probes)

    def test_access_reports_but_does_not_block_on_the_retiring_source_swap(self):
        contract, probes = migration_fixture()
        probes["legacy"]["swap_kb"] = 4194300
        module.verify(contract, ["access"], probes)
        probes["vault-1"]["swap_kb"] = 1024
        with self.assertRaisesRegex(ValueError, "vault-1: swap"):
            module.verify(contract, ["access"], probes)

    def test_leader_stage_accepts_fresh_nodes_but_not_a_split_cluster(self):
        contract, probes = fixture()
        fresh_install(probes)
        module.verify(contract, ["access", "no-foreign-cluster"], probes)
        contract, probes = fixture()
        probes["vault-2"]["health"]["cluster_id"] = "cluster-b"
        with self.assertRaisesRegex(ValueError, "different Vault cluster IDs"):
            module.verify(contract, ["no-foreign-cluster"], probes)

    def test_peer_unsealed_before_leader_is_rejected(self):
        contract, probes = fixture()
        probes["vault-0"]["health"] = {"initialized": False, "sealed": True, "standby": True}
        with self.assertRaisesRegex(ValueError, "leader is uninitialized"):
            module.verify(contract, ["no-foreign-cluster"], probes)

    def test_peers_require_manually_unsealed_leader(self):
        contract, probes = fixture()
        fresh_install(probes)
        probes["vault-0"]["health"] = {"initialized": True, "sealed": False, "standby": False, "cluster_id": "c"}
        module.verify(contract, ["leader-unsealed"], probes)
        probes["vault-0"]["health"]["sealed"] = True
        with self.assertRaisesRegex(ValueError, "initialized and unsealed by an operator"):
            module.verify(contract, ["leader-unsealed"], probes)

    def test_running_checks_need_a_vault_listener_and_active_unit(self):
        contract, probes = fixture()
        fresh_install(probes)
        module.verify(contract, ["leader-running", "peers-running"], probes)
        probes["vault-1"]["health"] = None
        with self.assertRaisesRegex(ValueError, "vault-1: Vault is not answering"):
            module.verify(contract, ["peers-running"], probes)

    def test_raft_quorum_requires_one_cluster_one_leader_on_a_private_address(self):
        contract, probes = fixture()
        module.verify(contract, ["raft-quorum"], probes)
        probes["vault-2"]["health"]["cluster_id"] = "cluster-b"
        with self.assertRaisesRegex(ValueError, "not one Raft cluster"):
            module.verify(contract, ["raft-quorum"], probes)

    def test_raft_quorum_rejects_sealed_or_extra_active_nodes(self):
        contract, probes = fixture()
        probes["vault-1"]["health"]["sealed"] = True
        with self.assertRaisesRegex(ValueError, "manually unsealed"):
            module.verify(contract, ["raft-quorum"], probes)
        contract, probes = fixture()
        probes["vault-1"]["health"]["standby"] = False
        with self.assertRaisesRegex(ValueError, "exactly one active"):
            module.verify(contract, ["raft-quorum"], probes)

    def test_raft_quorum_rejects_a_leader_outside_the_declared_nodes(self):
        contract, probes = fixture()
        for state in probes.values():
            state["leader"]["leader_cluster_address"] = "https://10.9.9.9:8201"
        with self.assertRaisesRegex(ValueError, "not a declared node"):
            module.verify(contract, ["raft-quorum"], probes)
        contract, probes = fixture()
        probes["vault-2"]["leader"]["leader_cluster_address"] = "https://10.81.0.3:8201"
        with self.assertRaisesRegex(ValueError, "disagree"):
            module.verify(contract, ["raft-quorum"], probes)

    def test_monitoring_and_gateway_checks(self):
        contract, probes = fixture()
        module.verify(contract, ["monitoring-running", "gateway-enrolled"], probes)
        probes["vault-1"]["units"]["vector"] = "inactive"
        with self.assertRaisesRegex(ValueError, "vector"):
            module.verify(contract, ["monitoring-running"], probes)
        probes["vault-0"]["gateway"] = {"exists": False, "enrolled": False, "public_key": ""}
        with self.assertRaisesRegex(ValueError, "not enrolled"):
            module.verify(contract, ["gateway-enrolled"], probes)

    def test_init_alone_is_identity_not_enrollment(self):
        contract, probes = fixture()
        probes["vault-0"]["gateway"] = {"exists": True, "enrolled": False, "public_key": GATEWAY_KEY}
        module.verify(contract, ["gateway-identity"], probes)
        with self.assertRaisesRegex(ValueError, "not enrolled"):
            module.verify(contract, ["gateway-enrolled"], probes)
        probes["vault-0"]["gateway"]["public_key"] = "not-a-key"
        with self.assertRaisesRegex(ValueError, "no local WireGuard identity"):
            module.verify(contract, ["gateway-identity"], probes)

    def test_gateway_running_needs_enrollment_and_both_units(self):
        contract, probes = fixture()
        probes["vault-0"]["units"].update({"xconnect-gateway-xray": "active", "xconnect-gateway-sync.timer": "active"})
        module.verify(contract, ["gateway-running"], probes)
        probes["vault-0"]["units"]["xconnect-gateway-sync.timer"] = "inactive"
        with self.assertRaisesRegex(ValueError, "sync.timer"):
            module.verify(contract, ["gateway-running"], probes)
        probes["vault-0"]["units"]["xconnect-gateway-sync.timer"] = "active"
        probes["vault-0"]["gateway"]["enrolled"] = False
        with self.assertRaisesRegex(ValueError, "not enrolled"):
            module.verify(contract, ["gateway-running"], probes)

    def test_remote_probe_never_prints_the_device_credential(self):
        probe_source = module.REMOTE_PROBE
        self.assertIn('"enrolled": bool(credential)', probe_source)
        self.assertNotIn('"credential": credential', probe_source)

    def test_rejects_unknown_checks_and_reports_state(self):
        contract, probes = fixture()
        with self.assertRaisesRegex(ValueError, "unknown checks"):
            module.verify(contract, ["arbitrary-shell"], probes)
        report = module.summary(contract, probes, "Before test")
        self.assertIn("| vault-0 | - | 10.81.0.2 | - | gateway enrolled; no overlay IP | active | raft | 1.21.4 | cluster- |", report)

    def test_report_shows_address_user_and_live_overlay_ip(self):
        contract, probes = fixture()
        contract["spec"]["nodes"][1].update({"address": "34.1.2.3", "ssh_user": "sa_1"})
        probes["vault-1"]["overlay"] = [{"interface": "xconone0", "address": "10.79.0.3"}]
        report = module.summary(contract, probes, "State")
        self.assertIn("| vault-1 | 34.1.2.3 | 10.81.0.3 | sa_1 | overlay 10.79.0.3 | standby |", report)
        probes["vault-0"]["gateway"] = {"exists": True, "enrolled": False, "public_key": "A" * 43 + "="}
        self.assertIn("| vault-0 | - | 10.81.0.2 | - | gateway identity; no overlay IP |", module.summary(contract, probes, "State"))
        probes["vault-2"] = {"reachable": False}
        self.assertIn("| vault-2 | - | 10.81.0.4 | - | - | unreachable |", module.summary(contract, probes, "State"))


def migration_fixture():
    contract, probes = fixture()
    for node in contract["spec"]["nodes"]:
        node["groups"] = [group for group in node["groups"] if group != "vault_shared_leader"]
        if "vault_shared_peers" not in node["groups"]:
            node["groups"].append("vault_shared_peers")
    legacy = {
        "id": "legacy", "private_address": "10.79.0.10", "overlay_address": "10.79.0.10",
        "groups": ["vault_legacy_source", "vault_shared_leader", "vault_single_node"],
    }
    contract["spec"]["nodes"].append(legacy)
    probes["legacy"] = {
        "reachable": True, "sudo": True, "swap_kb": 0, "free_mb": 20000,
        "health": {"initialized": True, "sealed": False, "cluster_id": "cluster-a", "standby": False},
        "leader": {"leader_cluster_address": "https://10.79.0.10:8201"},
        "storage_type": "raft", "version": "1.20.0", "units": {"vault": "active"},
        "init_file": True, "port_guard": True,
        "listeners": [{"address": "0.0.0.0", "port": 8200}],
    }
    for node_id in ("vault-0", "vault-1", "vault-2"):
        probes[node_id]["health"]["standby"] = True
        probes[node_id]["leader"]["leader_cluster_address"] = "https://10.79.0.10:8201"
    return contract, probes


class MigrationCheckTests(unittest.TestCase):
    def test_preflight_reports_the_on_disk_key_and_checks_disk(self):
        contract, probes = migration_fixture()
        warnings = module.verify(contract, ["legacy-unsealed", "legacy-report"], probes)
        self.assertTrue(any("rekey" in warning for warning in warnings))
        probes["legacy"]["free_mb"] = 100
        with self.assertRaisesRegex(ValueError, "MiB free"):
            module.verify(contract, ["legacy-report"], probes)
        probes["legacy"]["free_mb"] = 20000
        probes["legacy"]["storage_type"] = "consul"
        with self.assertRaisesRegex(ValueError, "unsupported source storage"):
            module.verify(contract, ["legacy-report"], probes)

    def test_conversion_needs_an_overlay_address_and_a_port_guard(self):
        contract, probes = migration_fixture()
        module.verify(contract, ["legacy-overlay", "vault-port-guard"], probes)
        probes["legacy"]["port_guard"] = False
        with self.assertRaisesRegex(ValueError, "vault_port_guard"):
            module.verify(contract, ["vault-port-guard"], probes)
        probes["legacy"]["listeners"] = [{"address": "10.79.0.10", "port": 8201}, {"address": "127.0.0.1", "port": 8200}]
        module.verify(contract, ["vault-port-guard"], probes)
        del contract["spec"]["nodes"][-1]["overlay_address"]
        with self.assertRaisesRegex(ValueError, "overlay address"):
            module.verify(contract, ["legacy-overlay"], probes)

    def test_join_needs_an_active_raft_source(self):
        contract, probes = migration_fixture()
        module.verify(contract, ["legacy-raft"], probes)
        probes["legacy"]["storage_type"] = "postgresql"
        with self.assertRaisesRegex(ValueError, "migrate-convert"):
            module.verify(contract, ["legacy-raft"], probes)

    def test_join_rejects_a_source_advertising_its_old_vpc_address(self):
        contract, probes = migration_fixture()
        probes["legacy"]["leader"]["leader_cluster_address"] = "https://10.81.0.4:8201"
        with self.assertRaisesRegex(ValueError, "not the declared XConnect overlay"):
            module.verify(contract, ["legacy-raft"], probes)

    def test_peers_join_one_at_a_time_and_each_is_unsealed_before_the_next(self):
        contract, probes = migration_fixture()
        for node_id in ("vault-0", "vault-1", "vault-2"):
            probes[node_id]["health"] = None
        self.assertEqual(module.next_peer(contract, probes)["id"], "vault-0")
        # vault-0 joined but waits for its manual unseal: nothing else may join.
        probes["vault-0"]["health"] = {"initialized": True, "sealed": True, "standby": True}
        with self.assertRaisesRegex(ValueError, "vault-0 joined but is still sealed"):
            module.verify(contract, ["next-peer"], probes)
        probes["vault-0"]["health"] = {"initialized": True, "sealed": False, "standby": True, "cluster_id": "cluster-a"}
        self.assertEqual(module.next_peer(contract, probes)["id"], "vault-1")
        for node_id in ("vault-1", "vault-2"):
            probes[node_id]["health"] = {"initialized": True, "sealed": False, "standby": True, "cluster_id": "cluster-a"}
        with self.assertRaisesRegex(ValueError, "already joined"):
            module.verify(contract, ["next-peer"], probes)

    def test_selected_running_checks_only_the_changed_node(self):
        contract, probes = migration_fixture()
        probes["vault-2"]["health"] = None
        probes["vault-2"]["units"]["vault"] = "inactive"
        probes["vault-1"]["health"] = {"initialized": True, "sealed": True, "standby": True}
        module.verify(contract, ["selected-running"], probes, selected=["vault-1"])
        with self.assertRaisesRegex(ValueError, "vault-2"):
            module.verify(contract, ["selected-running"], probes, selected=["vault-2"])
        with self.assertRaisesRegex(ValueError, "no node was selected"):
            module.verify(contract, ["selected-running"], probes)
        probes["vault-1"]["health"] = {"initialized": False, "sealed": True, "standby": True}
        with self.assertRaisesRegex(ValueError, "has not joined"):
            module.verify(contract, ["selected-running"], probes, selected=["vault-1"])

    def test_overlay_raft_path_must_reach_every_node_and_the_old_node_must_answer(self):
        contract, probes = migration_fixture()
        for node in contract["spec"]["nodes"]:
            targets = module.raft_targets(contract, node).split(",")
            self.assertNotIn(f"{node['private_address']}:8201", targets)
            probes[node["id"]]["reach"] = {
                target: ("open" if target.startswith("10.79.0.10:") else "refused") for target in targets
            }
        module.verify(contract, ["overlay-raft-path"], probes)
        probes["vault-1"]["reach"]["10.79.0.10:8201"] = "refused"
        with self.assertRaisesRegex(ValueError, "legacy does not answer on 10.79.0.10:8201 from vault-1"):
            module.verify(contract, ["overlay-raft-path"], probes)
        probes["vault-1"]["reach"]["10.79.0.10:8201"] = "open"
        probes["legacy"]["reach"]["10.81.0.4:8201"] = "blocked"
        with self.assertRaisesRegex(ValueError, "legacy cannot reach vault-2 at 10.81.0.4:8201"):
            module.verify(contract, ["overlay-raft-path"], probes)

    def test_join_needs_every_raft_address_on_the_overlay(self):
        contract, _ = migration_fixture()
        for node in contract["spec"]["nodes"]:
            node["overlay_address"] = node["private_address"]
        module.verify(contract, ["raft-overlay"], {})
        del contract["spec"]["nodes"][1]["overlay_address"]
        with self.assertRaisesRegex(ValueError, "vault-1: no XConnect overlay IP recorded"):
            module.verify(contract, ["raft-overlay"], {})

    def test_probe_targets_are_validated(self):
        self.assertTrue(module.SAFE_TARGETS.fullmatch("10.79.0.1:8200,10.79.0.1:8201"))
        self.assertTrue(module.SAFE_TARGETS.fullmatch(""))
        self.assertFalse(module.SAFE_TARGETS.fullmatch("10.79.0.1:8200;rm -rf /"))

    def test_quorum_includes_the_source_until_it_is_removed(self):
        contract, probes = migration_fixture()
        module.verify(contract, ["raft-quorum", "leader-unsealed"], probes)
        with self.assertRaisesRegex(ValueError, "leadership did not move"):
            module.verify(contract, ["legacy-standby"], probes)
        probes["legacy"]["health"]["standby"] = True
        probes["vault-1"]["health"]["standby"] = False
        for state in probes.values():
            state["leader"]["leader_cluster_address"] = "https://10.81.0.3:8201"
        module.verify(contract, ["legacy-standby", "raft-quorum"], probes)
        probes["legacy"] = {"reachable": True, "health": None}
        module.verify(contract, ["raft-quorum-new"], probes)
        with self.assertRaisesRegex(ValueError, "manually unsealed"):
            module.verify(contract, ["raft-quorum"], probes)

    def test_new_node_monitoring_ignores_the_source(self):
        contract, probes = migration_fixture()
        module.verify(contract, ["monitoring-running"], probes)


class CutoverAndServiceTests(unittest.TestCase):
    def test_removal_waits_for_the_service_dns(self):
        contract, _ = migration_fixture()
        contract["spec"]["nodes"][-1]["address"] = "old.example"
        original = module.addresses
        try:
            module.addresses = lambda host: {"vault.example": {"46.0.0.1"}, "old.example": {"46.0.0.1"}}[host]
            with self.assertRaisesRegex(ValueError, "still resolves to legacy"):
                module.verify(contract, ["service-dns-moved"], {}, vault_addr="https://vault.example")
            module.addresses = lambda host: {"vault.example": {"34.1.1.1"}, "old.example": {"46.0.0.1"}}[host]
            module.verify(contract, ["service-dns-moved"], {}, vault_addr="https://vault.example")
            module.addresses = lambda host: {"vault.example": set(), "old.example": {"46.0.0.1"}}[host]
            with self.assertRaisesRegex(ValueError, "still resolves"):
                module.verify(contract, ["service-dns-moved"], {}, vault_addr="https://vault.example")
        finally:
            module.addresses = original

    def test_observation_window_is_declared_and_elapsed(self):
        now = module.datetime(2026, 10, 2, 12, 0, tzinfo=module.timezone.utc)
        with self.assertRaisesRegex(ValueError, "dns_switched_at"):
            module.check_observation_window({}, now)
        with self.assertRaisesRegex(ValueError, "runs until 2026-10-02T08:00:00"):
            module.check_observation_window({"dns_switched_at": "2026-10-01T08:00:00+00:00", "hours": 24}, now.replace(hour=7))
        module.check_observation_window({"dns_switched_at": "2026-10-01T08:00:00+00:00", "hours": 24}, now)
        module.check_observation_window({"dns_switched_at": "2026-10-02T00:00:00Z", "hours": 6}, now)
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            module.check_observation_window({"dns_switched_at": "2026-10-01T08:00:00"}, now)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            module.check_observation_window({"dns_switched_at": "2026-10-01T08:00:00Z", "hours": 0}, now)

    def test_service_endpoint_answers_as_the_declared_cluster(self):
        contract, probes = fixture()
        original = module.service_health
        try:
            module.service_health = lambda address: {"initialized": True, "sealed": False, "cluster_id": "cluster-a"}
            module.verify(contract, ["service-endpoint"], probes, vault_addr="https://vault.example")
            module.service_health = lambda address: {"initialized": True, "sealed": False, "cluster_id": "other"}
            with self.assertRaisesRegex(ValueError, "different Vault cluster"):
                module.verify(contract, ["service-endpoint"], probes, vault_addr="https://vault.example")
            module.service_health = lambda address: {}
            with self.assertRaisesRegex(ValueError, "not answering"):
                module.verify(contract, ["service-endpoint"], probes, vault_addr="https://vault.example")
            with self.assertRaisesRegex(ValueError, "https"):
                module.verify(contract, ["service-endpoint"], probes, vault_addr="http://vault.example")
        finally:
            module.service_health = original


if __name__ == "__main__":
    unittest.main()
