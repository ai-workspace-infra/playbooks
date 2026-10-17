import importlib.util
import json
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "doco_check", Path(__file__).parents[1] / "scripts/check-doco-release.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class DocoReleaseGateTest(unittest.TestCase):
    def test_prepared_source_is_not_completed_deployment(self):
        event = dict(msg="source prepared", revision="wanted", target="prod-v2", job_id="new")
        self.assertFalse(check.completed_release([json.dumps(event)], "wanted", "prod-v2"))

    def test_other_jobs_cannot_satisfy_selected_release(self):
        lines = [json.dumps(dict(msg="source prepared", revision="wanted", target="prod-v2", job_id="new")),
                 json.dumps(dict(msg="job completed successfully", target="prod-v2", job_id="old"))]
        self.assertFalse(check.completed_release(lines, "wanted", "prod-v2"))
        lines.append(json.dumps(dict(msg="job completed successfully", target="prod-v2", job_id="new")))
        self.assertTrue(check.completed_release(lines, "wanted", "prod-v2"))


if __name__ == "__main__":
    unittest.main()
