#!/usr/bin/env python3
"""Read one non-secret runtime grant specification and emit a review plan.

This command is deliberately offline and supports no apply operation. It emits
no input values on errors, because caller-owned request context may be sensitive.
"""
import json
import sys

from runtime_object_grants import plan_runtime_grants


def main():
    try:
        spec = json.load(sys.stdin)
        plan = plan_runtime_grants(spec)
    except (ValueError, TypeError, json.JSONDecodeError):
        print('invalid runtime grant specification', file=sys.stderr)
        return 1
    json.dump({'apiVersion': 'RuntimeObjectGrantPlan/v1', 'sha256': plan.sha256,
               'statements': list(plan.statements), 'execution': 'not_implemented'}, sys.stdout)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
