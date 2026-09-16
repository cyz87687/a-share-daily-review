#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用 TDX finance()（该接口当前仍可用）补齐 _raw_wind_*.json 的 universe.ipo_date。

背景：universe._parse_ipo 只接受 int YYYYMMDD（如 20010827），而旧 wind_meta.json 存
"2012-04-10" 字符串 → 解析恒为 None → 次新剔除(D 日)从未生效。此脚本统一改写为 int。

用法：
  python3 tools/enrich_ipo_date.py _raw_wind_2026-09-15.json _raw_wind_2026-09-16.json
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    from mootdx.quotes import Quotes

    raw_paths = [Path(p) for p in sys.argv[1:]]
    if not raw_paths:
        print("用法: enrich_ipo_date.py <raw1.json> <raw2.json> ...")
        return

    # 收集全部 universe 代码
    docs = {p: json.loads(p.read_text(encoding="utf-8")) for p in raw_paths}
    codes = sorted({str(u["code"]).zfill(6) for d in docs.values() for u in d["universe"]})

    q = Quotes.factory(market="std")
    ipo_map: dict = {}
    for i, code in enumerate(codes, 1):
        try:
            f = q.finance(code)
            ipo = None
            if f is not None and len(f) and "ipo_date" in f.columns:
                v = f.iloc[0]["ipo_date"]
                if v is not None:
                    ipo = int(v)
            ipo_map[code] = ipo
        except Exception as e:  # noqa: BLE001
            ipo_map[code] = None
            print(f"[warn] {code} finance 失败: {e}")
        if i % 20 == 0:
            print(f"... {i}/{len(codes)}")

    hit = sum(1 for v in ipo_map.values() if v)
    print(f"[ipo] 取到 {hit}/{len(codes)}")

    for p, d in docs.items():
        for u in d["universe"]:
            c = str(u["code"]).zfill(6)
            if ipo_map.get(c):
                u["ipo_date"] = ipo_map[c]
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[write] {p.name} 已更新 ipo_date(int)")

    # 同步 wind_meta.json（改为 int，避免后续再次踩坑）
    meta_path = ROOT / "wind_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for c, v in ipo_map.items():
            if v:
                meta.setdefault(c, {})["ipo_date"] = v
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[write] wind_meta.json 已同步为 int")


if __name__ == "__main__":
    main()
