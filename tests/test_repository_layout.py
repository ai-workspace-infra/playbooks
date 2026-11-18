from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]

# roles/ 下仅允许这四个交付形态命名空间
ALLOWED_ROLE_NAMESPACES = {"docker", "k8s_helm", "saas", "vhosts"}

# 根目录允许的非 playbook 文档/杂项（.md 类文档必须进 docs/）
ROOT_ALLOWED_MD = {"README.md", "AGENTS.md", "LICENSE"}


class RepositoryLayoutTest(unittest.TestCase):
    def test_roles_only_contain_delivery_namespaces(self):
        """roles/ 下不允许出现 vhosts/docker/k8s_helm/saas 之外的 role 目录。

        符号链接（如 roles/vhost -> vhosts）属于兼容别名，不算未分层 role。
        """
        offenders = []
        for child in (ROOT / "roles").iterdir():
            if child.is_symlink():
                continue
            if child.is_dir() and child.name not in ALLOWED_ROLE_NAMESPACES:
                offenders.append(child.name)
        self.assertEqual(
            [], sorted(offenders),
            f"roles/ 下存在未分层的 role 目录，请归入 {sorted(ALLOWED_ROLE_NAMESPACES)}: {sorted(offenders)}",
        )

    def test_root_has_no_stray_docs(self):
        """根目录只允许 README.md/AGENTS.md/LICENSE；其它 .md 归 docs/。"""
        offenders = []
        for child in ROOT.iterdir():
            if child.is_file() and child.suffix.lower() == ".md":
                if child.name not in ROOT_ALLOWED_MD:
                    offenders.append(child.name)
        self.assertEqual([], sorted(offenders), f"根目录存在游离文档，请移入 docs/: {sorted(offenders)}")

    def test_roles_path_keeps_archive_role_roots_resolvable(self):
        """ansible.cfg 的 roles_path 必须包含归档 role 根，保证归档 playbook 裸 role 名可解析。"""
        cfg = (ROOT / "ansible.cfg").read_text(encoding="utf-8")
        for line in cfg.splitlines():
            if line.startswith("roles_path"):
                value = line.split("=", 1)[1].strip()
                self.assertIn("./roles", value, "roles_path 必须包含 ./roles")
                self.assertIn("./archive/roles/web-saas", value, "roles_path 必须包含归档 web-saas role 根")
                self.assertIn("./archive/roles/selfhost", value, "roles_path 必须包含归档 selfhost role 根")
                self.assertIn("./archive/roles/legacy", value, "roles_path 必须包含归档 legacy role 根")
                return
        self.fail("ansible.cfg 缺少 roles_path")


if __name__ == "__main__":
    unittest.main()
