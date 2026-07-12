"""
阶段7：湿实验 Protocol IR（只生成结构化协议 + 静态校验，绝不控制真实设备）。
流程（第一版只走前 3 步）：
  生成实验设计 → 输出结构化 Protocol IR → 静态检查(体积/浓度/单位/对照/材料) → 人工审批
  →〔后期〕编译 Opentrons/PyLabRobot → 模拟 → 人工确认 → 设备执行 → 结果回传
⚠️ human_approval_required 恒为 True；本模块不连接、不驱动任何仪器。
"""

import json
import re

from ssc_pi_agent import judge_llm, deepseek_llm_pro

PROTOCOL_SCHEMA_HINT = """
输出严格 JSON（Protocol IR）：
{
 "protocol_id": "简短英文id",
 "title": "实验名",
 "objective": "目的",
 "materials": [{"name":"试剂/样本","amount":"量+单位","notes":""}],
 "labware": ["需要的耗材/设备"],
 "steps": [
   {"operation":"操作(transfer/incubate/centrifuge/mix/measure...)",
    "detail":"具体描述","volume_ul": 数值或null,"temperature_c": 数值或null,
    "duration_min": 数值或null}
 ],
 "controls": ["阳性/阴性/空白对照"],
 "acceptance_criteria": ["判定成功的标准"],
 "hazards": ["危险/生物安全提示"],
 "human_approval_required": true
}
只输出 JSON。
"""


def _parse_json(text):
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        text = m.group(0)
    return json.loads(text)


def generate_protocol(experiment_description, model="deepseek"):
    """根据实验想法生成结构化 Protocol IR。返回 dict。"""
    llm = judge_llm if model == "claude" else deepseek_llm_pro
    prompt = (
        "你是严谨的实验方案设计员。根据下面的实验想法，设计一个可执行的湿实验方案，"
        "输出结构化 Protocol IR。步骤要具体、带体积/温度/时间/单位，必须包含对照和验收标准，"
        "标注生物安全/危险。不要编造不合理的数值。\n\n"
        f"实验想法：{experiment_description}\n\n{PROTOCOL_SCHEMA_HINT}"
    )
    try:
        ir = _parse_json(llm.invoke(prompt).content)
        ir["human_approval_required"] = True   # 恒为真
        return ir
    except Exception as e:
        return {"error": f"生成失败：{e}"}


def validate_protocol(ir: dict):
    """静态检查：单位/体积/对照/材料/验收/危险。返回 (通过?, 问题列表)。不连设备。"""
    issues = []
    if not isinstance(ir, dict) or ir.get("error"):
        return False, [ir.get("error", "不是有效协议")]

    if not ir.get("materials"):
        issues.append("缺少材料清单(materials)")
    else:
        for m in ir["materials"]:
            amt = str(m.get("amount", ""))
            if amt and not re.search(r"\d", amt):
                issues.append(f"材料 '{m.get('name')}' 的量缺少数值/单位：'{amt}'")

    steps = ir.get("steps", [])
    if not steps:
        issues.append("缺少实验步骤(steps)")
    for i, s in enumerate(steps, 1):
        v = s.get("volume_ul")
        if v is not None:
            try:
                vf = float(v)
                if vf <= 0:
                    issues.append(f"步骤{i} 体积非正数：{v}")
                if vf > 100000:
                    issues.append(f"步骤{i} 体积异常大({v} µL)，请核对单位")
            except (TypeError, ValueError):
                issues.append(f"步骤{i} 体积不是数值：{v}")
        t = s.get("temperature_c")
        if t is not None:
            try:
                if not (-80 <= float(t) <= 100):
                    issues.append(f"步骤{i} 温度超出常规范围：{t}°C")
            except (TypeError, ValueError):
                issues.append(f"步骤{i} 温度不是数值：{t}")

    if not ir.get("controls"):
        issues.append("缺少对照(controls)——实验必须有对照")
    if not ir.get("acceptance_criteria"):
        issues.append("缺少验收标准(acceptance_criteria)")
    if not ir.get("hazards"):
        issues.append("缺少生物安全/危险提示(hazards)")

    passed = len(issues) == 0
    return passed, issues


if __name__ == "__main__":
    # 自检：只测校验逻辑（免费，不生成）
    good = {"materials": [{"name": "TGF-β1", "amount": "10 ng/mL"}],
            "steps": [{"operation": "transfer", "volume_ul": 100, "temperature_c": 37}],
            "controls": ["未处理对照"], "acceptance_criteria": ["α-SMA 上调"],
            "hazards": ["BSL-2"], "human_approval_required": True}
    bad = {"materials": [{"name": "试剂", "amount": "适量"}],
           "steps": [{"operation": "transfer", "volume_ul": -5}],
           "controls": [], "acceptance_criteria": [], "hazards": []}
    print("合格方案校验:", validate_protocol(good))
    print("问题方案校验:", validate_protocol(bad))
