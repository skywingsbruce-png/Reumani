"""安全沙箱单元测试。运行：python -m pytest tests/ -q  或  python tests/test_sandbox.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ssc_sandbox import safe_run_python, ToolResult


def test_block_subprocess():
    r = safe_run_python("import subprocess; subprocess.run(['ls'])")
    assert not r.ok and r.error_type == "blocked"


def test_block_env_read():
    r = safe_run_python("print(open('.env').read())")
    assert not r.ok and r.error_type == "blocked"


def test_block_file_delete():
    r = safe_run_python("import os; os.remove('x')")
    assert not r.ok and r.error_type == "blocked"


def test_allow_pandas():
    r = safe_run_python("import pandas as pd; print(pd.DataFrame({'a':[1,2]}).sum().to_dict())")
    assert r.ok and "a" in r.data


def test_block_named_key():
    # 第一层：代码里字面提到密钥名，直接拦截
    r = safe_run_python("x = 'DEEPSEEK_API_KEY'")
    assert not r.ok and r.error_type == "blocked"


def test_api_key_scrubbed():
    # 第二层：即使不提密钥名，子进程环境里也读不到任何 token（已剥离）
    import os as _os
    _os.environ["SANDBOX_TEST_TOKEN"] = "leak-me"
    r = safe_run_python("import os\nprint('T=' + str(os.environ.get('SANDBOX_TEST_TOKEN')))")
    assert r.ok and "T=None" in r.data


def test_toolresult_failure_not_disguised():
    r = safe_run_python("raise ValueError('boom')")
    assert not r.ok and r.error_type == "runtime_error"
    assert "工具失败" in r.as_text()


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{passed}/{len(fns)} 通过")
