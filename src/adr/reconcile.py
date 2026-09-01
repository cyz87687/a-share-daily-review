"""昨日对账（DESIGN P0-12 / 三态判定：兑现/证伪/未触发）。

读上一交易日 holdings csv → 按代码匹配今日行情 → 自动判定三态。
首日（无昨日志）返回空，HTML 标注「首日运行，无对账数据」。
修正结论列在 prepare 模式留空待人工填，auto 模式由 AI 生成。
"""

from pathlib import Path

import pandas as pd

from src.adr import logs_repo


def _prev_trading_date(cfg, run_date: str):
    """在 data/logs 下查找早于 run_date 的最大 holdings 日期。"""
    d = Path(cfg.data_dir) / "logs"
    if not d.exists():
        return None
    best = None
    for f in d.glob("holdings-*.csv"):
        ds = f.stem.replace("holdings-", "")
        if ds < run_date:
            if best is None or ds > best:
                best = ds
    return best


def reconcile(cfg, run_date: str, today_snap: pd.DataFrame) -> list:
    """返回对账表 list[dict]；首日返回空。"""
    prev = _prev_trading_date(cfg, run_date)
    if prev is None:
        return []
    prev_hold = logs_repo.read_holdings(cfg, prev)
    if len(prev_hold) == 0:
        return []
    if today_snap is None or len(today_snap) == 0:
        return []

    idx = today_snap.copy()
    idx["code"] = idx["code"].astype(str)
    snap_idx = idx.set_index("code")

    rows = []
    for _, r in prev_hold.iterrows():
        code = str(r["代码"])
        if code not in snap_idx.index:
            continue
        t = snap_idx.loc[code]
        price = float(t["price"]) if pd.notna(t["price"]) else None
        last_close = float(t["last_close"]) if "last_close" in t and pd.notna(t["last_close"]) else None
        pct = (price - last_close) / last_close * 100.0 if (price is not None and last_close and last_close > 0) else None

        yesterday = str(r.get("触发标签", ""))
        if pct is not None:
            if pct > 0:
                result = "兑现"
            elif pct < 0:
                result = "证伪"
            else:
                result = "未触发"
        else:
            result = "未触发"

        rows.append(
            {
                "code": code,
                "name": str(r.get("名称", "")),
                "yesterday": yesterday,
                "result": result,
                "fix": "",
                "today_pct": round(pct, 2) if pct is not None else "N/A",
            }
        )
    return rows
