"""pytest 根配置：把仓库根加入 sys.path，保证 `import retrieval` 等在任何 cwd 下都能找到。"""
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
