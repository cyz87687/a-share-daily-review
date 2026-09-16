#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""通用版 Wind → _raw_wind_{date}.json 构建器（cache-only 流水线前置契约）。

用法：
  python3 tools/build_raw_wind_date.py \
      --date 2026-09-16 \
      --search data/wind_raw/search_stocks_0916.json \
      --kline-dir data/wind_raw/klines \
      --indices data/wind_raw/indices_0916.json \
      --out _raw_wind_2026-09-16.json

输入契约（全部为 Wind MCP 原始响应，禁止人工编造）：
  search   : Wind search_stocks 原始响应
             {"data":{"data":[{"columns":[{"name":...}],"rows":[[...]]}]}}
             列按【名称】匹配（兼容任意列序、任意日期前缀）。
  kline-dir: 目录下 kline_<6位代码>.json，各为 Wind get_stock_kline 原始响应
             {"data":{"columns":[{"name":"TIME"/"OPEN"/"MATCH"/"HIGH"/"LOW"/"TURNOVER"/"VOLUME"}],"rows":[...]}}
             可用 --asof 截断至 <= 该日（避免跨日复用时引入未来数据）。
  indices  : [{"code":"999999","data":{...get_index_kline 原始响应...}}, ...]

输出：{date, universe[], klines{}, indices[]}，amount 单位统一为「元」。
pre_close = close/(1+pct/100)（不复权口径）。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _find(cols: list, *keywords) -> int:
    """按列名关键字定位列索引（全部关键字命中）。"""
    for i, c in enumerate(cols):
        name = str(c.get("name") if isinstance(c, dict) else c)
        if all(k in name for k in keywords):
            return i
    raise KeyError(f"列未找到: {keywords}; 现有列={cols}")


def _code_of(wcode: str) -> str:
    return str(wcode).split(".")[0].zfill(6)


def build_universe(search_path: Path, meta: dict) -> list:
    raw = json.loads(search_path.read_text(encoding="utf-8"))
    block = raw["data"]["data"][0]
    cols = block["columns"]
    i_code = _find(cols, "Wind代码")
    i_name = _find(cols, "简称")
    i_open = _find(cols, "开盘价")
    i_close = _find(cols, "收盘价")
    i_high = _find(cols, "最高价")
    i_low = _find(cols, "最低价")
    i_vol = _find(cols, "成交量")
    i_amt = _find(cols, "成交额")
    i_pct = _find(cols, "涨跌幅")

    uni = []
    for r in block["rows"]:
        if r is None or all(v is None for v in r):
            continue
        code = _code_of(r[i_code])
        close = float(r[i_close])
        pct = float(r[i_pct]) if r[i_pct] is not None else 0.0
        pre_close = close / (1.0 + pct / 100.0) if (1.0 + pct / 100.0) else close
        uni.append({
            "code": code,
            "name": str(r[i_name]),
            "ipo_date": (meta.get(code) or {}).get("ipo_date"),
            "open": float(r[i_open]),
            "close": close,
            "high": float(r[i_high]),
            "low": float(r[i_low]),
            "vol": float(r[i_vol]),
            "amount": float(r[i_amt]) * 1e8,   # 亿元 → 元
            "pre_close": round(pre_close, 4),
        })
    return uni


def _bars_from_block(block: dict, asof: str) -> list:
    cols = block.get("columns", [])
    if not cols:
        return []
    i_t = _find(cols, "TIME")
    i_o = _find(cols, "OPEN")
    i_c = _find(cols, "MATCH")
    i_h = _find(cols, "HIGH")
    i_l = _find(cols, "LOW")
    i_to = _find(cols, "TURNOVER")
    i_v = _find(cols, "VOLUME")
    bars = []
    for row in block.get("rows", []):
        try:
            d = str(row[i_t])[:10]
            if asof and d > asof:
                continue
            bars.append({
                "date": d,
                "open": float(row[i_o]),
                "close": float(row[i_c]),
                "high": float(row[i_h]),
                "low": float(row[i_l]),
                "vol": float(row[i_v]),       # 股
                "amount": float(row[i_to]),   # 元
            })
        except (TypeError, ValueError, IndexError):
            continue
    return bars


def build_klines(kline_dir: Path, asof: str) -> dict:
    klines: dict = {}
    if not kline_dir.exists():
        return klines
    for p in sorted(kline_dir.glob("kline_*.json")):
        code = p.stem.replace("kline_", "").zfill(6)
        try:
            resp = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[warn] 跳过损坏 {p.name}: {e}")
            continue
        bars = _bars_from_block(resp.get("data", {}) or {}, asof)
        if bars:
            klines[code] = bars
    return klines


def build_indices(indices_path: Path, asof: str) -> list:
    items = json.loads(indices_path.read_text(encoding="utf-8"))
    out = []
    for it in items:
        code = str(it["code"]).zfill(6)
        bars = _bars_from_block(it.get("data", {}) or {}, asof)
        if not bars:
            print(f"[warn] 指数 {code} 无 K 线，跳过")
            continue
        last = bars[-1]
        prev_close = bars[-2]["close"] if len(bars) >= 2 else None
        price = float(last["close"])
        pct = (price - prev_close) / prev_close * 100.0 if prev_close else None
        out.append({
            "code": code,
            "price": price,
            "last_close": prev_close,
            "pct": round(pct, 4) if pct is not None else None,
            "amount": float(last["amount"]),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--search", required=True)
    ap.add_argument("--kline-dir", required=True)
    ap.add_argument("--indices", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--asof", default=None, help="K线截断日（默认=--date）")
    args = ap.parse_args()

    asof = args.asof or args.date
    meta_path = ROOT / "wind_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    uni = build_universe(Path(args.search), meta)
    klines = build_klines(Path(args.kline_dir), asof)
    indices = build_indices(Path(args.indices), asof)

    out = {"date": args.date, "universe": uni, "klines": klines, "indices": indices}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    miss = [u["code"] for u in uni if u["code"] not in klines]
    print(f"[build] date={args.date} asof={asof} universe={len(uni)} klines={len(klines)} indices={len(indices)}")
    print(f"[build] 无K线代码({len(miss)}): {miss[:20]}")


if __name__ == "__main__":
    main()
