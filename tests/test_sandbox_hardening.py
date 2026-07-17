"""十三 沙箱加固测试：一任务一独立目录、密钥脱敏、read_file 只读允许目录。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ssc_sandbox as SB


@pytest.mark.unit
def test_each_run_isolated_dir():
    r1 = SB.safe_run_python("print(1)")
    r2 = SB.safe_run_python("print(2)")
    id1 = r1.provenance.parameters.get("run_id")
    id2 = r2.provenance.parameters.get("run_id")
    assert id1 and id2 and id1 != id2                     # 独立 run_id → 独立目录，不会互相覆盖


@pytest.mark.unit
def test_no_shared_agent_run_script():
    # 新代码不再用固定脚本名 _agent_run.py（防并发覆盖）——清掉历史遗留后，运行不应重建它
    (SB.SANDBOX_DIR / "_agent_run.py").unlink(missing_ok=True)
    r = SB.safe_run_python("print('x')")
    assert not (SB.SANDBOX_DIR / "_agent_run.py").exists()
    # 脚本写在本任务独立目录里
    assert (SB.RUNS_DIR / r.provenance.parameters["run_id"] / "run.py").exists()


@pytest.mark.unit
def test_secret_redacted_in_output():
    r = SB.safe_run_python("print('key is sk-ABCD1234EFGH5678IJKL')")
    assert "REDACTED" in (r.data or "") and "sk-ABCD1234" not in (r.data or "")


@pytest.mark.unit
def test_readfile_rejects_env_and_absolute_outside():
    from ssc_skill_agent import read_file
    out_env = read_file.invoke({"path": ".env"})
    assert "拒绝" in out_env
    out_abs = read_file.invoke({"path": "/etc/passwd"})
    assert "拒绝" in out_abs
    out_trav = read_file.invoke({"path": "../../secret.txt"})
    assert "拒绝" in out_trav


@pytest.mark.unit
def test_readfile_allows_workspace():
    from ssc_skill_agent import read_file, WORKSPACE
    f = WORKSPACE / "hardening_probe.txt"
    f.write_text("hello-allowed", encoding="utf-8")
    try:
        assert "hello-allowed" in read_file.invoke({"path": "hardening_probe.txt"})
    finally:
        f.unlink(missing_ok=True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
