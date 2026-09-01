"""题材主线数据（真实题材榜，来自腾讯自选股/Wind；mootdx 无题材板块名）。

mootdx ``block_type==2`` 仅含 20 个宽基/指数板块（含 ST板块），成分股名为干净名但
不含任何题材/概念板块名（如 AI短剧/半导体/PCB）。要得到「当日核心主线」这种题材级结论，
必须依赖外部题材概念榜。本模块读取由助手经 westock/wind 核验后落盘的
``data/thematic/{date}.json``，零幻觉：仅消费已落盘真实数据，绝不联网编造。

缺失（文件不存在/无 main_line）→ 返回 None，上层退化为 block_type==2 口径并标注。
"""

from pathlib import Path

import json


def thematic_path(cfg, date: str) -> Path:
    """当日题材主线数据文件路径。"""
    return Path(cfg.data_dir) / "thematic" / f"{date}.json"


def load_thematic(cfg, date: str) -> dict | None:
    """读取当日题材主线数据（已落盘真实数据）。缺失返回 None。

    返回 dict 含：date / source / main_line / main_line_code / main_line_change_pct /
    main_line_main_net_inflow_yi / main_line_up / main_line_leader / top_thematic。
    """
    p = thematic_path(cfg, date)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not d or not d.get("main_line"):
        return None
    return d
