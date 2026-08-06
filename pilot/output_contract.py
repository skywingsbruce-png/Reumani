"""A.8.1 —— 共享层 OutputContract：结构化输出的**唯一语义来源**。

两次真实付费 Canary 都在 Synthesizer 处被 max_tokens 截断（1500/1500、1600/1600）。
根因不是 max_tokens 太小，而是：**schema 只能判定「什么算合法」，没有任何东西把长度上限
告诉模型，也没有使用 provider 原生结构化输出**。

本模块建立四层约束中的第 1 层（唯一语义来源），并派生出第 2 层（Prompt 文本）与
provider JSON Schema。第 3 层（provider adapter）与第 4 层（本地 validator）分别在
`pilot/provider_output.py` 与 `pilot/research_results.py` 中消费本模块，**不得各自再写一份上限**。

本模块是疾病无关、Agent 无关的：未来 Literature / Omics / Wet-lab / Clinical 四个能力
Agent 复用同一套机制，只需各自定义自己的 OutputContract。
**禁止**在此写入任何 SSc / cGAS–STING / 具体课题内容。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from schemas import _Strict

OUTPUT_CONTRACT_SCHEMA = "output-contract-v1"

# provider 强制能力等级（从强到弱）。绝不能把弱的谎报成强的。
NativeConstraintMode = Literal[
    "native_json_schema",     # provider 按 JSON Schema 强制结构
    "json_object_only",       # provider 只保证「是合法 JSON」，不保证字段/长度
    "prompt_only",            # provider 无任何强制，只有 Prompt 说明
]


class FieldLimit(_Strict):
    """单个字段的边界。Prompt 文本与本地 validator **都**从这里派生。"""
    name: str
    type: Literal["string", "string_list", "enum", "bool", "int", "object_list"]
    required: bool = True
    max_characters: Optional[int] = None      # 对 string / 列表中每一项
    max_items: Optional[int] = None           # 对列表
    enum_values: list[str] = Field(default_factory=list)
    evidence_reference_only: bool = False     # 只能填 evidence_id，不得夹带正文
    free_text_allowed: bool = True
    nullable: bool = False
    description: str = ""

    def prompt_line(self) -> str:
        """给模型看的一行机器可检查说明。

        刻意使用**紧凑记法**：这段文字要随每次调用付费发送，冗长的散文措辞会挤占
        科学字段的预算。记法压缩不改变任何语义，也不删减任何字段。
        """
        opt = "" if self.required else "?"
        if self.enum_values:
            body = "enum[" + "|".join(sorted(self.enum_values)) + "]"
        elif self.type in ("string_list", "object_list"):
            body = f"<={self.max_items}x<={self.max_characters}ch"
        elif self.type == "string":
            body = f"str<={self.max_characters}ch"
        else:
            body = self.type
        out = f"{self.name}{opt}: {body}"
        if self.evidence_reference_only:
            out += " [evidence_id ONLY]"
        if self.description:
            out += f"  # {self.description}"
        return out

    def json_schema_fragment(self) -> dict:
        """派生 provider JSON Schema 片段。

        注意：`maxLength` / `maxItems` 是否被 provider **强制**取决于 provider 能力；
        本函数只负责如实表达约束，不代表任何强制保证（见 ProviderOutputCapability）。
        """
        if self.type == "string":
            s: dict = {"type": "string"}
            if self.max_characters:
                s["maxLength"] = self.max_characters
            if self.enum_values:
                s["enum"] = sorted(self.enum_values)
            return s
        if self.type == "enum":
            return {"type": "string", "enum": sorted(self.enum_values)}
        if self.type == "bool":
            return {"type": "boolean"}
        if self.type == "int":
            return {"type": "integer"}
        if self.type in ("string_list", "object_list"):
            item: dict = {"type": "string"}
            if self.max_characters:
                item["maxLength"] = self.max_characters
            s = {"type": "array", "items": item}
            if self.max_items is not None:
                s["maxItems"] = self.max_items
            return s
        raise ValueError(f"未知字段类型：{self.type}")


class OutputContract(_Strict):
    """某个角色的结构化输出契约。**唯一语义来源。**"""
    schema_version: Literal["output-contract-v1"] = OUTPUT_CONTRACT_SCHEMA
    contract_id: str                       # 例：synthesis-result-v2
    role: str                              # synthesizer / verifier / claim_extractor
    result_schema_version: str             # 落到结果对象里的 schema_version
    fields: list[FieldLimit] = Field(default_factory=list)
    max_output_tokens: int
    allow_additional_properties: bool = False
    require_complete_output: bool = True
    truncation_policy: Literal["fail_closed"] = "fail_closed"
    validation_policy: Literal["local_always"] = "local_always"
    artifact_policy: Literal["only_on_complete_valid_output"] = "only_on_complete_valid_output"

    # ---------- 派生：本地 validator 用 ----------
    def limits(self) -> dict:
        """本地校验用的上限表。research_results 从这里取，**不得另写常量**。"""
        return {f.name: {"max_characters": f.max_characters, "max_items": f.max_items,
                         "evidence_reference_only": f.evidence_reference_only,
                         "required": f.required}
                for f in self.fields}

    # ---------- 派生：provider JSON Schema ----------
    def json_schema(self) -> dict:
        props = {f.name: f.json_schema_fragment() for f in self.fields}
        props["schema_version"] = {"type": "string", "enum": [self.result_schema_version]}
        required = [f.name for f in self.fields if f.required] + ["schema_version"]
        return {"type": "object", "properties": props, "required": sorted(set(required)),
                "additionalProperties": bool(self.allow_additional_properties)}

    # ---------- 派生：Prompt 文本 ----------
    def prompt_block(self) -> str:
        """把上限**真正告诉模型**。这是 A.8.1 的核心：过去 Prompt 从未包含任何长度上限。"""
        lines = [
            f"OUTPUT CONTRACT {self.contract_id} — hard limits, violating any voids the answer:",
            f'schema_version="{self.result_schema_version}"',
        ]
        lines += [f.prompt_line() for f in self.fields]
        lines += [
            "Reply = ONE complete JSON object, nothing else. No extra fields. "
            "No restated abstracts or EvidenceCard copies. No reasoning or chain-of-thought.",
            f"Fill every required field first; if tight, SHORTEN wording — never stop mid-object. "
            f"Whole reply must fit in {self.max_output_tokens} output tokens.",
        ]
        return "\n".join(lines)

    def prompt_fingerprint(self) -> str:
        """Prompt 块与 limits 同源的证明（测试用）。"""
        from tool_envelope import compute_hash
        return compute_hash({"limits": self.limits(), "block": self.prompt_block()})


class ProviderOutputCapability(_Strict):
    """某个 provider+model+SDK 组合的**实测**结构化输出能力。

    只允许根据已安装 SDK / 官方文档核实后填写；**禁止**按方法名猜测，
    禁止把 json_object 谎报成完整 JSON Schema 保证。
    """
    provider: str
    model_id: str
    sdk_version: str
    json_object_supported: bool
    json_schema_supported: bool
    strict_tool_schema_supported: bool
    string_max_length_supported_by_provider: bool
    array_max_items_supported_by_provider: bool
    native_constraint_mode: NativeConstraintMode
    fallback_mode: NativeConstraintMode
    verified_at: str
    documentation_source: str

    def guarantees(self) -> dict:
        """provider 真正保证什么 vs 只能靠本地 validator。"""
        native = self.native_constraint_mode == "native_json_schema"
        return {
            "json_wellformed_by_provider": self.native_constraint_mode in
                ("native_json_schema", "json_object_only"),
            "field_structure_by_provider": native,
            "string_length_by_provider": bool(native and
                                              self.string_max_length_supported_by_provider),
            "array_items_by_provider": bool(native and
                                            self.array_max_items_supported_by_provider),
            "everything_else_local_only": True,
        }


__all__ = ["OutputContract", "FieldLimit", "ProviderOutputCapability",
           "OUTPUT_CONTRACT_SCHEMA", "NativeConstraintMode"]
