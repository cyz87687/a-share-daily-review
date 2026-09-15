#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Wind 预热缓存写入器（cache-only 流水线前置）。

读取 _raw_wind.json（由 Wind MCP 抓取的真实交易日数据），写入
data/cache/{date}/ 下与 TdxClient 完全兼容的 pickle，使
`review.py run --date {date} --cache-only` 可离线跑出
holdings-{date}.csv + 复盘⑧ 明日候选观察清单（enrich/screener 逻辑不变）。

_raw_wind.json 契约（amount 一律为「元」，非亿元）：
{
  "date": "2026-09-14",
  "universe": [
    {"code":"300308","name":"中际旭创","ipo_date":"2012-04-10",
     "open":890.0,"close":873.0,"high":895.0,"low":866.66,
     "vol":27183039,"amount":23936800302,"pre_close":895.xx}
  ],
  "klines": {
    "300308": [
      {"date":"2026-08-18","open":..,"close":..,"high":..,"low":..,"vol":..,"amount":..},
      ...
      {"date":"2026-09-14", ...}   # 含当日行
    ]
  },
  "indices": [
    {"code":"999999","price":..,"last_close":..,"pct":..,"amount":..},
    {"code":"399001","price":..,"last_close":..,"pct":..,"amount":..}
  ]
}

说明：snapshot 的 last_close 取该 code klines 中当日行的前一行 close；
若 klines 不含当日行则回退 universe.pre_close。板块维度(pdxr/blocks)留空 → N/A。
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adr.config import Config
from src.adr.datasource.cache import Cache

_XDXR_COLS = ["year", "month", "day", "category", "fenhong", "peigujia", "songzhuangu", "peigu"]


def market_of(code: str) -> int:
    s = str(code)
    return 1 if s.startswith(("60", "68", "90")) else 0


def main() -> None:
    raw_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_raw_wind.json"
    data = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    date = str(data["date"])
    cfg = Config.load(ROOT / "config.yaml")
    cache = Cache(cfg)

    klines = {str(k).zfill(6): v for k, v in data.get("klines", {}).items()}
    uni_by_code = {str(u["code"]).zfill(6): u for u in data.get("universe", [])}

    # 1) snapshot.pkl（全市场候选快照，供 build_universe TopN + 炸板判定）
    snap_rows = []
    for u in data.get("universe", []):
        code = str(u["code"]).zfill(6)
        kl = klines.get(code, [])
        idx_list = [i for i, r in enumerate(kl) if r.get("date") == date]
        row = kl[idx_list[0]] if idx_list else u
        prev_close = u.get("pre_close")
        if idx_list and idx_list[0] > 0:
            prev_close = float(kl[idx_list[0] - 1]["close"])
        snap_rows.append({
            "market": market_of(code),
            "code": code,
            "price": float(row.get("close", row.get("price"))),
            "last_close": float(prev_close) if prev_close is not None else None,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "vol": float(row["vol"]),
            "amount": float(row["amount"]),
            "servertime": f"{date} 15:00:00",
            "name": str(u.get("name", "")),
            "pre_close": float(prev_close) if prev_close is not None else None,
        })
    snap_df = pd.DataFrame(
        snap_rows,
        columns=["market", "code", "price", "last_close", "open", "high", "low", "vol", "amount", "servertime", "name", "pre_close"],
    )
    cache.save(snap_df, "snapshot", date)
    print(f"[warm] snapshot.pkl {len(snap_df)} 行")

    # 2) bars_{code}.pkl + finance_{code}.pkl + xdxr_{code}.pkl
    for code, kl in klines.items():
        code = str(code).zfill(6)
        bars = pd.DataFrame(
            [{
                "date": r["date"],
                "open": float(r["open"]),
                "close": float(r["close"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "vol": float(r["vol"]),
                "amount": float(r["amount"]),
            } for r in kl],
            columns=["date", "open", "close", "high", "low", "vol", "amount"],
        )
        cache.save(bars, "bars", date, code)

        u = uni_by_code.get(code, {})
        fin = pd.DataFrame([{
            "industry": None,
            "ipo_date": u.get("ipo_date"),
            "liutongguben": None,
            "zongguben": None,
            "market": market_of(code),
            "code": code,
        }], columns=["industry", "ipo_date", "liutongguben", "zongguben", "market", "code"])
        cache.save(fin, "finance", date, code)
        cache.save(pd.DataFrame(columns=_XDXR_COLS), "xdxr", date, code)
    print(f"[warm] bars/finance/xdxr {len(klines)} 只")

    # 2b) 为全部 universe 代码补齐 finance（ipo_date/name），保证 build_universe 次新剔除与名称解析可用
    for u in data.get("universe", []):
        code = str(u["code"]).zfill(6)
        if code in klines:
            continue
        fin = pd.DataFrame([{
            "industry": None,
            "ipo_date": u.get("ipo_date"),
            "liutongguben": None,
            "zongguben": None,
            "market": market_of(code),
            "code": code,
        }], columns=["industry", "ipo_date", "liutongguben", "zongguben", "market", "code"])
        cache.save(fin, "finance", date, code)

    # 3) blocks.pkl（空：Wind 无 mootdx block_type==2 等价物，板块维度 N/A）
    cache.save(pd.DataFrame(columns=["block_name", "block_code", "code"]), "blocks", date)
    print("[warm] blocks.pkl 空（板块维度 N/A）")

    # 4) index.pkl
    idx_df = pd.DataFrame(
        [{
            "code": str(x["code"]).zfill(6),
            "price": float(x["price"]),
            "last_close": float(x["last_close"]) if x.get("last_close") is not None else None,
            "pct": float(x["pct"]) if x.get("pct") is not None else None,
            "amount": float(x["amount"]) if x.get("amount") is not None else None,
        } for x in data.get("indices", [])],
        columns=["code", "price", "last_close", "pct", "amount"],
    )
    cache.save(idx_df, "index", date)
    print(f"[warm] index.pkl {len(idx_df)} 行")


if __name__ == "__main__":
    main()
