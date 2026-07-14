"""
上传文件的解析：把 PDF/CSV/Excel/文本抽成文字喂给模型；图片编码成 data URI 给视觉模型(Claude)。
让"上传数据 → 针对数据回答"成立。
"""

import base64
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
          ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}


def is_image(path):
    return Path(path).suffix.lower() in IMAGE_EXT


def extract_text(path, max_chars=6000):
    """从非图片文件抽取文本摘要（供放进上下文）。"""
    p = Path(path)
    ext = p.suffix.lower()
    try:
        if ext == ".pdf":
            import PyPDF2
            reader = PyPDF2.PdfReader(str(p))
            text = "\n".join((pg.extract_text() or "") for pg in reader.pages[:25])
        elif ext in (".csv", ".tsv"):
            import pandas as pd
            df = pd.read_csv(p, sep=None, engine="python", nrows=300)
            text = f"[表格 {p.name}] 形状≈{df.shape}，列：{list(df.columns)}\n" + df.head(25).to_string()
        elif ext in (".xlsx", ".xls"):
            import pandas as pd
            df = pd.read_excel(p, nrows=300)
            text = f"[表格 {p.name}] 形状≈{df.shape}，列：{list(df.columns)}\n" + df.head(25).to_string()
        elif ext in (".txt", ".md", ".json", ".py", ".tsv"):
            text = p.read_text(encoding="utf-8", errors="replace")
        else:
            text = f"[{p.name}] 暂不支持解析该类型，仅记录文件名。"
    except Exception as e:
        text = f"[解析 {p.name} 失败：{e}]"
    return text[:max_chars]


def encode_image(path):
    """图片 → data URI，供 Claude 视觉消息。"""
    p = Path(path)
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    media = _MEDIA.get(p.suffix.lower(), "image/png")
    return f"data:{media};base64,{b64}"
