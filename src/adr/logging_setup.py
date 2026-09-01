"""统一日志：``logs/run-{date}.log`` 落地 + 控制台输出。

记录数据载入耗时、候选池规模、各阶段剔除/留存计数、截断名录、AI 批次耗时与 token、
质量门 6 项结论（PRD P0-22）。
"""

import logging
import sys
from logging import Logger
from pathlib import Path


def setup_logging(logs_dir: str, date: str, name: str = "adr") -> Logger:
    """配置并返回 logger；同一进程重复调用会重置 handler 避免重复输出。"""
    log_path = Path(logs_dir) / f"run-{date}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger
