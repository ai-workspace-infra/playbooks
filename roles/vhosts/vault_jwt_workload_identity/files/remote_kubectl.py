"""Narrow SSH-backed kubectl adapter for the workload identity collector.

The caller must obtain host and known_hosts from an independently verified CMDB
and provider host-key attestation. SSH agent authentication is supplied by the
caller. No credential values or kubeconfig are copied to disk or logged.
"""
import ipaddress
from pathlib import Path
import shlex
import re
import subprocess

from cluster_identity import ClusterError


class RemoteKubectl:
    def __init__(self, host, known_hosts, identity, runtime, ssh_user, env=None):
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise ClusterError('verified node address required') from None
        if address.is_unspecified or address.is_multicast:
            raise ClusterError('invalid node address')
        path = Path(known_hosts).resolve()
        if not path.is_file() or path.stat().st_size == 0:
            raise ClusterError('provider-attested host keys required')
        if runtime not in {'k3s', 'kubernetes'}:
            raise ClusterError('unsupported Kubernetes runtime')
        if not isinstance(ssh_user, str) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', ssh_user):
            raise ClusterError('invalid SSH management user')
        self.host, self.known_hosts, self.identity, self.runtime, self.ssh_user, self.env = str(address), str(path), identity, runtime, ssh_user, env

    def __call__(self, *args):
        identity = self.identity
        fixed = {
            ('get', 'namespace', 'kube-system', '-o', 'json'),
            ('get', 'nodes', '-o', 'json'),
            ('get', 'gitrepositories,kustomizations,helmreleases', '-A', '-o', 'json'),
            ('get', 'gitrepository', 'config-prod', '-n', 'flux-system', '-o', 'json'),
            ('get', 'gitrepository', 'vault-prod-config', '-n', 'flux-system', '-o', 'json'),
            ('get', 'kustomization', 'prod-k3s-v6', '-n', 'flux-system', '-o', 'json'),
            ('get', 'kustomization', 'external-secrets', '-n', 'flux-system', '-o', 'json'),
            ('get', 'helmrelease', 'external-secrets', '-n', 'external-secrets', '-o', 'json'),
            ('get', 'kustomization', 'vault-tls-identity', '-n', 'flux-system', '-o', 'json'),
            ('get', 'serviceaccount', identity.service_account, '-n', identity.namespace, '-o', 'json'),
            ('get', 'serviceaccount', identity.probe_service_account, '-n', identity.namespace, '-o', 'json'),
            ('get', '--raw', '/.well-known/openid-configuration'),
            ('get', '--raw', '/openid/v1/jwks'),
            ('create', 'token', identity.service_account, '-n', identity.namespace, '--audience', identity.audience, '--duration', '600s'),
            ('create', 'token', identity.service_account, '-n', identity.namespace, '--audience', identity.denied_audience, '--duration', '600s'),
            ('create', 'token', identity.probe_service_account, '-n', identity.namespace, '--audience', identity.audience, '--duration', '600s'),
        }
        if tuple(args) not in fixed:
            raise ClusterError('kubectl operation outside identity allowlist')
        executable = ('env', '-u', 'KUBECONFIG', 'k3s', 'kubectl') if self.runtime == 'k3s' else ('kubectl',)
        remote = shlex.join((*executable, *args))
        command = ['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes',
                   '-o', 'StrictHostKeyChecking=yes', '-o', 'GlobalKnownHostsFile=/dev/null',
                   '-o', 'UserKnownHostsFile=' + self.known_hosts,
                   '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
                   '-o', 'PermitLocalCommand=no', '-o', 'PasswordAuthentication=no',
                   '-o', 'ConnectTimeout=15', '-l', self.ssh_user, self.host, remote]
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=45, check=False, env=self.env)
        except (OSError, subprocess.TimeoutExpired):
            raise ClusterError('verified host operation failed') from None
        if result.returncode:
            raise ClusterError('verified host operation failed')
        return result.stdout.strip()

    def apply_manifest(self, manifest):
        if not isinstance(manifest, str) or not manifest.strip() or len(manifest) > 65536:
            raise ClusterError('invalid reviewed Flux manifest')
        command = ['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes',
                   '-o', 'StrictHostKeyChecking=yes', '-o', 'GlobalKnownHostsFile=/dev/null',
                   '-o', 'UserKnownHostsFile=' + self.known_hosts,
                   '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
                   '-o', 'PermitLocalCommand=no', '-o', 'PasswordAuthentication=no',
                   '-o', 'ConnectTimeout=15', '-l', self.ssh_user, self.host,
                   shlex.join((*(('env','-u','KUBECONFIG','k3s','kubectl') if self.runtime == 'k3s' else ('kubectl',)), 'apply', '-f', '-'))]
        try:
            result = subprocess.run(command, input=manifest, text=True, capture_output=True,
                                    timeout=45, check=False, env=self.env)
        except (OSError, subprocess.TimeoutExpired):
            raise ClusterError('verified Flux apply failed') from None
        if result.returncode:
            raise ClusterError('verified Flux apply failed')
        return result.stdout.strip()
