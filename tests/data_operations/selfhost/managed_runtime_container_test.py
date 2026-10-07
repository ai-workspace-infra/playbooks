#!/usr/bin/env python3
"""GitHub-hosted, fixed-source binary fixtures; no published/PROD image claim."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'scripts/data_operations/selfhost'))
import managed_runtime_host as HOST

COMMITS = dict(accounts='fabe68a85b4d90826dccdfe6a2116ef025f475d4',
               billing='f11e875f74cbe0154e49f6310b6697eff65f731e')
PHASE = 'preflight'


def run(argv, **kwargs):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=300, **kwargs)
    if result.returncode != 0:
        raise RuntimeError('Disposable managed runtime command failed')
    return result.stdout.strip()


def main():
    global PHASE
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('Disposable GitHub-hosted qualification required')
    for name in COMMITS:
        checkout = Path(os.environ[name.upper()+'_CHECKOUT'])
        if run(['git','-C',str(checkout),'rev-parse','HEAD']) != COMMITS[name]:
            raise RuntimeError('Fixed source fixture differs')
    for name in COMMITS:
        container='managed-runtime-probe-'+uuid.uuid4().hex
        fixture='managed-runtime-fixture-'+name+':'+uuid.uuid4().hex
        service=dict(commit=COMMITS[name], image='ghcr.io/ai-workspace-services/'+HOST.SERVICES[name][0]+':sha-'+COMMITS[name],
                     image_digest='sha256:'+'0'*64)
        try:
            with tempfile.TemporaryDirectory() as directory:
                work=Path(directory)
                binary=Path(os.environ[name.upper()+'_BINARY'])
                (work/'service').write_bytes(binary.read_bytes());(work/'service').chmod(0o755)
                PHASE=name+'_build_fixture'
                if name=='accounts':
                    dockerfile='FROM ubuntu:24.04\nRUN apt-get update && apt-get install -y --no-install-recommends netcat-openbsd && rm -rf /var/lib/apt/lists/*\nCOPY service /usr/local/bin/account\nENTRYPOINT ["/bin/false"]\n'
                else:
                    dockerfile='FROM alpine:3.22\nRUN addgroup -S app && adduser -S app -G app\nCOPY service /app/billing-service\nUSER app\nENTRYPOINT ["/bin/false"]\n'
                (work/'Dockerfile').write_text(dockerfile)
                run(['docker','build','-t',fixture,str(work)])
                argv=HOST.standby_argv(name,service,container)
                # Only this disposable image reference is replaced. Production
                # execute/qualify always uses the reviewed full-SHA digest.
                argv[argv.index(service['image']+'@'+service['image_digest'])]=fixture
                PHASE=name+'_standby_start'
                run(argv)
                PHASE=name+'_isolated_container_contract'
                HOST.inspect_container(name,service,container,expected_image=fixture)
                PHASE=name+'_release_and_business_probes'
                HOST.verify_probes(name,service,container)
                print(name+': fixed-source managed standby, overridden entrypoint, network none, read-only, no DB credential, health/503 and all business verbs passed; fixture only.')
        finally:
            HOST.remove_execution_container(container)
            subprocess.run(['docker','image','rm','--force',fixture],capture_output=True,text=True,timeout=30)
    print('Both managed runtime source fixtures qualified; no published image pull, host deployment, schema/data equality or cutover acceptance.')


if __name__=='__main__':
    try:
        main()
    except Exception:
        print('Disposable managed runtime qualification failed at '+PHASE+'; raw output withheld.',file=sys.stderr)
        sys.exit(1)
