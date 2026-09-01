"""留存日志（holdings-{date}.csv）读写、次日回填、按日幂等覆盖（DESIGN 8.7）。

字段（对齐上游提示词留存日志）：代码|名称|触发标签|优先级|所属板块及涨幅排名|量比|收盘价|
次日开盘表现|次日收盘表现。次日两列由次日运行回填；按日整体覆盖，不追加。
"""

from pathlib import Path

import pandas as pd


def _path(cfg, run_date: str) -> Path:
    return Path(cfg.data_dir) / "logs" / f"holdings-{run_date}.csv"


def write_holdings(cfg, run_date: str, candidates: list) -> None:
    """按日覆盖写留存日志（幂等）。"""
    p = _path(cfg, run_date)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for c in candidates:
        sec = c.sector
        if sec and sec.pct_weighted is not None:
            sector_str = f"{sec.block_name} 排名{sec.block_rank}/{sec.member_count} 涨{sec.pct_weighted:.2f}%"
        else:
            sector_str = "N/A"
        rows.append(
            {
                "代码": c.code,
                "名称": c.name,
                "触发标签": "|".join(c.tags),
                "优先级": c.priority,
                "所属板块及涨幅排名": sector_str,
                "量比": round(c.metrics.vr, 2) if c.metrics.vr is not None else "N/A",
                "收盘价": round(c.snap.price, 2) if c.snap.price is not None else "N/A",
                "次日开盘表现": "",
                "次日收盘表现": "",
            }
        )
    cols = ["代码", "名称", "触发标签", "优先级", "所属板块及涨幅排名", "量比", "收盘价", "次日开盘表现", "次日收盘表现"]
    df = pd.DataFrame(rows, columns=cols)
    df.to_csv(p, index=False, encoding="utf-8-sig")


def read_holdings(cfg, run_date: str) -> pd.DataFrame:
    """读取留存日志；不存在返回空表。"""
    p = _path(cfg, run_date)
    cols = ["代码", "名称", "触发标签", "优先级", "所属板块及涨幅排名", "量比", "收盘价", "次日开盘表现", "次日收盘表现"]
    if not p.exists():
        return pd.DataFrame(columns=cols)
    return pd.read_csv(p, dtype={"代码": str}, encoding="utf-8-sig")


def backfill_next_day(cfg, prev_date: str, today_snap: pd.DataFrame) -> None:
    """将 prev_date 持仓代码的今日开盘/收盘表现回填进昨日 csv（闭环关键）。

    ``today_snap`` 为当日全市场快照 DataFrame（含 code/open/price）。
    """
    p = _path(cfg, prev_date)
    if not p.exists():
        return
    if today_snap is None or len(today_snap) == 0:
        return
    df = pd.read_csv(p, dtype={"代码": str}, encoding="utf-8-sig")
    idx = today_snap.copy()
    idx["code"] = idx["code"].astype(str)
    snap_idx = idx.set_index("code")[["open", "price"]]
    for i, row in df.iterrows():
        code = str(row["代码"])
        if code in snap_idx.index:
            r = snap_idx.loc[code]
            df.at[i, "次日开盘表现"] = round(float(r["open"]), 2) if pd.notna(r["open"]) else "N/A"
            df.at[i, "次日收盘表现"] = round(float(r["price"]), 2) if pd.notna(r["price"]) else "N/A"
    df.to_csv(p, index=False, encoding="utf-8-sig")
