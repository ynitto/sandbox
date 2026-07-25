"""agent_project.resident — 常駐体（resident）本体（設計 §4.2、実装計画 P1）。

agent_project の他の断片（fragment-exec 合成の 30 ファイル）とは異なり、resident は
独立した通常パッケージとして書く（設計 §4.7 — transport / protocol / resident は最初から
通常モジュール）。単体 import・単体テストができ、fragment の共有名前空間に依存しない。
"""
from __future__ import annotations

from .scheduler import Scheduler, Tick, TickTimeout
from .supervisor import ChildSpec, Supervisor, graceful_shutdown
from .worker import NodeWorkerPool, WorkItem
from .status import (CONTRACT_VERSION, ChildStatus, EngineStatus, NodeCapability,
                     SyncHealth, contract_compatible)
from .gc import run_gc

__all__ = ["Scheduler", "Tick", "TickTimeout", "ChildSpec", "Supervisor", "graceful_shutdown",
          "NodeWorkerPool", "WorkItem", "CONTRACT_VERSION", "ChildStatus", "EngineStatus",
          "NodeCapability", "SyncHealth", "contract_compatible", "run_gc"]
