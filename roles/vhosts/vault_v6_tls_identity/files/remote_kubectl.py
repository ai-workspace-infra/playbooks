"""Narrow SSH-backed kubectl adapter for the V6 identity collector.

The caller must obtain host and known_hosts from an independently verified CMDB
and provider host-key attestation. SSH agent authentication is supplied by the
caller. No credential values or kubeconfig are copied to disk or logged.
"""
import ipaddress
from pathlib import Path
import shlex
import subprocess

from cluster_identity import AUDIENCE, DENIED_AUDIENCE, NAMESPACE, SA, ClusterError


class RemoteKubectl:
    def __init__(self, host, known_hosts):
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise ClusterError('verified node address required') from None
        if address.version != 4 or address.is_unspecified or address.is_multicast:
            raise ClusterError('invalid node address')
        path = Path(known_hosts).resolve()
        if not path.is_file() or path.stat().st_size == 0:
            raise ClusterError('provider-attested host keys required')
        self.host, self.known_hosts = str(address), str(path)

    def __call__(self, *args):
        fixed = {
            ('get', 'namespace', 'kube-system', '-o', 'json'),
            ('get', 'nodes', '-o', 'json'),
            ('get', 'serviceaccount', SA, '-n', NAMESPACE, '-o', 'json'),
            ('get', 'serviceaccount', 'default', '-n', NAMESPACE, '-o', 'json'),
            ('get', '--raw', '/.well-known/openid-configuration'),
            ('get', '--raw', '/openid/v1/jwks'),
            ('create', 'token', SA, '-n', NAMESPACE, '--audience', AUDIENCE, '--duration', '600s'),
            ('create', 'token', SA, '-n', NAMESPACE, '--audience', DENIED_AUDIENCE, '--duration', '600s'),
            ('create', 'token', 'default', '-n', NAMESPACE, '--audience', AUDIENCE, '--duration', '600s'),
        }
        if tuple(args) not in fixed:
            raise ClusterError('kubectl operation outside identity allowlist')
        remote = shlex.join(('k3s', 'kubectl', *args))
        command = ['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes',
                   '-o', 'StrictHostKeyChecking=yes', '-o', 'GlobalKnownHostsFile=/dev/null',
                   '-o', 'UserKnownHostsFile=' + self.known_hosts,
                   '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
                   '-o', 'PermitLocalCommand=no', '-o', 'PasswordAuthentication=no',
                   '-o', 'ConnectTimeout=15', '-l', 'root', self.host, remote]
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=45, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ClusterError('verified host operation failed') from None
        if result.returncode:
            raise ClusterError('verified host operation failed')
        return result.stdout.strip()
