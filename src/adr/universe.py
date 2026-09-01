"""全市场快照构建 + 候选池 Top N + 五项硬剔除（DESIGN / PRD P0-2 / P0-3）。

流程（DESIGN 4.2 build_universe）：
  全市场快照 → exclude_bj/正则过滤 → 五项硬剔除
  （ST、次新<D、一字板、停牌、成交额<1亿且流通市值<50亿）
  → 按成交额降序取 Top N。
每条剔除记录 reason 到 exclude_log（幂等，按日可覆盖由上层决定）。
"""

from datetime import date, datetime

import pandas as pd

from src.adr.datasource.tdx import limit_pct_of
from src.adr.types import Snapshot
from src.adr.sector import load_sector_map


def _parse_ipo(ipo) -> date:
    """ipo_date 为 int YYYYMMDD（如 20010827）→ date；失败返回 None（零幻觉）。"""
    try:
        s = str(int(ipo))
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except Exception:
        return None


def build_universe(client, cfg, run_date: str):
    """返回 (candidates_df, exclude_log_df)。"""
    snap = client.get_full_snapshot(run_date)
    exclude_rows = []

    df = snap.copy()
    # 去除 mootdx 名称中的 NUL/控制字符填充（如「新强联\x00\x00」），再 strip
    df["name"] = (
        df["name"].fillna("").astype(str)
        .str.replace(r"[\x00-\x1f]", "", regex=True)
        .str.strip()
    )

    # 基础派生字段
    df["is_st"] = df["name"].str.contains("ST|退", regex=True, na=False)
    df["is_suspended"] = (df["price"] <= 0) | (df["amount"] <= 0) | (df["vol"] <= 0)
    df["is_yiziban"] = (
        (df["open"] == df["high"])
        & (df["high"] == df["low"])
        & (df["low"] == df["price"])
        & (df["price"] > 0)
    )
    df["limit_pct"] = df["code"].apply(limit_pct_of)

    def _limit_up_price(r):
        if r["last_close"] and r["last_close"] > 0 and r["limit_pct"]:
            return r["last_close"] * (1 + r["limit_pct"] / 100.0) * 0.9995
        return None

    df["limit_up_price"] = df.apply(_limit_up_price, axis=1)
    df["is_limit_up"] = (df["price"] >= df["limit_up_price"]) & (df["price"] > 0)
    df["is_broken_board"] = (
        (df["high"] >= df["limit_up_price"])
        & (df["price"] < df["limit_up_price"])
        & (df["price"] > 0)
    )
    # 涨跌幅（不复权口径，真实涨幅）
    df["pct"] = df.apply(
        lambda r: (r["price"] - r["last_close"]) / r["last_close"] * 100.0
        if (r["last_close"] and r["last_close"] > 0 and pd.notna(r["price"]))
        else None,
        axis=1,
    )
    df["amount_yi"] = df["amount"] / 1e8

    # ----- 廉价剔除（ST / 停牌 / 一字板）-----
    def _add_excl(sub, reason):
        for _, r in sub.iterrows():
            exclude_rows.append({"code": str(r["code"]), "name": str(r["name"]), "reason": reason})

    st_mask = df["is_st"]
    _add_excl(df[st_mask], "ST/*ST/退市")
    df = df[~st_mask]

    sus_mask = df["is_suspended"]
    _add_excl(df[sus_mask], "停牌/无成交")
    df = df[~sus_mask]

    yz_mask = df["is_yiziban"]
    _add_excl(df[yz_mask], "一字板")
    df = df[~yz_mask]

    # ----- 按成交额降序取候选池 -----
    df = df.sort_values("amount", ascending=False, na_position="last").reset_index(drop=True)

    # ----- 拉取 finance（次新/流通市值剔除需要），仅 Top N+buffer 避免全市场 5000+ 次调用 -----
    buf = cfg.N + 300
    top = df.head(min(buf, len(df))).copy()
    fin_records = []
    for code in top["code"]:
        f = client.finance(code) or {}
        fin_records.append(
            {
                "code": code,
                "industry": f.get("industry"),
                "ipo_date": f.get("ipo_date"),
                "liutongguben": f.get("liutongguben"),
                "zongguben": f.get("zongguben"),
            }
        )
    fin_df = pd.DataFrame(fin_records)
    top = top.merge(fin_df, on="code", how="left")

    # 派生（缺失 → None，不编造）
    top["float_mv"] = top.apply(
        lambda r: r["price"] * r["liutongguben"] if (pd.notna(r["liutongguben"]) and pd.notna(r["price"])) else None,
        axis=1,
    )
    top["turnover"] = top.apply(
        lambda r: r["vol"] * 100.0 / r["liutongguben"]
        if (pd.notna(r["liutongguben"]) and r["liutongguben"] > 0)
        else None,
        axis=1,
    )
    top["industry_code"] = top["industry"].apply(lambda v: str(int(v)) if pd.notna(v) else None)
    top["ipo_date_parsed"] = top["ipo_date"].apply(_parse_ipo)
    d_obj = datetime.strptime(run_date, "%Y-%m-%d").date()
    top["listed_days"] = top["ipo_date_parsed"].apply(lambda d: (d_obj - d).days if d else None)

    # 行业维度（industry 编码→行业名）由 build_sectors 通过 data/sector_map.csv 可插拔映射，
    # 此处仅保留 industry_code；industry_name 列由 build_sectors 就地写入（缺失 N/A），绝不编造。

    # ----- 次新剔除（上市 < D 交易日）-----
    new_mask = top["listed_days"].apply(lambda d: d is not None and d < cfg.D)
    _add_excl(top[new_mask], f"次新(上市<{cfg.D}交易日)")
    top = top[~new_mask]

    # ----- 成交额<1亿 且 流通市值<50亿 -----
    low_liq = (top["amount"] < 1e8) & (
        top["float_mv"].apply(lambda v: v is not None and v < 5e9)
    )
    _add_excl(top[low_liq], "成交额<1亿且流通市值<50亿")
    top = top[~low_liq]

    # ----- Top N -----
    candidates = top.head(cfg.N).reset_index(drop=True)
    exclude_log = (
        pd.DataFrame(exclude_rows, columns=["code", "name", "reason"])
        if exclude_rows
        else pd.DataFrame(columns=["code", "name", "reason"])
    )
    return candidates, exclude_log


def snapshot_from_row(row) -> Snapshot:
    """从 candidates DataFrame 的一行构建 Snapshot（缺失登记到 missing，渲染 N/A）。"""

    def _num(v):
        return float(v) if pd.notna(v) else None

    snap = Snapshot(
        code=str(row["code"]),
        name=str(row["name"]),
        price=_num(row["price"]),
        last_close=_num(row["last_close"]),
        open=_num(row["open"]),
        high=_num(row["high"]),
        low=_num(row["low"]),
        vol=_num(row["vol"]),
        amount=_num(row["amount"]),
        pct=_num(row["pct"]),
        amount_yi=_num(row["amount_yi"]),
        turnover=_num(row.get("turnover")),
        float_mv=_num(row.get("float_mv")),
        industry_code=(str(row["industry_code"]) if pd.notna(row.get("industry_code")) else None),
        industry_name=(str(row["industry_name"]) if pd.notna(row.get("industry_name")) else None),
        ipo_date=(row["ipo_date_parsed"] if pd.notna(row.get("ipo_date_parsed")) else None),
        listed_days=(int(row["listed_days"]) if pd.notna(row.get("listed_days")) else None),
        limit_pct=_num(row.get("limit_pct")),
        is_st=bool(row["is_st"]),
        is_suspended=bool(row["is_suspended"]),
        is_yiziban=bool(row["is_yiziban"]),
        is_limit_up=bool(row["is_limit_up"]),
        is_broken_board=bool(row["is_broken_board"]),
    )
    if snap.turnover is None and snap.float_mv is None:
        snap.na("float_mv", "finance() 未返回 liutongguben")
    if snap.industry_code is None:
        snap.na("industry_code", "finance() 未返回 industry 编码")
    if snap.ipo_date is None:
        snap.na("ipo_date", "finance() 未返回 ipo_date")
    return snap
