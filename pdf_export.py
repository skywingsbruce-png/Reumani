"""
实验方案 PDF 导出（中文）。用 reportlab + 系统 simsun 字体，自动换行/分页。
build_plan_pdf(title, subtitle, body) -> bytes，可直接给 Streamlit download_button。
"""

import io
import re
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

_FONT = "SSCChinese"
_REGISTERED = False


def _ensure_font():
    global _REGISTERED
    if _REGISTERED:
        return _FONT
    candidates = [
        r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",           # Linux (Noto CJK)
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",                     # Linux (文泉驿)
        "/System/Library/Fonts/PingFang.ttc",                              # macOS
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                pdfmetrics.registerFont(TTFont(_FONT, path, subfontIndex=0))
                _REGISTERED = True
                return _FONT
            except Exception:
                continue
    return "Helvetica"  # 兜底（中文可能乱码，但不崩）


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_plan_pdf(title, subtitle, body):
    """把一段文本方案渲染成 PDF 字节。标题/①②③小标题加粗放大，- 列表缩进。"""
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    font = _ensure_font()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=16 * mm,
                            leftMargin=18 * mm, rightMargin=16 * mm, title=title)
    h1 = ParagraphStyle("h1", fontName=font, fontSize=16, leading=22, spaceAfter=6)
    sub = ParagraphStyle("sub", fontName=font, fontSize=9, leading=13, textColor="#666666", spaceAfter=10)
    h2 = ParagraphStyle("h2", fontName=font, fontSize=12, leading=18, spaceBefore=8, spaceAfter=3, textColor="#0b5")
    body_s = ParagraphStyle("body", fontName=font, fontSize=10, leading=15)
    bullet = ParagraphStyle("bullet", fontName=font, fontSize=10, leading=15, leftIndent=10)

    flow = [Paragraph(_esc(title), h1)]
    if subtitle:
        flow.append(Paragraph(_esc(subtitle), sub))
    for raw in (body or "").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            flow.append(Spacer(1, 4))
            continue
        stripped = line.strip()
        if stripped.startswith("======") or re.match(r"^[①-⑩]", stripped):
            flow.append(Paragraph(_esc(stripped.strip("= ")), h2))
        elif stripped.startswith("- "):
            flow.append(Paragraph("• " + _esc(stripped[2:]), bullet))
        else:
            flow.append(Paragraph(_esc(stripped), body_s))
    flow.append(Spacer(1, 10))
    flow.append(Paragraph(_esc("⚠️ 科研决策支持，非临床/操作规程；对照、伦理与最终判断由研究者负责。"),
                          ParagraphStyle("foot", fontName=font, fontSize=8, leading=11, textColor="#999999")))
    doc.build(flow)
    return buf.getvalue()


if __name__ == "__main__":
    pdf = build_plan_pdf("实验方案（测试）", "SSc · 全血 · 流式",
                         "====== 实验副驾建议 ======\n① 样本路径\n- Ficoll 分 PBMC\n② 对照\n- FMO 定门\n正文测试中文渲染。")
    Path("_test_plan.pdf").write_bytes(pdf)
    print("OK", len(pdf), "bytes")
