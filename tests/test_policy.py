import pathlib as pl
from marven_local.core.policy import Policy

def test_policy_default(tmp_path: pl.Path):
    p = Policy(tmp_path)
    assert p.check("fs.read", path=tmp_path)
    assert not p.check("net.http")


def test_policy_rejects_sibling_prefix_escape(tmp_path: pl.Path):
    root = tmp_path / "project"
    sibling = tmp_path / "project-private"
    root.mkdir()
    sibling.mkdir()
    policy = Policy(root)
    assert not policy.check("fs.read", path=sibling / "secret.txt")
