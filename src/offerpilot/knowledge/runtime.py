"""Knowledge Worker 生命周期运行时。

该模块只重导出 ``worker.py`` 中的运行时类型，给应用装配提供稳定的模块边界，避免
FastAPI 生命周期直接依赖 Worker 实现细节。
"""

from offerpilot.knowledge.worker import KnowledgeWorkerRuntime

__all__ = ["KnowledgeWorkerRuntime"]
