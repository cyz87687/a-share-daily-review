"""管道编排者（DESIGN 5.1 / 唯一编排者）。

prepare / auto / finalize 三条流程 + rebuild_index。层间以 dataclass / dict 传递，无反向依赖。
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from assets.palette import PALETTE
from src.adr.config import Config
from src.adr.datasource.tdx import DataUnavailableError, TdxClient, limit_pct_of
from src.adr.logging_setup import setup_logging
from src.adr.adjust import build_qfq
from src.adr.indicators import enrich
from src.adr.universe import build_universe, snapshot_from_row
from src.adr.sector import build_sectors, load_sector_map, is_concept_block
from src.adr.thematic import load_thematic
from src.adr.screener import screen
from src.adr import logs_repo
from src.adr.reconcile import _prev_trading_date, reconcile
from src.adr.datapack import build_datapack
from src.adr.prompt import _inject_params, build_prompt
from src.adr import quality
from src.adr.local_review import board_of, _is_limit_up, _fmt
from src.adr.parser import parse_ai_json
from src.adr.renderer.review_page import make_sparkline, render_review
from src.adr.renderer.index_page import render_index


def _css_vars(palette: dict) -> str:
    return ":root{" + "".join(f"--{k.lower()}:{v};" for k, v in palette.items()) + "}"


def _idx_dict(row) -> dict:
    if row is None or len(row) == 0:
        return None
    return {"code": str(row["code"]), "name": "", "price": row.get("price"), "pct": row.get("pct")}


def _prev_holdings_map(cfg, run_date: str) -> dict:
    prev = _prev_trading_date(cfg, run_date)
    if not prev:
        return {}
    df = logs_repo.read_holdings(cfg, prev)
    return {str(r["代码"]): str(r.get("触发标签", "")) for _, r in df.iterrows()}


    def _is_first_day(cfg, run_date: str) -> bool:
        return _prev_trading_date(cfg, run_date) is None

def _dedupe_kept(kept: list) -> list:
    """按代码去重（保留首次出现），消除候选池重复标的（如同一代码命中多维度）。"""
    seen, out = set(), []
    for c in kept:
        if c.code in seen:
            continue
        seen.add(c.code)
        out.append(c)
    return out


class Pipeline:
    """编排者。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = TdxClient(cfg)
        self.logger = None

    # ----------------------------------------------------------- 日期推断
    def infer_date(self) -> str:
        if self.client._q is None:
            self.client.connect()
        idx = self.client._q.bars("999999", frequency=9, offset=1)
        return str(idx["datetime"].iloc[-1])[:10]

    # ----------------------------------------------------------- 数据层
    def _prepare_data(self, run_date: str) -> dict:
        full_snap = self.client.get_full_snapshot(run_date)
        candidates, exclude_log = build_universe(self.client, self.cfg, run_date)

        # 板块维度 + 行业维度映射：必须在建快照循环之前执行，否则 industry_name 无法写入 Snapshot
        sectors = build_sectors(
            candidates,
            self.cfg,
            self.client.blocks_type2(),
            load_sector_map(self.cfg.sector_map_path),
        )

        metrics: dict = {}
        snaps: dict = {}
        for _, row in candidates.iterrows():
            code = str(row["code"])
            bars = self.client.daily(code, offset=120)
            xdxr = self.client.xdxr(code)
            bars_qfq = build_qfq(bars, xdxr)
            snap = snapshot_from_row(row)
            m = enrich(bars_qfq, bars, self.cfg, snap)
            ex_dates = set()
            if len(xdxr):
                for _, e in xdxr[xdxr["category"] == 1].iterrows():
                    try:
                        ex_dates.add(f"{int(e['year'])}-{int(e['month']):02d}-{int(e['day']):02d}")
                    except Exception:
                        pass
            m.is_ex_dividend = run_date in ex_dates
            metrics[code] = m
            snaps[code] = snap

        # 题材主线（真实题材榜；mootdx 无题材板块名，缺失则退化为 block_type==2 口径）
        thematic = load_thematic(self.cfg, run_date)
        kept, truncate_log = screen(candidates, metrics, sectors, self.cfg)
        kept = _dedupe_kept(kept)
        rec = reconcile(self.cfg, run_date, full_snap)
        market = self._build_market(run_date, full_snap)
        prev_map = _prev_holdings_map(self.cfg, run_date)
        datapack = build_datapack(kept, self.client, self.cfg, run_date, prev_map)
        return dict(
            candidates=candidates, metrics=metrics, snaps=snaps, sectors=sectors,
            kept=kept, truncate_log=truncate_log, exclude_log=exclude_log,
            datapack=datapack, rec=rec, market=market, full_snap=full_snap,
            thematic=thematic,
        )

    def _build_market(self, run_date: str, full_snap: pd.DataFrame) -> dict:
        idx = self.client.index_quotes()
        valid = full_snap[(full_snap["price"] > 0) & (full_snap["amount"] > 0)].copy()
        turnover_yi = float(valid["amount"].sum()) / 1e8 if len(valid) else None
        valid["pct"] = valid.apply(
            lambda r: (r["price"] - r["last_close"]) / r["last_close"] * 100.0
            if (r["last_close"] and r["last_close"] > 0)
            else None,
            axis=1,
        )
        up = int((valid["pct"] > 0).sum())
        down = int((valid["pct"] < 0).sum())
        valid["limit_pct"] = valid["code"].apply(limit_pct_of)
        valid["limit_up_price"] = valid.apply(
            lambda r: r["last_close"] * (1 + r["limit_pct"] / 100.0) * 0.9995
            if (r["last_close"] and r["limit_pct"])
            else None,
            axis=1,
        )
        limit_up = int((valid["price"] >= valid["limit_up_price"]).sum())
        broken = int(((valid["high"] >= valid["limit_up_price"]) & (valid["price"] < valid["limit_up_price"])).sum())
        sh = _idx_dict(idx[idx["code"] == "999999"].iloc[0]) if len(idx) and len(idx[idx["code"] == "999999"]) else None
        sz = _idx_dict(idx[idx["code"] == "399001"].iloc[0]) if len(idx) and len(idx[idx["code"] == "399001"]) else None
        return {
            "sh_index": sh,
            "sz_index": sz,
            "turnover_yi": round(turnover_yi, 2) if turnover_yi else None,
            "turnover_chg": None,  # A5 首日 N/A（需前一日基准）
            "up": up,
            "down": down,
            "limit_up": limit_up,
            "broken_board": broken,
            "max_board": None,  # A4 需跨日状态机，P0 输出 N/A
        }

    # ----------------------------------------------------------- 复盘分支
    def _review_for(self, c, review_result, mode: str) -> dict:
        if review_result and review_result.get("stocks"):
            for s in review_result["stocks"]:
                if s.get("code") == c.code:
                    return s
        status = "AI_FAILED" if mode == "auto" else "待补充"
        return {
            "review_status": status,
            "volume_price": "",
            "capital": {"type": "", "evidence": "", "strength": ""},
            "sector_role": {"type": "", "basis": ""},
            "levels": {"support": None, "support_basis": "", "resistance": None, "resistance_basis": ""},
            "entry": {"trigger": "", "zone": "", "stop_loss": None, "target": None, "odds": None},
            "falsify": "",
            "risk": [],
        }

    def _build_payload(self, run_date, mode, market, sectors, kept, datapack, rec, truncate_log, exclude_log, review_result, qr, has_ai, thematic=None) -> dict:
        weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][datetime.strptime(run_date, "%Y-%m-%d").weekday()]
        dp_stocks = {s["code"]: s for s in datapack["stocks"]}
        stocks = []
        for c in kept:
            dp = dp_stocks.get(c.code, {})
            bars20 = dp.get("bars_20", [])
            review = self._review_for(c, review_result, mode)
            stocks.append(
                {
                    "code": c.code,
                    "name": c.name,
                    "tags": c.tags,
                    "priority": c.priority,
                    "strength": round(c.strength, 2),
                    "price": c.snap.price,
                    "pct": c.snap.pct,
                    "amount_yi": c.snap.amount_yi,
                    "turnover": c.snap.turnover,
                    "vr": c.metrics.vr,
                    "float_mv": c.snap.float_mv,
                    "ma5": c.metrics.ma5,
                    "ma10": c.metrics.ma10,
                    "ma20": c.metrics.ma20,
                    "vma5": c.metrics.vma5,
                    "block_name": c.sector.block_name if c.sector else board_of(c.code),
                    "sector_block": c.sector.block_name if c.sector else board_of(c.code),
                    "industry_name": c.snap.industry_name if c.snap.industry_name else None,
                    "sector_rank": c.sector.block_rank if c.sector else None,
                    "sector_pct_weighted": round(c.sector.pct_weighted, 2) if c.sector and c.sector.pct_weighted is not None else None,
                    "limit_up_price": dp.get("limit_up_price"),
                    "limit_down_price": dp.get("limit_down_price"),
                    "is_broken_board": c.snap.is_broken_board,
                    "is_ex_dividend": c.metrics.is_ex_dividend,
                    "trace": c.trace,
                    "spark_svg": make_sparkline(bars20, PALETTE),
                    "review": review,
                    "yesterday_history": dp.get("yesterday_history"),
                    "missing": c.snap.missing,
                }
            )
        # 默认按盈亏比降序排列（None 置于末尾）
        def _odds_of(s):
            try:
                o = s.get("review", {}).get("entry", {}).get("odds")
            except Exception:
                o = None
            return o if o is not None else float("-inf")

        stocks.sort(key=_odds_of, reverse=True)

        sectors_top = sorted([s for s in sectors.values() if s.pct_weighted is not None], key=lambda s: s.block_rank)[: self.cfg.K]
        sectors_top = [
            {"block_name": s.block_name, "block_rank": s.block_rank,
             "industry_code": s.industry_code, "industry_name": s.industry_name, "rank": s.rank,
             "pct_weighted": s.pct_weighted, "amount_yi": s.amount_yi, "member_count": s.member_count}
            for s in sectors_top
        ]
        # 概念板块过滤（用户要求「板块数据只用概念板块」）：从 block_type==2 中筛概念类，
        # 作为题材榜缺失时的退化展示；真实概念板块来自题材榜（thematic.top_thematic）。
        sectors_concept = [
            {"block_name": s.block_name, "block_rank": s.block_rank,
             "pct_weighted": s.pct_weighted, "amount_yi": s.amount_yi, "member_count": s.member_count}
            for s in sectors.values()
            if s.pct_weighted is not None and is_concept_block(s.block_name)
        ]
        sectors_concept.sort(key=lambda s: s["pct_weighted"], reverse=True)
        summary = (review_result.get("summary") if review_result else {}) or {}
        first_day = (len(rec) == 0 and _is_first_day(self.cfg, run_date))
        return {
            "date": run_date,
            "weekday": weekday,
            "mode": mode,
            "data_cutoff": "15:00 收盘",
            "palette": PALETTE,
            "css_vars": _css_vars(PALETTE),
            "market": market,
            "quality": qr.to_dict() if qr is not None else None,
            "sectors_top": sectors_top,
            "sectors_concept": sectors_concept,
            "stocks": stocks,
            "reconcile": rec,
            "truncate_log": truncate_log,
            "exclude_log": exclude_log.to_dict("records") if len(exclude_log) else [],
            "config": self.cfg.redacted(),
            "summary": summary,
            "first_day": first_day,
            "has_ai": has_ai,
            "thematic": thematic,
        }

    def _write_outputs(self, run_date, mode, data, rec, qr, elapsed, market) -> None:
        out_dir = Path(self.cfg.output_dir) / run_date
        out_dir.mkdir(parents=True, exist_ok=True)
        # datapack.json
        (out_dir / "datapack.json").write_text(json.dumps(data["datapack"], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        # quality.json
        (out_dir / "quality.json").write_text(json.dumps(qr.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        # HTML
        render_review(run_date, data["payload"], str(out_dir / f"review-{run_date}.html"))
        # run_meta
        self._write_run_meta(run_date, mode, elapsed, market, data["sectors"], data["kept"], qr, data.get("summary", {}), data.get("thematic"))

    def _write_run_meta(self, run_date, mode, elapsed, market, sectors, kept, qr, summary, thematic=None) -> None:
        # 核心主线优先级：① 已落盘真实题材榜（westock/wind 核验，零幻觉）> ② AI 摘要 main_line
        # > ③ 退化为 block_type==2 rank==1（宽基/指数板块，非题材）。mootdx 无题材板块名，
        # 旧逻辑仅能给出科创50/沪深300 等指数口径，结构性无法反映「AI短剧」类题材主线。
        if thematic and thematic.get("main_line"):
            main_line = thematic["main_line"]
            main_line_source = "题材榜(腾讯/Wind)"
        else:
            best = next((s for s in sectors.values() if s.rank == 1), None)
            main_line = (summary.get("main_line") if summary else None) or (best.block_name if best else None)
            main_line_source = "block_type==2(宽基/指数)" if not (summary and summary.get("main_line")) else "AI摘要"
        meta = {
            "date": run_date,
            "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][datetime.strptime(run_date, "%Y-%m-%d").weekday()],
            "mode": mode,
            "elapsed_sec": round(elapsed, 1),
            "kept": len(kept),
            "quality_passed": qr.passed,
            "sh_index": market.get("sh_index"),
            "main_line": main_line,
            "main_line_source": main_line_source,
        }
        (Path(self.cfg.output_dir) / run_date / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # ----------------------------------------------------------- 市场总结：本地引擎 / 真实 LLM
    # 设计：① per-stock 8 项复盘始终由本地确定性引擎产出（零幻觉、零外部 LLM）。
    #       ② 市场总结（需要关注的点 + 公司）在 llm 模式 / auto 且配置了 api_key 时，
    #          由真实 LLM（DeepSeek）基于【真实信号】归纳；LLM 不可用或输出不合格时
    #          自动降级本地引擎确定性总结，绝不中断、绝不编造（零幻觉底线）。

    def _build_llm_prompt(self, datapack, thematic, reviewed_by_code) -> str:
        """构造喂给真实 LLM 的上下文：仅含真实信号，禁止自由发挥（零幻觉）。"""
        stocks = datapack.get("stocks", [])
        # 预筛选：高优先级 + 非涨停 + (突破 or 板块强势)，取前 8 作为 LLM 唯一可引用标的
        cands = [
            s for s in stocks
            if s.get("priority") == "高" and not _is_limit_up(s)
            and (s.get("break_up") or ("板块强势" in (s.get("tags") or [])))
        ]
        cands.sort(key=lambda s: (s.get("pct") or 0), reverse=True)
        cands = cands[:8]

        cand_lines = []
        for s in cands:
            code = s["code"]
            rv = reviewed_by_code.get(code, {}) or {}
            entry = rv.get("entry", {}) or {}
            levels = rv.get("levels", {}) or {}
            reasons = rv.get("selected_reason", []) or []
            risk = rv.get("risk", []) or []
            tags = "/".join(s.get("tags") or []) or "—"
            block = s.get("block_name") or board_of(code)
            parts = [
                f"{code} {s.get('name', '')}",
                f"板块={block}",
                f"涨跌幅={_fmt(s.get('pct'))}%",
                f"标签={tags}",
                f"板块强势={'是' if '板块强势' in (s.get('tags') or []) else '否'}",
                f"放量突破={'是' if s.get('break_up') else '否'}",
                f"支撑={_fmt(levels.get('support'))}({levels.get('support_basis', '')})",
                f"压力={_fmt(levels.get('resistance'))}({levels.get('resistance_basis', '')})",
                f"盈亏比={_fmt(entry.get('odds'))}",
                f"介入触发={entry.get('trigger') or '—'}",
                f"止损={_fmt(entry.get('stop_loss'))}",
                f"目标={_fmt(entry.get('target'))}",
                f"入选理由={'；'.join(reasons)}",
                f"风险={'；'.join(risk[:2])}",
            ]
            cand_lines.append(" | ".join(parts))

        main_line = (thematic or {}).get("main_line") if thematic else None
        main_src = (thematic or {}).get("source_tool") if thematic else None
        concept = sorted(
            [s for s in stocks if s.get("sector_pct_weighted") is not None],
            key=lambda s: s["sector_pct_weighted"], reverse=True,
        )[:5]
        concept_lines = [
            f"- {c.get('block_name') or board_of(c['code'])}：{c['sector_pct_weighted']:.2f}%（成分排名 {c.get('sector_rank', '-')}）"
            for c in concept
        ] or ["N/A"]

        prompt = (
            "你是一名A股复盘分析师。以下是确定性引擎从真实行情数据中提取的「候选观察标的」与其真实信号（无未来函数）。"
            "请严格基于【仅下列真实数据】做归纳总结，绝对禁止编造任何未出现的数据、公司、价格或信号。\n\n"
            f"# 核心主线（题材榜真实口径）\n{main_line or 'N/A'}（来源：{main_src or 'block_type==2 降级'}）\n\n"
            "# 概念板块强弱（真实加权涨幅 Top5）\n" + "\n".join(concept_lines) + "\n\n"
            "# 候选观察标的（真实信号，仅这些代码可被引用）\n"
            + ("\n".join(cand_lines) if cand_lines else "N/A") + "\n\n"
            "# 输出要求（严格 JSON，不要任何多余文字/解释）\n"
            "{\n"
            '  "focus_points": [\n'
            '    "市场级需要关注的点（3-6条）：聚焦核心主线、最强板块强弱与退潮风险、明日观察方向、必须含一条风险提示"\n'
            "  ],\n"
            '  "watchlist": [\n'
            '    {"code": "候选代码（必须来自上面列表）", "name": "对应名称",\n'
            '     "points": ["针对该标的的1-4条关注点，必须来自其真实信号（板块强势/突破/关键价位/介入/风险），禁止编造"]}\n'
            "  ]\n"
            "}\n\n"
            "约束：\n"
            "1. watchlist 只能包含上面列出的候选代码，不得新增任何代码；若无可写标的，watchlist 返回 []。\n"
            "2. focus_points 不得出现上面数据之外的数字、公司名、价格。\n"
            "3. 这是盘中待验证信号，非投资建议；风险提示必须包含一条关于情绪/量能/主线退潮的内容。\n"
            "4. 输出必须是合法 JSON，可被 json.loads 解析。"
        )
        return prompt

    def _llm_market_summary(self, datapack, thematic, reviewed_by_code, run_date) -> dict | None:
        """调用真实 LLM 归纳市场总结；不可用/不合格时返回 None（调用方降级本地引擎）。"""
        from src.adr.llm import LLMClient, LLMUnavailableError

        try:
            client = LLMClient(self.cfg, run_date)
            prompt = self._build_llm_prompt(datapack, thematic, reviewed_by_code)
            content, _ = client.chat(prompt, "market_summary")
        except LLMUnavailableError as e:
            self.logger.warning("LLM 不可用，降级本地引擎：%s", e)
            return None

        parsed = parse_ai_json(content)
        if not isinstance(parsed, dict):
            self.logger.warning("LLM 输出无法解析为 JSON，降级本地引擎")
            return None
        focus = parsed.get("focus_points")
        watch = parsed.get("watchlist")
        if not isinstance(focus, list) and not isinstance(watch, list):
            self.logger.warning("LLM 输出缺少 focus_points/watchlist，降级本地引擎")
            return None

        stocks_by_code = {s["code"]: s for s in datapack.get("stocks", [])}
        allowed = set(stocks_by_code)
        wl = []
        for it in (watch or []):
            if not isinstance(it, dict):
                continue
            code = str(it.get("code", "")).strip()
            if code not in allowed:  # 防幻觉：剔除 LLM 凭空新增的代码
                self.logger.warning("LLM 引用了未提供代码 %s，已剔除（防幻觉）", code)
                continue
            src = stocks_by_code[code]
            pts = it.get("points")
            if not isinstance(pts, list):
                pts = [str(pts)] if pts else []
            pts = [str(p) for p in pts if p][:6]
            wl.append({
                "code": code,
                "name": src.get("name", "") or str(it.get("name", "")),
                "block": src.get("block_name") or board_of(code),
                "pct": src.get("pct"),
                "priority": src.get("priority"),
                "points": pts,
            })

        fp = [str(p) for p in (focus or []) if p][:8]
        if not wl and not fp:
            return None

        main_line = (thematic or {}).get("main_line") if thematic else None
        return {
            "main_line": main_line,
            "emotion": None,
            "watchlist": wl,
            "watchlist_text": [f"{w['code']} {w['name']}（{w['block']}）" for w in wl],
            "focus_points": fp or None,
            "llm_generated": True,
        }

    def _generate_review(self, datapack, thematic, run_date, use_llm: bool) -> dict:
        """生产完整复盘（per-stock 8 项 + 市场总结）。use_llm=True 时市场总结改由真实 LLM 归纳。"""
        from src.adr.local_review import generate_review

        review_result = generate_review(datapack, self.cfg, thematic)
        if not use_llm:
            review_result["summary"]["llm_generated"] = False
            return review_result
        reviewed_by_code = {r["code"]: r for r in review_result.get("stocks", [])}
        llm_summary = self._llm_market_summary(datapack, thematic, reviewed_by_code, run_date)
        if llm_summary is not None:
            review_result["summary"] = llm_summary
        else:
            review_result["summary"]["llm_generated"] = False
            self.logger.warning("LLM 总结不可用，已降级为本地引擎确定性总结")
        return review_result

    # ----------------------------------------------------------- 主入口
    def run(self, run_date: str, mode: str) -> int:
        t0 = time.time()
        self.logger = setup_logging(self.cfg.logs_dir, run_date)
        log = self.logger
        log.info("开始运行 date=%s mode=%s", run_date, mode)
        try:
            self.client.connect()
        except Exception as e:  # noqa: BLE001
            log.error("连接行情服务器失败: %s", e)
            print(f"[错误] 连接行情服务器失败：{e}", file=sys.stderr)
            return 1

        try:
            self.client.assert_data_ready(run_date)
        except DataUnavailableError as e:
            log.error("数据可用性断言失败：%s", e)
            print(f"[断言失败] {e}（不写任何输出文件，退出码 1）", file=sys.stderr)
            return 1
        log.info("数据可用性断言通过")

        data_layer = self._prepare_data(run_date)
        kept = data_layer["kept"]
        log.info("候选池=%d 留存 M=%d 截断=%d 排除=%d", len(data_layer["candidates"]), len(kept), len(data_layer["truncate_log"]), len(data_layer["exclude_log"]))

        logs_repo.write_holdings(self.cfg, run_date, kept)
        prev = _prev_trading_date(self.cfg, run_date)
        if prev:
            logs_repo.backfill_next_day(self.cfg, prev, data_layer["full_snap"])

        datapack = data_layer["datapack"]
        prompt_path = build_prompt(datapack, self.cfg, run_date)

        # 复盘引擎
        # per-stock 8 项复盘：始终本地确定性引擎（零幻觉、零外部 LLM，守住零幻觉底线）。
        # 市场总结（需要关注的点 + 公司）：llm 模式或 auto 且配置了 api_key 时改用真实 LLM 归纳，
        # 失败时自动降级本地引擎，绝不中断。
        review_result = None
        has_ai = False
        if mode in ("auto", "llm"):
            use_llm = (mode == "llm") or self.cfg.llm.is_available()
            try:
                review_result = self._generate_review(datapack, data_layer.get("thematic"), run_date, use_llm)
                has_ai = True
            except Exception as e:  # noqa: BLE001
                log.warning("复盘引擎异常，降级为无 AI 精复盘：%s", e)
                review_result = None
                has_ai = False

        reconcile_done = (len(data_layer["rec"]) > 0) or _is_first_day(self.cfg, run_date)
        qr = quality.check(
            payload=self._build_payload(run_date, mode, data_layer["market"], data_layer["sectors"], kept, datapack, data_layer["rec"], data_layer["truncate_log"], data_layer["exclude_log"], review_result, None, False),
            datapack=datapack,
            has_ai=has_ai,
            reconcile_done=reconcile_done,
            holdings_updated=True,
            data_date=run_date,
        )

        payload = self._build_payload(run_date, mode, data_layer["market"], data_layer["sectors"], kept, datapack, data_layer["rec"], data_layer["truncate_log"], data_layer["exclude_log"], review_result, qr, has_ai, data_layer["thematic"])
        data_layer["payload"] = payload
        data_layer["summary"] = review_result.get("summary") if review_result else {}

        self._write_outputs(run_date, mode, data_layer, data_layer["rec"], qr, time.time() - t0, data_layer["market"])
        self.rebuild_index()

        elapsed = time.time() - t0
        log.info("完成 date=%s mode=%s 耗时=%.1fs 质量=%s", run_date, mode, elapsed, qr.summary)
        print(f"[完成] {run_date} {mode} 耗时 {elapsed:.1f}s | 留存 {len(kept)} | 质量门: {qr.summary}")
        print(f"[产物] {Path(self.cfg.output_dir)/run_date/f'review-{run_date}.html'}")
        print(f"[产物] {prompt_path}")
        if mode == "prepare":
            print("[指引] 将 prompt.txt 粘贴至 AI，回填 review JSON 后执行：")
            print(f"        review.py finalize --date {run_date} --review <path>.json")
        return 0 if qr.passed else 2

    # ----------------------------------------------------------- 终审回填
    def finalize(self, run_date: str, review_json: str) -> int:
        t0 = time.time()
        self.logger = setup_logging(self.cfg.logs_dir, run_date)
        try:
            data = json.loads(Path(review_json).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[错误] 终审 JSON 读取失败：{e}", file=sys.stderr)
            return 2
        removed = set(data.get("removed", []))
        watchlist = list(data.get("watchlist", []))
        # 终审 JSON 携带「本模型」逐股复盘内容（8 项）+ 市场总结，回填至卡片
        review_result = {
            "stocks": data.get("stocks", []),
            "summary": data.get("summary", {}),
        }
        has_ai = bool(review_result["stocks"])

        self.client.connect()
        data_layer = self._prepare_data(run_date)
        kept = data_layer["kept"]
        datapack = data_layer["datapack"]
        self.logger.info("finalize：剔除 %d / 观察池 %d / 复盘已回填 %d 只", len(removed), len(watchlist), len(review_result["stocks"]))

        qr = quality.check(
            payload=self._build_payload(run_date, "finalize", data_layer["market"], data_layer["sectors"], kept, datapack, data_layer["rec"], data_layer["truncate_log"], data_layer["exclude_log"], review_result, None, has_ai),
            datapack=datapack, has_ai=has_ai, reconcile_done=True, holdings_updated=True, data_date=run_date,
        )
        payload = self._build_payload(run_date, "finalize", data_layer["market"], data_layer["sectors"], kept, datapack, data_layer["rec"], data_layer["truncate_log"], data_layer["exclude_log"], review_result, qr, has_ai, data_layer["thematic"])
        for s in payload["stocks"]:
            s["finalize_removed"] = s["code"] in removed
            s["finalize_watch"] = s["code"] in watchlist
        payload["finalize"] = {"removed": list(removed), "watchlist": watchlist}
        data_layer["payload"] = payload
        data_layer["summary"] = {}
        self._write_outputs(run_date, "finalize", data_layer, data_layer["rec"], qr, time.time() - t0, data_layer["market"])
        self.rebuild_index()
        print(f"[finalize] 已重新渲染 {Path(self.cfg.output_dir)/run_date/f'review-{run_date}.html'}（剔除 {len(removed)} / 观察池 {len(watchlist)}）")
        return 0

    # ----------------------------------------------------------- 索引
    def rebuild_index(self) -> int:
        metas = []
        base = Path(self.cfg.output_dir)
        if base.exists():
            for d in sorted(base.iterdir()):
                meta_p = d / "run_meta.json"
                if d.is_dir() and meta_p.exists():
                    try:
                        metas.append(json.loads(meta_p.read_text(encoding="utf-8")))
                    except Exception:
                        pass
        metas.sort(key=lambda m: m["date"], reverse=True)
        render_index(metas, str(base / "index.html"))
        print(f"[索引] 已重建 output/index.html（{len(metas)} 期）")
        return 0
