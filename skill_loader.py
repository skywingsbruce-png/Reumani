"""
技能加载器：扫描 nature-skills/skills/ 和本地 skills/ 下的所有 SKILL.md，
解析出 name + description，供通用 agent「先看有哪些技能，再读某个技能的完整手册」。
这就是把 agent 从「写死流程」变成「读技能干活」的关键一环。
"""

import re
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

BASE = Path(__file__).resolve().parent
# 技能来源：nature-skills 仓库 + 你自己的本地 skills 目录
SKILL_DIRS = [
    BASE / "nature-skills" / "skills",
    BASE / "skills",
]


def _parse_frontmatter(text: str) -> dict:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}
    block = m.group(1)
    if yaml:
        try:
            data = yaml.safe_load(block)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    out = {}
    nm = re.search(r"^name:\s*(.+)$", block, re.MULTILINE)
    if nm:
        out["name"] = nm.group(1).strip()
    dm = re.search(r"^description:\s*(.+)$", block, re.MULTILINE)
    if dm:
        out["description"] = dm.group(1).strip()
    return out


def discover_skills() -> dict:
    """返回 {name: {name, folder, path, description}}。"""
    skills = {}
    for d in SKILL_DIRS:
        if not d.exists():
            continue
        for sk in sorted(d.iterdir()):
            if not sk.is_dir():
                continue
            md = sk / "SKILL.md"
            if not md.exists():
                continue
            text = md.read_text(encoding="utf-8", errors="replace")
            fm = _parse_frontmatter(text)
            name = str(fm.get("name") or sk.name).strip()
            desc = str(fm.get("description") or "").strip()
            desc = re.sub(r"\s+", " ", desc)
            skills[name] = {
                "name": name,
                "folder": sk.name,
                "path": str(md),
                "description": desc,
            }
    return skills


def list_skills_text(max_desc: int = 280) -> str:
    skills = discover_skills()
    if not skills:
        return "（未发现任何技能，请确认 nature-skills 已克隆到项目目录下。）"
    lines = []
    for s in skills.values():
        d = s["description"]
        if len(d) > max_desc:
            d = d[:max_desc] + "…"
        lines.append(f"- [{s['name']}]（文件夹 {s['folder']}）: {d}")
    return "\n".join(lines)


def read_skill_md(name: str) -> str:
    skills = discover_skills()
    target = skills.get(name)
    if not target:
        for s in skills.values():
            if s["folder"] == name or name.lower() in s["name"].lower() or name.lower() in s["folder"].lower():
                target = s
                break
    if not target:
        return f"未找到技能「{name}」。可用技能：{', '.join(skills.keys())}"

    text = Path(target["path"]).read_text(encoding="utf-8", errors="replace")
    # 附上该技能目录下的文件清单，方便 agent 进一步 read_file / run_python 调用其脚本
    folder = Path(target["path"]).parent
    files = []
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            files.append(str(p.relative_to(folder)))
        if len(files) >= 80:
            break
    files_block = "\n".join(files) if files else "（无附带文件）"
    return (
        text
        + f"\n\n---\n【本技能目录：{folder}】\n"
        + "【目录下的文件（可用 read_file 读取、或在 run_python 里 import 调用）】：\n"
        + files_block
    )


if __name__ == "__main__":
    # 直接运行本文件可自检：打印发现的技能清单（不消耗任何 API）
    print(list_skills_text())
