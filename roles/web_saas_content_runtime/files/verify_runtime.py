#!/usr/bin/env python3
"""Validate Content runtime wiring without emitting environment values."""

import argparse
import hmac
import json
import sys


EXPECTED_NAMES = {
    "/web-saas-accounts",
    "/web-saas-billing",
    "/web-saas-console",
    "/web-saas-content",
}
INTERNAL_NETWORK = "web_saas_recovery"
CONTENT_EGRESS_NETWORK = "web_saas_content_egress"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-image", required=True)
    args = parser.parse_args()

    try:
        rows = json.load(sys.stdin)
        containers = {row["Name"]: row for row in rows}
        if set(containers) != EXPECTED_NAMES:
            raise ValueError

        envs = {
            name: dict(
                item.split("=", 1)
                for item in row["Config"].get("Env", [])
                if "=" in item
            )
            for name, row in containers.items()
        }
        tokens = [envs[name].get("INTERNAL_SERVICE_TOKEN", "") for name in EXPECTED_NAMES]
        token_consistent = bool(tokens[0]) and all(
            hmac.compare_digest(tokens[0], token) for token in tokens[1:]
        )

        content = containers["/web-saas-content"]
        content_env = envs["/web-saas-content"]
        console_env = envs["/web-saas-console"]
        content_networks = set(content["NetworkSettings"]["Networks"])
        app_networks = {
            name: set(row["NetworkSettings"]["Networks"])
            for name, row in containers.items()
            if name != "/web-saas-content"
        }

        checks = {
            "content_image_matches": content["Config"].get("Image") == args.expected_image,
            "content_healthy": content["State"].get("Running") is True
            and content["State"].get("Health", {}).get("Status") == "healthy",
            "applications_running": all(
                row["State"].get("Running") is True for row in containers.values()
            ),
            "shared_token_consistent": token_consistent,
            "console_uses_internal_content_url": console_env.get("DOCS_SERVICE_URL")
            == "http://content:8084"
            and console_env.get("RUNTIME_DOCS_SERVICE_URL") == "http://content:8084",
            "content_uses_internal_service_token": bool(
                content_env.get("INTERNAL_SERVICE_TOKEN")
            ),
            "content_network_is_scoped": INTERNAL_NETWORK in content_networks
            and CONTENT_EGRESS_NETWORK in content_networks
            and all(
                INTERNAL_NETWORK in networks
                and CONTENT_EGRESS_NETWORK not in networks
                for networks in app_networks.values()
            ),
        }
        if not all(checks.values()):
            raise ValueError

        print(
            json.dumps(
                {
                    "status": "passed",
                    **checks,
                    "content_image_digest": args.expected_image.rsplit("@", 1)[1],
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception:
        print("Content runtime verification failed; secret values were suppressed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
