import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import yaml


SOURCE = Path(__file__).parents[1] / "files" / "model-allowlist-stage.py"
spec = importlib.util.spec_from_file_location("model_allowlist_stage", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ModelAllowlistStageTests(unittest.TestCase):
 def test_model_rules_preserve_existing_auth_and_other_routes(self):
    bundle = {
        "consumers": [{"username": "personal", "plugins": {"key-auth": {"key": "dummy"}}}],
        "routes": [
            {"id": "new-api", "uri": "/*", "priority": 1, "plugins": {
                "key-auth": {"header": "apikey"},
                "consumer-restriction": {"whitelist": ["personal"]},
                "ip-restriction": {"whitelist": ["127.0.0.1/32"]},
                "proxy-rewrite": {"headers": {"set": {"Authorization": "dummy"}}},
            }},
            {"id": "litellm-rollback", "uri": "/litellm/*", "plugins": {}},
        ],
    }
    runtime = {"plugins": ["key-auth", "proxy-rewrite"]}
    models = ["gpt-5.6-luna", "claude-sonnet-5"]
    result, config = module.apply_policy(bundle, runtime, models)

    assert [route["id"] for route in result["routes"]] == [
        "new-api", "litellm-rollback", "new-api-models-allowlist", "new-api-inference-allowlist"
    ]
    catalog = result["routes"][2]
    inference = result["routes"][3]
    assert catalog["uri"] == "/v1/models"
    assert catalog["plugins"]["response-rewrite"]["vars"] == [["status", "==", 200]]
    assert "status_code" not in catalog["plugins"]["response-rewrite"]
    assert [entry["id"] for entry in json.loads(catalog["plugins"]["response-rewrite"]["body"])["data"]] == models
    assert inference["plugins"]["request-validation"]["body_schema"]["properties"]["model"]["enum"] == models
    assert inference["plugins"]["key-auth"]["header"] == "apikey"
    assert inference["plugins"]["ip-restriction"]["whitelist"] == ["127.0.0.1/32"]
    assert "response-rewrite" in config["plugins"]
    assert "request-validation" in config["plugins"]

    again, config = module.apply_policy(result, config, models)
    assert len(again["routes"]) == 4
    assert config["plugins"].count("request-validation") == 1


 def test_atomic_runtime_config_is_idempotent(self):
    with tempfile.TemporaryDirectory() as temporary:
        destination = Path(temporary) / "apisix.yaml"
        destination.write_text("routes: []\n#END\n")
        destination.chmod(0o640)
        assert module.atomic_yaml(destination, {"routes": [{"id": "test"}]})
        assert destination.read_text().endswith("#END\n")
        assert yaml.safe_load(destination.read_text())["routes"][0]["id"] == "test"
        assert not module.atomic_yaml(destination, {"routes": [{"id": "test"}]})


if __name__ == "__main__":
    unittest.main()
