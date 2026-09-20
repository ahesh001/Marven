import pathlib as pl
from marven_local.core.policy import Policy

def test_policy_default(tmp_path: pl.Path):
    p = Policy(tmp_path)
    assert p.check("fs.read", path=tmp_path)
    assert not p.check("net.http")
