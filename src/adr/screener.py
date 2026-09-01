"""四维异动筛选 + 炸板标记 + 优先级排序 + M 截断（DESIGN / PRD P0-4 / P0-5）。

四维：量能（放量/极致缩量）、价格（|涨跌幅|>P_TH）、形态（突破/跌破/大阳/大阴/长影线）、
板块（强势 Top K 且个股涨幅≥1.5%）。
炸板：独立标记、不剔除、不计入维度。
优先级 = 触发维度数（≥3 高 / 2 中 / 1 低）；同级按 |pct|×量比 降序；截断至 M。
每只 Candidate.trace 记录每个标签触发时的原始数值（供溯源）。
"""

import pandas as pd

from src.adr.types import Candidate
from src.adr.universe import snapshot_from_row


def screen(candidates: pd.DataFrame, metrics: dict, sectors: dict, cfg):
    """返回 (kept: list[Candidate], truncate_log: list[dict])。

    板块维度：``sectors`` 为 ``{block_name: SectorStat}``（block_type==2）。
    构建 代码→板块 反查，取排名最优（rank 最小）的板块用于「板块强势」判定与展示。
    """
    kept: list = []
    truncate_log: list = []

    # 代码 → 所属板块列表（一只股票可属多个 block_type==2 板块）
    code_blocks: dict = {}
    for s in sectors.values():
        for c in s.members:
            code_blocks.setdefault(str(c).zfill(6), []).append(s)

    def _best_block(code):
        lst = code_blocks.get(str(code).zfill(6))
        if not lst:
            return None
        return min(lst, key=lambda s: s.rank if s.rank else 10 ** 9)

    for _, row in candidates.iterrows():
        code = str(row["code"])
        m = metrics.get(code)
        if m is None:
            continue

        snap = snapshot_from_row(row)
        tags: list = []
        trace: dict = {}

        # ① 量能
        if m.vr is not None:
            if m.vr > cfg.Q_UP and (m.vma5 and row["vol"] > m.vma5 * 1.2):
                tags.append("放量")
                trace["放量"] = f"量比{m.vr:.2f}>Q_UP{cfg.Q_UP} 且 量{row['vol']:.0f}手>5日均量×1.2({m.vma5*1.2:.0f}手)"
            elif m.vr < cfg.Q_LOW:
                tags.append("极致缩量")
                trace["极致缩量"] = f"量比{m.vr:.2f}<Q_LOW{cfg.Q_LOW}"

        # ② 价格
        pct = snap.pct
        if pct is not None:
            if pct > cfg.P_TH:
                tags.append("涨异动")
                trace["涨异动"] = f"涨{pct:.2f}%>P_TH{cfg.P_TH}"
            elif pct < -cfg.P_TH:
                tags.append("跌异动")
                trace["跌异动"] = f"跌{pct:.2f}%<-P_TH{cfg.P_TH}"

        # ③ 形态
        if m.break_up:
            tags.append("突破")
            trace["突破"] = "收盘站上MA(前收≤MA，前复权口径)"
        if m.break_down:
            tags.append("跌破")
            trace["跌破"] = "收盘跌破MA(前收≥MA，前复权口径)"
        if m.big_bull:
            tags.append("大阳")
            trace["大阳"] = "涨幅超分板阈值(主板5%/创科10%)"
        if m.big_bear:
            tags.append("大阴")
            trace["大阴"] = "跌幅超分板阈值(主板5%/创科10%)"
        if m.long_shadow:
            tags.append("长影线")
            trace["长影线"] = "上/下影线≥实体且≥振幅40%"

        # ④ 板块（block_type==2 真实板块；取排名最优者）
        sec = _best_block(code)
        if sec is not None and sec.block_rank <= cfg.K and pct is not None and pct >= 1.5:
            tags.append("板块强势")
            trace["板块强势"] = f"{sec.block_name} 排名{sec.block_rank}/{len(sectors)} 加权涨{sec.pct_weighted:.2f}%"

        # 炸板（情绪标签，独立、不计入维度）
        if bool(row["is_broken_board"]):
            tags.append("炸板")
            trace["炸板"] = "最高触及涨停价但收盘未封板（分歧信号）"

        # 维度计数（量能/价格/形态/板块）
        dim_volume = any(t in ("放量", "极致缩量") for t in tags)
        dim_price = any(t in ("涨异动", "跌异动") for t in tags)
        dim_form = any(t in ("突破", "跌破", "大阳", "大阴", "长影线") for t in tags)
        dim_sector = "板块强势" in tags
        dims = sum([dim_volume, dim_price, dim_form, dim_sector])

        # 命中任意异动维度 或 炸板 → 留存
        if dims == 0 and "炸板" not in tags:
            continue

        priority = "高" if dims >= 3 else ("中" if dims == 2 else "低")
        strength = (abs(pct) if pct is not None else 0.0) * (m.vr if m.vr else 1.0)

        cand = Candidate(
            code=code,
            name=snap.name,
            tags=tags,
            priority=priority,
            strength=strength,
            snap=snap,
            metrics=m,
            sector=sec,
            trace=trace,
        )
        kept.append(cand)

    # 排序：优先级↓ → 量价强度↓
    _order = {"高": 3, "中": 2, "低": 1}
    kept.sort(key=lambda c: (_order[c.priority], c.strength), reverse=True)

    # 截断至 M
    if len(kept) > cfg.M:
        for c in kept[cfg.M:]:
            truncate_log.append(
                {
                    "code": c.code,
                    "name": c.name,
                    "priority": c.priority,
                    "reason": f"优先级*{_order[c.priority]} 量价强度{c.strength:.2f} 超出 M={cfg.M} 被截断",
                    "strength": round(c.strength, 4),
                }
            )
        kept = kept[: cfg.M]
    return kept, truncate_log
