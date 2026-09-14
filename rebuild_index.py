#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重建历史归档索引页 output/index.html（轻量版，仅依赖 jinja2 + assets.palette）。

用途：本沙箱运行 `review.py index` 会因完整 import 链（pandas/mootdx）OOM 被杀，
故提供此最小脚本，只渲染 index.html.j2，不触碰重依赖。

用法：
    python3 rebuild_index.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from jinja2 import Environment, FileSystemLoader  # noqa: E402
from assets.palette import PALETTE  # noqa: E402


def main() -> int:
    env = Environment(loader=FileSystemLoader(str(ROOT / "templates")), autoescape=False)
    tpl = env.get_template("index.html.j2")
    css_vars = ":root{" + "".join(f"--{k.lower()}:{v};" for k, v in PALETTE.items()) + "}"

    metas = []
    base = ROOT / "output"
    if base.exists():
        for d in sorted(base.iterdir()):
            mp = d / "run_meta.json"
            if d.is_dir() and mp.exists():
                try:
                    metas.append(json.loads(mp.read_text(encoding="utf-8")))
                except Exception:
                    pass
    metas.sort(key=lambda m: m["date"], reverse=True)

    html = tpl.render(metas=metas, css_vars=css_vars)
    (base / "index.html").write_text(html, encoding="utf-8")
    print(f"[索引] 已重建 output/index.html（{len(metas)} 期）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
