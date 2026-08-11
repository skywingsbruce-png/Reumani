"""A.8.2b.1 §3 —— legacy 的 **key / dotenv 边界**（零副作用配置契约）。

现状问题：`ssc_pi_agent.py` 在 import 期就 `load_dotenv()` 并读 `DEEPSEEK_API_KEY`。
只要有任何模块 `from ssc_pi_agent import <任意名字>`，.env 就被读走。库模块不该这样。

本模块的规则：
- **import 本模块不读 .env、不读 os.environ、不碰文件系统**；
- 读取只发生在两个**显式函数**里：`from_environment()` 与
  `load_local_dotenv_then_environment()`；
- 后者只允许**应用入口**（CLI/Streamlit 入口）调用，库模块一律不得调用；
- `repr` / `model_dump()` 默认脱敏，绝不吐出 key；
- 占位 key（如 `"not-configured"`）**不算已配置** —— 不允许冒充。

本轮**不修改** `ssc_pi_agent` 现有的配置读取，只建立未来的迁移目标。
"""

from __future__ import annotations

from typing import Optional

from pydantic import ConfigDict, Field

from schemas import _Strict


class LegacyRuntimeConfigError(RuntimeError):
    """配置缺失/非法 → fail-closed，绝不用占位 key 继续。"""


# 明确列出**不算已配置**的占位值。ssc_pi_agent 现在正是用 "not-configured" 兜底，
# 所以它必须被识别为"未配置"，否则我们会把缺 key 误报成已配置。
_PLACEHOLDERS = frozenset({
    "", "not-configured", "not_configured", "none", "null", "todo",
    "changeme", "change-me", "xxx", "your-api-key", "sk-xxx",
})

SOURCE_UNSET = "unset"
SOURCE_ENVIRONMENT = "environment"
SOURCE_DOTENV_THEN_ENVIRONMENT = "dotenv_then_environment"
SOURCE_EXPLICIT = "explicit"

# 默认模型一律钉死版本。绝不把浮动别名当默认值——那正是 ssc_pi_agent 现在的问题
# （DEEPSEEK_MODEL 默认解析成一个会在你不知情时更换的别名）。
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"


def _is_configured(value: Optional[str]) -> bool:
    return bool(value) and str(value).strip().lower() not in _PLACEHOLDERS


def _mask(value: Optional[str]) -> str:
    """脱敏展示：只保留长度与末 4 位之外的形状信息，绝不回显 key 本体。"""
    if not value:
        return "<unset>"
    s = str(value)
    if not _is_configured(s):
        return "<placeholder>"
    return f"<set:{len(s)}chars>"


class LegacyRuntimeConfig(_Strict):
    """legacy 付费调用需要的运行期配置。默认全空 —— 构造本身不读任何环境。"""
    model_config = ConfigDict(frozen=True, extra="forbid")

    deepseek_api_key: Optional[str] = Field(default=None, repr=False)
    anthropic_api_key: Optional[str] = Field(default=None, repr=False)
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL
    source: str = SOURCE_UNSET

    # ---------- 脱敏 ----------
    def __repr__(self) -> str:                      # noqa: D105
        return (f"LegacyRuntimeConfig(source={self.source!r}, "
                f"deepseek_api_key={_mask(self.deepseek_api_key)}, "
                f"anthropic_api_key={_mask(self.anthropic_api_key)}, "
                f"deepseek_model={self.deepseek_model!r}, "
                f"anthropic_model={self.anthropic_model!r})")

    __str__ = __repr__

    def model_dump(self, *a, **kw) -> dict:         # type: ignore[override]
        """默认脱敏。要拿真 key 只能走 `secret_for(provider)`，那是**显式**动作。"""
        return {"source": self.source,
                "deepseek_api_key": _mask(self.deepseek_api_key),
                "anthropic_api_key": _mask(self.anthropic_api_key),
                "deepseek_model": self.deepseek_model,
                "anthropic_model": self.anthropic_model}

    # ---------- 查询 ----------
    def is_configured(self, provider: str) -> bool:
        if provider == "deepseek":
            return _is_configured(self.deepseek_api_key)
        if provider == "anthropic":
            return _is_configured(self.anthropic_api_key)
        raise LegacyRuntimeConfigError(f"未知 provider：{provider!r}")

    def secret_for(self, provider: str) -> str:
        """取真实 key。缺失或占位一律抛错 —— **不返回占位 key**。"""
        value = (self.deepseek_api_key if provider == "deepseek"
                 else self.anthropic_api_key if provider == "anthropic" else None)
        if provider not in ("deepseek", "anthropic"):
            raise LegacyRuntimeConfigError(f"未知 provider：{provider!r}")
        if not _is_configured(value):
            raise LegacyRuntimeConfigError(
                f"{provider} 的 API key 未配置（占位值不算已配置）→ 拒绝构造客户端")
        return str(value)

    def model_for(self, provider: str) -> str:
        if provider == "deepseek":
            return self.deepseek_model
        if provider == "anthropic":
            return self.anthropic_model
        raise LegacyRuntimeConfigError(f"未知 provider：{provider!r}")


def empty_config() -> LegacyRuntimeConfig:
    """完全不读环境的空配置。用于 import-safety 与离线测试。"""
    return LegacyRuntimeConfig()


def from_environment(env: Optional[dict] = None) -> LegacyRuntimeConfig:
    """**显式**从环境变量读取。不读 .env、不写任何全局状态。

    `env` 可注入一个假环境用于测试；不传才读真实 `os.environ`。
    """
    import os

    e = os.environ if env is None else env
    return LegacyRuntimeConfig(
        deepseek_api_key=e.get("DEEPSEEK_API_KEY"),
        anthropic_api_key=e.get("ANTHROPIC_API_KEY"),
        deepseek_model=e.get("DEEPSEEK_MODEL") or DEFAULT_DEEPSEEK_MODEL,
        anthropic_model=e.get("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL,
        source=SOURCE_ENVIRONMENT)


def load_local_dotenv_then_environment(dotenv_path=None) -> LegacyRuntimeConfig:
    """**仅应用入口可调用**：先把本地 .env 载入进程环境，再读环境。

    库模块不得调用它 —— 这正是 `ssc_pi_agent.py:23` 现在做错的事。
    `python-dotenv` 缺失时不静默降级，直接抛。
    """
    try:
        from dotenv import load_dotenv
    except ImportError as e:                        # pragma: no cover - 依赖缺失路径
        raise LegacyRuntimeConfigError(
            "python-dotenv 未安装，无法载入本地 .env（不静默降级）") from e
    load_dotenv(dotenv_path) if dotenv_path else load_dotenv()
    cfg = from_environment()
    return cfg.model_copy(update={"source": SOURCE_DOTENV_THEN_ENVIRONMENT})


def explicit_config(*, deepseek_api_key=None, anthropic_api_key=None,
                    deepseek_model=DEFAULT_DEEPSEEK_MODEL,
                    anthropic_model=DEFAULT_ANTHROPIC_MODEL) -> LegacyRuntimeConfig:
    """完全由调用方注入（测试与依赖注入用）。不碰环境。"""
    return LegacyRuntimeConfig(
        deepseek_api_key=deepseek_api_key, anthropic_api_key=anthropic_api_key,
        deepseek_model=deepseek_model, anthropic_model=anthropic_model,
        source=SOURCE_EXPLICIT)


__all__ = ["LegacyRuntimeConfig", "LegacyRuntimeConfigError", "empty_config",
           "from_environment", "load_local_dotenv_then_environment", "explicit_config",
           "SOURCE_UNSET", "SOURCE_ENVIRONMENT", "SOURCE_DOTENV_THEN_ENVIRONMENT",
           "SOURCE_EXPLICIT", "DEFAULT_DEEPSEEK_MODEL", "DEFAULT_ANTHROPIC_MODEL"]
