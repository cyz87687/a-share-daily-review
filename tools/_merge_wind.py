#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""将一批 Wind get_stock_kline 原始响应合并进 _raw_wind.json。

用法: python tools/_merge_wind.py <batch_file.json>

batch_file.json 格式（由 lead 逐批从工具原始响应复制，零换算）:
[
  {"code": "300308", "resp": {"data": {"columns":[...], "rows":[[...]]}, "error": null}},
  ...
]

映射（按列名，鲁棒）:
  TIME -> date (取前10位)
  OPEN -> open
  MATCH -> close
  HIGH -> high
  LOW  -> low
  TURNOVER -> amount (元)
  VOLUME   -> vol (股)

universe 条目: 取 klines 末行 OHLCV 为 09-14 当日值，pre_close 取末行前一行 close；
name/ipo_date 来自 wind_meta.json（search_stocks 真实值）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def code6(c: str) -> str:
    return str(c).replace(".SZ", "").replace(".SH", "").replace(".BJ", "").zfill(6)


# get_stock_kline 固定列序（按列名，与 Wind 返回一致）
_HARDCODED_COLS = ["TIME", "OPEN", "MATCH", "HIGH", "LOW", "TURNOVER", "VOLUME", "CHANGEHANDRATE", "AVPRICE"]


def main() -> None:
    batch_path = Path(sys.argv[1])
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    meta = json.loads((ROOT / "wind_meta.json").read_text(encoding="utf-8"))

    outp = ROOT / "_raw_wind.json"
    if outp.exists():
        data = json.loads(outp.read_text(encoding="utf-8"))
    else:
        data = {"date": "2026-09-14", "universe": [], "klines": {}, "indices": []}

    have = {u["code"] for u in data["universe"]}

    added = 0
    for item in batch:
        code = code6(item["code"])
        # 兼容两种格式：
        #  - 完整响应：{"code":.., "resp": {"data": {"columns":[...], "rows":[[...]]}}}
        #  - 紧凑格式：{"code":.., "rows":[[TIME,OPEN,MATCH,HIGH,LOW,TURNOVER,VOLUME,...], ...]}
        if "resp" in item:
            d = item["resp"]["data"]
            cols = [c["name"] for c in d["columns"]]
        else:
            cols = _HARDCODED_COLS
        idx = {name: i for i, name in enumerate(cols)}
        ti, oi = idx["TIME"], idx["OPEN"]
        mi, hi = idx["MATCH"], idx["HIGH"]
        li, vi, ai = idx["LOW"], idx["VOLUME"], idx["TURNOVER"]
        rows_raw = item["resp"]["data"]["rows"] if "resp" in item else item["rows"]

        rows = []
        for r in rows_raw:
            rows.append({
                "date": str(r[ti])[:10],
                "open": float(r[oi]),
                "close": float(r[mi]),
                "high": float(r[hi]),
                "low": float(r[li]),
                "vol": float(r[vi]),
                "amount": float(r[ai]),
            })
        if not rows:
            continue
        data["klines"][code] = rows

        if code not in have:
            last = rows[-1]
            prev = rows[-2] if len(rows) >= 2 else last
            m = meta.get(code, {})
            data["universe"].append({
                "code": code,
                "name": m.get("name", ""),
                "ipo_date": m.get("ipo_date"),
                "open": last["open"],
                "close": last["close"],
                "high": last["high"],
                "low": last["low"],
                "vol": last["vol"],
                "amount": last["amount"],
                "pre_close": prev["close"],
            })
            have.add(code)
            added += 1

    outp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"[merge] 本批 +{added} 新代码; universe 累计 {len(data['universe'])}, klines 累计 {len(data['klines'])}")


if __name__ == "__main__":
    main()
