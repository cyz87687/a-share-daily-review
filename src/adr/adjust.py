"""自实现前复权（DESIGN 0.6）。

mootdx 原生 ``bars(adjust='qfq')`` 在 pandas 3.0.3 下崩溃（K4），故自实现。
对 ``category==1`` 的每笔除权除息记录（按时间升序累积）：
    div = fenhong / 10
    sg  = songzhuangu / 10
    pg  = peigu / 10;  pgj = peigujia
    theo = (P - div + pg * pgj) / (1 + sg + pg)     # P 为 T 日前一交易日不复权收盘
    f = theo / P
    → T 日之前所有 bar 的 open/high/low/close 乘 f（前复权口径）
返回新 DataFrame，不修改入参；缺失 xdxr 时前复权序列等于不复权序列。
"""

import pandas as pd


def build_qfq(bars: pd.DataFrame, xdxr: pd.DataFrame) -> pd.DataFrame:
    """产出含 ``*_qfq`` 与 ``adj_factor`` 的前复权序列。"""
    if bars is None or len(bars) == 0:
        return pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "vol", "amount",
                     "open_qfq", "high_qfq", "low_qfq", "close_qfq", "adj_factor"]
        )

    df = bars.copy()
    for c in ["open", "high", "low", "close"]:
        df[c + "_qfq"] = df[c].astype(float)
    df["adj_factor"] = 1.0

    if xdxr is None or len(xdxr) == 0:
        return df

    ex = xdxr[xdxr["category"] == 1].copy()
    if len(ex) == 0:
        return df

    ex["ex_date"] = pd.to_datetime(
        dict(year=ex["year"], month=ex["month"], day=ex["day"]), errors="coerce"
    )
    ex = ex.dropna(subset=["ex_date"]).sort_values("ex_date")
    if len(ex) == 0:
        return df

    ddates = pd.to_datetime(df["date"], errors="coerce")

    for _, e in ex.iterrows():
        t = e["ex_date"]
        prior = df[ddates < t]
        if len(prior) == 0:
            continue
        p = float(prior["close"].iloc[-1])  # 前复权前的不复权收盘（P 为原始 OHLC，未被修改）
        if p <= 0:
            continue
        div = float(e.get("fenhong") or 0) / 10.0
        sg = float(e.get("songzhuangu") or 0) / 10.0
        pg = float(e.get("peigu") or 0) / 10.0
        pgj = float(e.get("peigujia") or 0)
        denom = 1.0 + sg + pg
        if denom <= 0:
            continue
        theo = (p - div + pg * pgj) / denom
        if theo <= 0:
            continue
        f = theo / p
        mask = ddates < t
        if not bool(mask.any()):
            continue
        for c in ["open", "high", "low", "close"]:
            df.loc[mask, c + "_qfq"] = df.loc[mask, c + "_qfq"] * f
        df.loc[mask, "adj_factor"] = df.loc[mask, "adj_factor"] * f

    return df
