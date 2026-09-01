#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""A股每日自动复盘工具 · CLI 入口。

子命令（DESIGN 5.1 / PRD P0-20）：
  review run [--date YYYY-MM-DD] [--mode prepare|auto]   执行当日复盘管道
  review finalize --date YYYY-MM-DD --review <json>      人工终审回填并重新渲染
  review index                                           重建历史归档索引页

退出码（DESIGN 8.8）：
  1 数据可用性断言失败（不写任何输出）
  2 质量门未通过（仍发布，标红）
  0 正常完成 / 单批 AI 失败降级
"""

import argparse
import sys
from pathlib import Path

# 确保项目根在 sys.path（src / assets 包可导入）
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adr.config import Config
from src.adr.pipeline import Pipeline


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    p = argparse.ArgumentParser(prog="review", description="A股每日自动复盘工具（adr）")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="执行当日复盘管道")
    run.add_argument("--date", default=None, help="交易日 YYYY-MM-DD（缺省取基准指数最新交易日）")
    run.add_argument("--mode", default=None, choices=["prepare", "auto"], help="覆盖 config.yaml 的 mode")

    fin = sub.add_parser("finalize", help="人工终审回填并重新渲染当日 HTML")
    fin.add_argument("--date", required=True, help="交易日 YYYY-MM-DD")
    fin.add_argument("--review", required=True, help="终审结果 JSON 路径（导出后回填）")

    sub.add_parser("index", help="重建历史归档索引页 output/index.html")
    return p


def main(argv: list = None) -> int:
    """CLI 主函数，返回进程退出码。"""
    args = build_parser().parse_args(argv)
    cfg = Config.load(ROOT / "config.yaml")
    pipe = Pipeline(cfg)

    if args.cmd == "run":
        date = args.date or pipe.infer_date()
        mode = args.mode or cfg.mode
        if mode not in ("prepare", "auto"):
            print(f"未知 mode: {mode}", file=sys.stderr)
            return 2
        return pipe.run(date, mode)
    if args.cmd == "finalize":
        return pipe.finalize(args.date, args.review)
    if args.cmd == "index":
        return pipe.rebuild_index()
    return 2


if __name__ == "__main__":
    sys.exit(main())
