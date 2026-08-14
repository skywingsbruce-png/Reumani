"""哨兵模块：**故意**在 import 期产生文件系统副作用。

用途只有一个 —— 证明 `test_import_performs_no_filesystem_network_or_model_io`
使用的探针确实抓得到副作用。否则那些"全 0"的断言可能只是探针失灵。

它读自己的源文件、并在**临时目录**下建一个目录（不污染仓库）。
不碰网络、不碰 key、不碰模型、不 import ssc_pi_agent。
"""

import tempfile
from pathlib import Path

# 故意的 import 期读取：读本文件自己，路径落在仓库内，探针应当记到 read。
_SELF_TEXT_LEN = len(Path(__file__).read_text(encoding="utf-8"))

# 故意的 import 期 mkdir：建在系统临时目录下，探针应当记到 mkdir。
_CANARY_DIR = Path(tempfile.gettempdir()) / "reumani_fs_probe_canary"
_CANARY_DIR.mkdir(exist_ok=True)

__all__ = ["_SELF_TEXT_LEN", "_CANARY_DIR"]
