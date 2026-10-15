"""Run the configuration API and synchronizer as one systemd unit.

If either child exits, the supervisor terminates its sibling and exits non-zero
so systemd restarts the complete configuration plane as one failure domain.
"""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


root = Path(os.environ["GATEWAY_ROOT"])
config_root = Path(os.environ.get("GATEWAY_CONFIG_ROOT", root / "config"))
state_root = Path(os.environ.get("GATEWAY_STATE_ROOT", root / "state"))
install_root = Path(os.environ.get("GATEWAY_INSTALL_ROOT", "/opt/apisix-pg-standalone"))
gateway_id = os.environ["GATEWAY_GATEWAY_ID"]
bootstrap = config_root / "config.yaml"
validator = install_root / "validate-native.sh"
binary = install_root / "gateway_config.py"
probe = f"{sys.executable} {install_root / 'probe-local.py'}"
api = "http://127.0.0.1:" + os.environ.get("GATEWAY_CONFIG_API_PORT", "19089")

children = []
stopping = False


def stop_all(_signum=None, _frame=None):
    global stopping
    stopping = True
    for child in children:
        if child.poll() is None:
            child.terminate()


signal.signal(signal.SIGTERM, stop_all)
signal.signal(signal.SIGINT, stop_all)

try:
    children.append(subprocess.Popen([
        sys.executable, str(binary), "serve", "--gateway", gateway_id,
        "--bootstrap", str(bootstrap), "--validator", str(validator),
        "--port", os.environ.get("GATEWAY_CONFIG_API_PORT", "19089")]))
    children.append(subprocess.Popen([
        sys.executable, str(binary), "sync", "--gateway", gateway_id,
        "--bootstrap", str(bootstrap), "--api", api,
        "--validator", str(validator), "--output",
        str(config_root / "apisix.yaml"), "--state",
        str(state_root / "state.json"), "--probe", probe]))
except Exception:
    stop_all()
    for child in children:
        child.wait(timeout=20)
    raise

try:
    while True:
        for child in children:
            code = child.poll()
            if code is not None:
                stop_all()
                time.sleep(0.5)
                raise SystemExit(0 if stopping else (code or 1))
        time.sleep(0.5)
finally:
    stop_all()
    for child in children:
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
