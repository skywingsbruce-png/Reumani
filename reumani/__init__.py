"""
reumani 包骨架（兼容层）。目标目录结构见 MIGRATION.md。
第一轮【不做全量迁移】：现有顶层模块(schemas.py / retrieval.py / ssc_a1.py 等)仍是入口，照常可用；
本包只提供未来的分层命名空间与 re-export 兼容层，逐步迁移。
"""
__version__ = "0.1.0-compat"
