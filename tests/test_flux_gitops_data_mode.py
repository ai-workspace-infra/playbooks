"""/data contract for declarative_active: independent ext4 volume or allocated directory."""
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'roles/vhosts/flux_gitops/files/active_flux_bootstrap.py'


def load():
    spec = importlib.util.spec_from_file_location('afb', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_volume_mode_requires_matching_ext4_mount(monkeypatch):
    afb = load()
    monkeypatch.setattr(afb, 'run', lambda *a, **k: '/data abc ext4')
    assert afb.check_data('volume', 'abc') == 'abc'
    with pytest.raises(ValueError):
        afb.check_data('volume', 'other')


def test_directory_mode_records_backing_filesystem(monkeypatch, tmp_path):
    afb = load()
    d = tmp_path / 'data'
    d.mkdir(mode=0o755)
    if os.geteuid() != 0:

        class FakeStat:
            st_mode = 0o040755
            st_uid = 0

        monkeypatch.setattr(afb, 'Path', lambda p: type('P', (), {'lstat': lambda self: FakeStat()})())
    monkeypatch.setattr(afb, 'run', lambda *a, **k: 'root-uuid')
    assert afb.check_data('directory', '', str(d)) == 'root-uuid'


def test_directory_mode_rejects_writable_or_non_root(monkeypatch):
    afb = load()

    class Writable:
        st_mode = 0o040777
        st_uid = 0

    class NonRoot:
        st_mode = 0o040755
        st_uid = 1000

    for bad in (Writable, NonRoot):
        monkeypatch.setattr(afb, 'Path', lambda p, s=bad: type('P', (), {'lstat': lambda self: s()})())
        with pytest.raises(ValueError):
            afb.check_data('directory', '', '/data')


def test_handover_passes_mode_and_preflight_bounds_it():
    handover = (ROOT / 'roles/vhosts/flux_gitops/tasks/handover.yml').read_text()
    preflight = (ROOT / 'roles/vhosts/flux_gitops/tasks/preflight.yml').read_text()
    defaults = (ROOT / 'roles/vhosts/flux_gitops/defaults/main.yml').read_text()
    assert '--data-mode' in handover and 'k3s_platform_data_mode' in handover
    assert "k3s_platform_data_mode in ['volume', 'directory']" in preflight
    assert 'k3s_platform_data_mode: volume' in defaults
