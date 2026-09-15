#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""由 Wind 抓取的真实数据构建 _raw_wind.json（cache-only 流水线前置契约）。

输入：
  data/wind_raw/search_stocks_0914.json  —— Wind search_stocks 返回的 09-14 全市场快照
                                            （100 名，列：Wind代码/证券简称/开/收/高/低/成交量(股)/成交额(亿元)/涨跌幅(%)/币种）
  wind_meta.json                          —— {code: {name, ipo_date}}
  data/wind_raw/kline_<code>.json         —— （可选）Wind get_stock_kline 原始响应，存在则并入 klines
  indices（脚本内硬编码，由 get_index_kline 两次调用解析）

输出：
  _raw_wind.json  —— {date, universe[{code,name,ipo_date,open,close,high,low,vol,amount(元),pre_close}],
                       klines{code:[{date,open,close,high,low,vol,amount}]},
                       indices[{code,price,last_close,pct,amount}]}

amount 统一为「元」；pre_close 由 close/(1+pct/100) 反推（不复权口径，与 TDX last_close 一致）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RAW_DIR = ROOT / "data" / "wind_raw"
DATE = "2026-09-14"

# 指数：由 09-14 get_index_kline(000001.SH / 399001.SZ) 解析
#   上证 999999: close 3885.33 / last_close(09-11) 3888.11 / amount 779281246400 元
#   深证 399001: close 13384.57 / last_close(09-11) 13471.26 / amount 849894305500 元
_INDICES = [
    {"code": "999999", "price": 3885.33, "last_close": 3888.11, "amount": 779281246400.0},
    {"code": "399001", "price": 13384.57, "last_close": 13471.26, "amount": 849894305500.0},
]


def _code_of(wcode: str) -> str:
    return wcode.split(".")[0].zfill(6)


def build_universe() -> list:
    raw = json.loads((RAW_DIR / "search_stocks_0914.json").read_text(encoding="utf-8"))
    meta = json.loads((ROOT / "wind_meta.json").read_text(encoding="utf-8"))
    block = raw["data"]["data"][0]
    rows = block["rows"]
    uni = []
    for r in rows:
        wcode, name = r[0], r[1]
        code = _code_of(wcode)
        open_, close, high, low = float(r[2]), float(r[3]), float(r[4]), float(r[5])
        vol = float(r[6])          # 股
        amount_yi = float(r[7])    # 亿元
        amount = amount_yi * 1e8   # 元
        pct = float(r[8])
        pre_close = close / (1.0 + pct / 100.0)
        uni.append({
            "code": code,
            "name": name,
            "ipo_date": meta.get(code, {}).get("ipo_date"),
            "open": open_, "close": close, "high": high, "low": low,
            "vol": vol, "amount": amount, "pre_close": round(pre_close, 4),
        })
    return uni


def build_klines() -> dict:
    klines: dict = {}
    for p in sorted(RAW_DIR.glob("kline_*.json")):
        code = p.stem.replace("kline_", "")
        try:
            resp = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[warn] 跳过损坏的 kline 文件 {p.name}: {e}")
            continue
        block = resp.get("data", {})
        cols = [c["name"] for c in block.get("columns", [])]
        idx = {name: i for i, name in enumerate(cols)}
        t_i, o_i, c_i, h_i, l_i = idx["TIME"], idx["OPEN"], idx["MATCH"], idx["HIGH"], idx["LOW"]
        to_i, v_i = idx["TURNOVER"], idx["VOLUME"]
        bars = []
        for row in block.get("rows", []):
            try:
                bar = {
                    "date": str(row[t_i])[:10],
                    "open": float(row[o_i]),
                    "close": float(row[c_i]),
                    "high": float(row[h_i]),
                    "low": float(row[l_i]),
                    "vol": float(row[v_i]),      # 股
                    "amount": float(row[to_i]),  # 元
                }
            except (TypeError, ValueError):
                # 跳过非交易日 / 空值行（如停牌日 Wind 返回 null）
                continue
            bars.append(bar)
        if bars:
            klines[code] = bars
    return klines


def main() -> None:
    uni = build_universe()
    klines = build_klines()
    indices = []
    for it in _INDICES:
        pct = (it["price"] - it["last_close"]) / it["last_close"] * 100.0
        indices.append({
            "code": it["code"], "price": it["price"],
            "last_close": it["last_close"], "pct": round(pct, 4), "amount": it["amount"],
        })
    out = {"date": DATE, "universe": uni, "klines": klines, "indices": indices}
    (ROOT / "_raw_wind.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[build] universe={len(uni)} klines_codes={len(klines)} indices={len(indices)}")
    if klines:
        print(f"[build] klines 覆盖: {sorted(klines)[:10]} ... 共 {len(klines)} 只")


if __name__ == "__main__":
    main()
