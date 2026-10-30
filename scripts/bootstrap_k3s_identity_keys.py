#!/usr/bin/env python3
"""Run master-local K3s identity initialization (no pre-provisioned credentials)."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'roles/vhosts/k3s_platform_bootstrap/files/bootstrap_k3s_identity_keys.py'), run_name='__main__')
