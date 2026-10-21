#!/usr/bin/env python3
"""Reject two active exporter processes claiming the same node/transport ID."""
import json
import os
import unittest
from pathlib import Path


def duplicate_claims(records):
    claims = {}
    for record in records:
        if not record.get("active", True):
            continue
        identity = (record["node_id"], record["transport"])
        claims.setdefault(identity, set()).add(record["source"])
    return {identity: sources for identity, sources in claims.items() if len(sources) > 1}


class DuplicateProducerIdentityTest(unittest.TestCase):
    def test_distinct_transports_on_one_node_are_valid(self):
        records = [
            {"node_id": "jp-node", "transport": "xhttp", "source": "host-a"},
            {"node_id": "jp-node", "transport": "tcp", "source": "host-a"},
        ]
        self.assertEqual(duplicate_claims(records), {})

    def test_two_active_sources_claiming_same_identity_fail(self):
        records = [
            {"node_id": "jp-node", "transport": "xhttp", "source": "host-a"},
            {"node_id": "jp-node", "transport": "xhttp", "source": "host-b"},
        ]
        self.assertIn(("jp-node", "xhttp"), duplicate_claims(records))

    def test_inactive_old_producer_does_not_block(self):
        records = [
            {"node_id": "jp-node", "transport": "xhttp", "source": "host-a"},
            {"node_id": "jp-node", "transport": "xhttp", "source": "host-old", "active": False},
        ]
        self.assertEqual(duplicate_claims(records), {})

    def test_optional_json_inventory_fails_on_duplicate(self):
        inventory = os.environ.get("V4_PRODUCER_INVENTORY")
        if not inventory:
            self.skipTest("set V4_PRODUCER_INVENTORY to a sanitized producer inventory JSON file")
        records = json.loads(Path(inventory).read_text())
        duplicates = duplicate_claims(records)
        self.assertFalse(duplicates, f"duplicate producer identities: {sorted(duplicates)}")


if __name__ == "__main__":
    unittest.main()
