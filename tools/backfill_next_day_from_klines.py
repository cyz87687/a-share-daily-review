#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从本地 Wind klines 补齐 holdings-{date}.csv 的「次日开盘/收盘表现」。

适用场景：cache-only 重建时 snapshot 仅覆盖 Top100，导致前一日留存池中不在次日
快照内的个股无法被 pipeline 的 backfill_next_day 回填。此脚本用已落盘的 Wind 日线
（真实数据）按 code+日期精确补齐，绝不编造。

用法：
  python3 tools/backfill_next_day_from_klines.py \
      --holdings data/logs/holdings-2026-09-14.csv \
      --next-date 2026-09-15 \
      --kline-dirs data/wind_raw/klines_gap data/wind_raw/klines
"""
import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def _load_bar(kline_dirs, code: str, date: str):
    for d in kline_dirs:
        p = Path(d) / f"kline_{code}.json"
        if not p.exists():
            continue
        try:
            resp = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        block = resp.get("data", {}) or {}
        cols = [c.get("name") for c in block.get("columns", [])]
        if not cols:
            continue
        idx = {n: i for i, n in enumerate(cols)}
        for row in block.get("rows", []):
            try:
                if str(row[idx["TIME"]])[:10] == date:
                    return float(row[idx["OPEN"]]), float(row[idx["MATCH"]])
            except (KeyError, TypeError, ValueError, IndexError):
                continue
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdings", required=True)
    ap.add_argument("--next-date", required=True)
    ap.add_argument("--kline-dirs", nargs="+", required=True)
    args = ap.parse_args()

    p = Path(args.holdings)
    df = pd.read_csv(p, dtype={"代码": str}, encoding="utf-8-sig", keep_default_na=False)
    for c in ("次日开盘表现", "次日收盘表现"):
        df[c] = df[c].astype(object)

    filled = 0
    still = []
    for i, row in df.iterrows():
        code = str(row["代码"]).zfill(6)
        if str(row["次日开盘表现"]) not in ("", "nan") and str(row["次日收盘表现"]) not in ("", "nan"):
            continue
        bar = _load_bar(args.kline_dirs, code, args.next_date)
        if bar is None:
            still.append(code)
            continue
        o, c = bar
        df.at[i, "次日开盘表现"] = round(o, 2) if o is not None else ""
        df.at[i, "次日收盘表现"] = round(c, 2) if c is not None else ""
        filled += 1

    df.to_csv(p, index=False, encoding="utf-8-sig")
    print(f"[backfill] {p.name} next={args.next_date} 本次补齐={filled} 仍缺={len(still)} {still}")


if __name__ == "__main__":
    main()
