"""候选数据包装配（DESIGN / PRD P0-7）。

为每只留存标的导出全部原始字段：20 日 OHLCV、量比、5/10/20 日均量、涨跌幅、换手率、
流通市值、行业编码及板块涨幅排名、涨停/跌停价、是否炸板、昨日留存历史判断。
缺失字段一律 ``None``（数值）或 ``"N/A"``（字符串）+ 记录 ``missing_reason``，禁止推算。
"""

import json

import pandas as pd


def _r(v, nd: int = 4):
    """数值四舍五入；None 保持 None（渲染 N/A）。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return round(float(v), nd)
    except Exception:
        return None


def _json_default(o):
    if pd.isna(o):
        return None
    return str(o)


def build_datapack(kept: list, client, cfg, run_date: str, prev_map: dict = None) -> dict:
    """装配候选数据包（dict）。``prev_map``: {code: yesterday_tags}。"""
    prev_map = prev_map or {}
    stocks = []
    for c in kept:
        snap = c.snap
        m = c.metrics
        sec = c.sector

        bars = client.daily(c.code, offset=120)
        bars20 = bars.tail(20) if len(bars) else bars
        bars_20_list = []
        for _, br in bars20.iterrows():
            bars_20_list.append(
                {
                    "date": br["date"],
                    "open": _r(br["open"]),
                    "high": _r(br["high"]),
                    "low": _r(br["low"]),
                    "close": _r(br["close"]),
                    "vol": _r(br["vol"]),
                    "amount": _r(br["amount"]),
                }
            )

        limit_pct = snap.limit_pct
        if snap.last_close and limit_pct:
            limit_up_price = snap.last_close * (1 + limit_pct / 100.0)
            limit_down_price = snap.last_close * (1 - limit_pct / 100.0)
        else:
            limit_up_price = None
            limit_down_price = None

        stock = {
            "code": c.code,
            "name": c.name,
            "tags": list(c.tags),
            "priority": c.priority,
            "price": _r(snap.price),
            "last_close": _r(snap.last_close),
            "pct": _r(snap.pct),
            "amount_yi": _r(snap.amount_yi, 2),
            "turnover": _r(snap.turnover, 2),
            "float_mv": _r(snap.float_mv, 2),
            "vr": _r(m.vr, 2),
            "vma5": _r(m.vma5, 2),
            "vma10": _r(m.vma10, 2),
            "vma20": _r(m.vma20, 2),
            "ma5": _r(m.ma5, 2),
            "ma10": _r(m.ma10, 2),
            "ma20": _r(m.ma20, 2),
            "amplitude": _r(m.amplitude, 2),
            "upper_shadow": _r(m.upper_shadow, 2),
            "lower_shadow": _r(m.lower_shadow, 2),
            "break_up": m.break_up,
            "break_down": m.break_down,
            "big_bull": m.big_bull,
            "big_bear": m.big_bear,
            "long_shadow": m.long_shadow,
            "is_ex_dividend": m.is_ex_dividend,
            "industry_code": snap.industry_code,
            "industry_name": snap.industry_name,
            "block_name": sec.block_name if sec else None,
            "sector_rank": sec.block_rank if sec else None,
            "sector_pct_weighted": _r(sec.pct_weighted, 2) if sec else None,
            "sector_member_count": sec.member_count if sec else None,
            "limit_up_price": _r(limit_up_price, 2),
            "limit_down_price": _r(limit_down_price, 2),
            "is_broken_board": snap.is_broken_board,
            "yesterday_history": prev_map.get(c.code, "N/A"),
            "missing": snap.missing,
            "bars_20": bars_20_list,
        }
        stocks.append(stock)

    datapack = {
        "date": run_date,
        "params": {
            "N": cfg.N,
            "D": cfg.D,
            "Q_UP": cfg.Q_UP,
            "Q_LOW": cfg.Q_LOW,
            "P_TH": cfg.P_TH,
            "K": cfg.K,
            "M": cfg.M,
        },
        "schema_note": "缺失字段为 None（数值）或 'N/A'（字符串），禁止推算/编造（DESIGN 8.4）",
        "stocks": stocks,
    }
    return datapack
