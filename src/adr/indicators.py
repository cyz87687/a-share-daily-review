"""派生指标（DESIGN 0.6 / Q3 口径分工）。

- 涨跌幅：不复权口径（``snap.pct``，真实涨幅），用于价格异动标签。
- 均线/形态/关键价位：前复权序列（``bars_qfq``），避免除权造成的均线断裂与假突破。
- 量比：当日量 ÷ 5日均量（日线近似，每处标注口径），``vr = vol / vma5``。
- 振幅 / 上下影线：当日原始 OHLC vs 昨收。

缺失一律 ``None``（渲染 N/A），禁止推算、禁止静默顶替。
"""

from src.adr.types import Snapshot, StockMetrics


def _limit_thr(limit_pct):
    """大阳/大阴阈值：创科/创业板(20%板)→10%，主板/中小板(10%板)→5%。"""
    if limit_pct == 20.0:
        return 10.0
    return 5.0


def enrich(bars_qfq: object, bars_raw: object, cfg, snap: Snapshot) -> StockMetrics:
    """计算 MA5/10/20（前复权）、5日均量、量比、振幅、上下影线、突破/跌破、
    大阳大阴、长影线、除权日标记（is_ex_dividend 由上层依据 xdxr 设置）。"""
    m = StockMetrics(code=snap.code)
    n = len(bars_qfq)
    if n == 0:
        return m

    qc = bars_qfq["close_qfq"]

    # MA5/10/20（前复权收盘）
    for k, attr in [(5, "ma5"), (10, "ma10"), (20, "ma20")]:
        if n >= k:
            setattr(m, attr, float(qc.tail(k).mean()))

    # 5/10/20 日均量（原始 vol，含今日）+ 量比
    for k, attr in [(5, "vma5"), (10, "vma10"), (20, "vma20")]:
        if n >= k:
            setattr(m, attr, float(bars_raw["vol"].tail(k).mean()))
    today_vol = float(bars_raw["vol"].iloc[-1]) if n else None
    if m.vma5 and m.vma5 > 0 and today_vol is not None:
        m.vr = today_vol / m.vma5

    # 今日原始 OHLC（用于振幅/影线，不复权口径）
    o = float(bars_raw["open"].iloc[-1])
    h = float(bars_raw["high"].iloc[-1])
    l = float(bars_raw["low"].iloc[-1])
    c = float(bars_raw["close"].iloc[-1])
    last_close = snap.last_close if (snap.last_close and snap.last_close > 0) else None
    if last_close:
        m.amplitude = (h - l) / last_close * 100.0
        body = abs(c - o)
        upper = (h - max(o, c)) / last_close * 100.0
        lower = (min(o, c) - l) / last_close * 100.0
        m.upper_shadow = upper
        m.lower_shadow = lower
        if m.amplitude and m.amplitude > 0:
            m.long_shadow = ((upper >= body) or (lower >= body)) and (
                max(upper, lower) >= 0.4 * m.amplitude
            )

    # 突破/跌破（收盘确认，前复权序列；需 n>=k+1 才有前一日 MA）
    if n >= 2:
        for k in (5, 10, 20):
            if n >= k + 1:
                ma_series = qc.rolling(k).mean()
                cur = ma_series.iloc[-1]
                prev = ma_series.iloc[-2]
                cc = qc.iloc[-1]
                pc = qc.iloc[-2]
                if cur is not None and prev is not None:
                    if cc > cur and pc <= prev:
                        m.break_up = True
                    if cc < cur and pc >= prev:
                        m.break_down = True

    # 大阳/大阴（按 limit_pct 分板）
    pct = snap.pct
    if pct is not None and snap.limit_pct:
        thr = _limit_thr(snap.limit_pct)
        if pct > thr:
            m.big_bull = True
        elif pct < -thr:
            m.big_bear = True

    return m
