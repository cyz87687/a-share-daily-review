"""本地复盘引擎（"本模型"分析逻辑 · 零外部 LLM · 零幻觉）。

DESIGN / PRD P0-9：将提示词文档定义的「单股 8 项复盘 + 市场总结」方法论固化为
确定性规则。全部输入来自真实行情数据包（mootdx 字段），缺失标 N/A，禁止编造。

与 DeepSeek(llm.py) 路径互斥：``review run --mode auto`` 默认走本引擎，无需任何
api_key，可在 CI 中每日自动运行 —— 直接满足用户「不要套其他模型 / 每天自动更新」。

输出 Schema（与 _review_for / 渲染模板严格一致）：
  stocks[]: {code, review_status:'OK', volume_price, capital{type,evidence,strength},
             sector_role{type,basis}, levels{support,support_basis,resistance,resistance_basis},
             entry{trigger,zone,stop_loss,target,odds}, falsify, risk[]}
  summary:  {main_line, watchlist:[display strings], emotion}
  watchlist: [code...]  # 顶层，供 finalize 复用
"""

from collections import Counter


def _fmt(v, nd=2, suffix=""):
    """数值格式化；None → N/A（渲染口径）。"""
    if v is None:
        return "N/A"
    try:
        return f"{float(v):.{nd}f}{suffix}"
    except Exception:
        return "N/A"


def _is_limit_up(s: dict) -> bool:
    price = s.get("price")
    lup = s.get("limit_up_price")
    if price is not None and lup is not None and price >= lup * 0.995:
        return True
    pct = s.get("pct")
    if pct is not None and pct >= 9.8:
        return True
    return False


def _ma_position(s: dict):
    price = s.get("price")
    if price is None:
        return 0, []
    above = [k for k in ("ma5", "ma10", "ma20") if s.get(k) is not None and price >= s[k]]
    return len(above), above


def board_of(code: str) -> str:
    """按代码前缀返回上市板块（真实分类，零编造）；用于板块缺失时的兜底展示。"""
    c = str(code or "")
    if c.startswith(("600", "601", "603", "605")):
        return "沪市主板"
    if c.startswith(("688", "689")):
        return "科创板"
    if c.startswith(("000", "001", "002", "003")):
        return "深市主板"
    if c.startswith(("300", "301")):
        return "创业板"
    if c.startswith(("8", "4")):
        return "北交所"
    if c.startswith("9"):
        return "B股"
    return "其他"


def _review_stock(s: dict) -> dict:
    code = s.get("code")
    price = s.get("price")
    pct = s.get("pct")
    vr = s.get("vr")
    amount = s.get("amount_yi")
    turnover = s.get("turnover")
    ma5, ma10, ma20 = s.get("ma5"), s.get("ma10"), s.get("ma20")
    bu, bd = s.get("break_up"), s.get("break_down")
    bbull, bear = s.get("big_bull"), s.get("big_bear")
    long_sh = s.get("long_shadow")
    us, ls = s.get("upper_shadow"), s.get("lower_shadow")
    amp = s.get("amplitude")
    tags = s.get("tags") or []
    block = s.get("block_name") or board_of(s.get("code", ""))
    srank = s.get("sector_rank")
    spw = s.get("sector_pct_weighted")
    ind = s.get("industry_name")
    lup = s.get("limit_up_price")
    fm = s.get("float_mv")

    bars = s.get("bars_20") or []
    highs = [b["high"] for b in bars if b.get("high") is not None]
    lows = [b["low"] for b in bars if b.get("low") is not None]
    hi20 = max(highs) if highs else None
    lo20 = min(lows) if lows else None
    is_lu = _is_limit_up(s)
    n_above, above = _ma_position(s)

    # ① 量价结构
    if bbull:
        body = "大阳线"
    elif bear:
        body = "大阴线"
    else:
        body = "小实体"
    ma_s = ("站上 " + "/".join(k.upper() for k in above)) if above else "跌破全部均线"
    newhi = "20日新高突破" if (hi20 is not None and price is not None and price >= hi20 * 0.999) else ""
    if long_sh:
        shadow_s = f"；长影线（上影{_fmt(us, 2)}%/下影{_fmt(ls, 2)}%）"
    elif us is not None and ls is not None and us < 0.3 and ls < 0.3:
        shadow_s = "；近乎光头光脚"
    else:
        shadow_s = ""
    if vr is not None and vr >= 1.5:
        vol_s = "放量"
    elif vr is not None and vr < 0.8:
        vol_s = "缩量"
    else:
        vol_s = "量能平稳"
    volume_price = (
        f"收{_fmt(price)}元（{_fmt(pct, 2, '%')}，量比{_fmt(vr, 2)}，{vol_s}），{body}；{ma_s}"
        + (f"；{newhi}" if newhi else "")
        + shadow_s
        + f"。振幅{_fmt(amp, 2, '%')}。"
    )

    # ③ 资金定性
    if bbull and (vr is None or vr >= 1.2):
        ctype, strength = "主多", ("强" if (vr is not None and vr >= 2) else "中")
    elif bear:
        ctype, strength = "主空", ("强" if (vr is not None and vr >= 2) else "中")
    elif vr is not None and vr >= 1.8 and (pct or 0) > 0:
        ctype, strength = "主多", "中"
    elif vr is not None and vr >= 1.5:
        ctype, strength = "分歧", "中"
    else:
        ctype, strength = "观望", "弱"
    bull_s = "大阳(主多)" if bbull else ("大阴(主空)" if bear else "中性")
    capital = {
        "type": ctype,
        "evidence": f"量比{_fmt(vr, 2)}，成交额{_fmt(amount, 2, '亿')}，换手{_fmt(turnover, 2, '%')}；大单方向={bull_s}",
        "strength": strength,
    }

    # ④ 板块定位（block_type==2 为板块/指数板块，混合宽基指数+概念+地域，非申万/非题材主线，口径需明示）
    if "板块强势" in tags:
        stype = "题材联动（强势）"
    elif srank is not None and srank <= 3:
        stype = "板块内前排"
    elif srank is not None and srank <= 10:
        stype = "板块内中排"
    else:
        stype = "弱势/独立"
    sbasis = f"板块 {block or 'N/A'}（成分加权涨{_fmt(spw, 2, '%')}，Rank {srank if srank is not None else 'N/A'}）"
    if ind and ind != "N/A":
        sbasis += f"；行业 {ind}"
    if "板块强势" in tags:
        sbasis += "；标签含板块强势"
    sector_role = {"type": stype, "basis": sbasis}

    # ⑤ 关键价位
    if price is not None and ma20 is not None and price >= ma20:
        support, sbasis_l = round(ma20, 2), "MA20 支撑"
    elif price is not None and ma10 is not None and price >= ma10:
        support, sbasis_l = round(ma10, 2), "MA10 支撑"
    elif ma5 is not None:
        support, sbasis_l = round(ma5, 2), "MA5 支撑"
    else:
        support, sbasis_l = (round(lo20, 2) if lo20 else None), ("20日低点支撑" if lo20 else "N/A")
    if is_lu:
        resistance, rbasis = (round(hi20, 2) if hi20 else None), "20日高点（次日连板空间）"
    else:
        resistance, rbasis = (round(hi20, 2) if hi20 else None), "20日高点"
    if resistance is None and lup is not None:
        resistance, rbasis = round(lup, 2), "涨停价"
    levels = {
        "support": support,
        "support_basis": sbasis_l,
        "resistance": resistance,
        "resistance_basis": rbasis,
    }

    # ⑥ 介入三件套（盘中假设，非投资建议）
    if is_lu:
        trigger, zone = "放量换手回封 / 分时均线低吸", f"涨停价附近换手承接（约{_fmt(price)}）"
    elif bu:
        trigger, zone = "回踩不破均线低吸", f"MA5/MA10 区间（{_fmt(ma5)}~{_fmt(ma10)}）"
    elif support is not None and price is not None and price > support:
        trigger, zone = "站上MA且放量确认", f"突破{_fmt(resistance)} 后回踩"
    else:
        trigger, zone = "破位观望 / 收复企稳再介入", f"已破{sbasis_l}（{_fmt(support)}），待收复企稳"
    stop_loss = target = odds = None
    if price is not None and resistance is not None and is_lu:
        stop_loss = round(price * 0.93, 2)  # 涨停股：隔日跌破涨停价 7% 即失效
    elif price is not None and support is not None and price > support:
        stop_loss = round(support * 0.98, 2)  # 正常：支撑下方 2% 止损
    elif price is not None:
        stop_loss = round(price * 0.95, 2)  # 已破位/支撑缺失：现价-5% 硬止损，保证风险为正
    if resistance is not None:
        target = round(resistance * 1.03, 2)
    elif price is not None:
        target = round(price * 1.05, 2)  # 无压力位：现价+5% 保守目标
    if stop_loss is not None and target is not None and price is not None:
        risk_amt = price - stop_loss
        if risk_amt > 0:
            odds = round((target - price) / risk_amt, 2)
    entry = {"trigger": trigger, "zone": zone, "stop_loss": stop_loss, "target": target, "odds": odds}

    # ⑦ 证伪条件
    falsify = f"跌破{sbasis_l}（{_fmt(support)}）且缩量（量比<1），或板块 {block or 'N/A'} 转跌/主线退潮，则证伪初判。"

    # ⑧ 核心风险
    risk = []
    if is_lu:
        risk.append("涨停板获利盘集中，次日开板回落风险")
    if long_sh:
        risk.append("长上影线，上方抛压较重")
    if fm is not None and fm < 8e9:  # 流通市值 < 80亿（8e9 元）
        risk.append("流通市值偏小，流动性/情绪波动大")
    risk.append("题材发酵不及预期或大盘情绪退潮的系统性风险")
    if not risk:
        risk.append("情绪/流动性波动风险")

    # 重点突出（供卡片精简渲染，不再逐条列全 8 项）
    body_txt = "涨停" if is_lu else body
    hl_parts = [f"{body_txt}（{_fmt(pct, 2, '%')}，量比{_fmt(vr, 2)}）{ma_s}"]
    if block and block not in ("沪市主板", "深市主板", "科创板", "创业板", "北交所"):
        hl_parts.append(f"板块「{block}」")
    else:
        hl_parts.append(f"{block}")
    if ind and ind != "N/A":
        hl_parts.append(f"行业 {ind}")
    hl_parts.append(f"支撑{_fmt(support)}/压力{_fmt(resistance)}，盈亏比{_fmt(odds)}")
    highlight = "；".join(hl_parts) + "。"

    # 入选理由（确定性合成，零幻觉：均来自真实筛选信号，非自由生成文本）
    reasons = []
    if "板块强势" in tags:
        reasons.append("板块强势：处于题材/板块联动前排，主线资金聚焦")
    elif is_lu:
        reasons.append("涨停封板：资金强势，列入连板观察")
    elif bu:
        reasons.append("放量突破：触发突破信号，站上均线打开空间")
    elif bbull:
        reasons.append("大阳线：主多资金推动，量价配合")
    elif "涨异动" in tags:
        reasons.append("涨幅异动：进入当日异动榜")
    elif "跌异动" in tags:
        reasons.append("跌幅异动：进入当日异动榜（弱势/风险释放，列观察）")
    if block and block not in ("沪市主板", "深市主板", "科创板", "创业板", "北交所") and srank is not None:
        reasons.append(f"属「{block}」板块成分（Rank {srank}），板块加权涨幅靠前")
    if vr is not None:
        if vr >= 1.5:
            reasons.append(f"放量确认（量比{vr:.2f}）")
        elif vr < 0.8:
            reasons.append(f"缩量企稳（量比{vr:.2f}）")
    if odds is not None and odds >= 2:
        reasons.append(f"风险收益比 {odds:.2f}（≥2，赔率占优）")
    pri = s.get("priority")
    if pri:
        reasons.append(f"综合优先级：{pri}")
    selected_reason = reasons if reasons else ["进入候选池（高成交额 + 异动信号）"]

    return {
        "code": code,
        "review_status": "OK",
        "volume_price": volume_price,
        "capital": capital,
        "sector_role": sector_role,
        "levels": levels,
        "entry": entry,
        "falsify": falsify,
        "risk": risk,
        "highlight": highlight,
        "selected_reason": selected_reason,
    }


def generate_review(datapack: dict, cfg=None, thematic: dict = None) -> dict:
    """由真实数据包生成完整复盘结果（stocks + summary + watchlist）。

    用户要求「每日生成完数据用 AI 总结，列出需要关注的点和公司」：本引擎（auto 模式，
    零外部 LLM、零幻觉）确定性合成 summary，包含两个维度：
      - focus_points：市场级「需要关注的点」（主线 / 候选信号统计 / 退潮风险 / 风险提示）。
      - watchlist：结构化「公司」列表，每只携带 points（关注点）、关键价位、介入与证伪
        —— 全部来自真实筛选/行情信号，非自由生成文本。

    thematic: 可选，题材主线（data/thematic/{date}.json）；提供时 summary.main_line 取题材榜口径。
    """
    stocks = datapack.get("stocks", [])
    reviewed = [_review_stock(s) for s in stocks]
    reviewed_by_code = {r["code"]: r for r in reviewed}

    # 市场总结 · 核心主线
    main_line = (thematic or {}).get("main_line") if thematic else None
    if not main_line:
        # 退化为：成分加权涨幅最高的板块
        best = max(stocks, key=lambda s: (s.get("sector_pct_weighted") or -1e9)) if stocks else {}
        main_line = best.get("block_name")

    # 明日候选观察池：高优先级 + 非涨停 + (突破 or 板块强势)，按涨幅降序取前 5
    cands = [
        s for s in stocks
        if s.get("priority") == "高" and not _is_limit_up(s)
        and (s.get("break_up") or ("板块强势" in (s.get("tags") or [])))
    ]
    cands.sort(key=lambda s: (s.get("pct") or 0), reverse=True)
    top = cands[:5]
    watch_codes = [s["code"] for s in top]

    # 结构化「公司 + 需要关注的点」（确定性合成，零幻觉：均来自真实信号）
    watchlist = []
    for s in top:
        code = s["code"]
        rv = reviewed_by_code.get(code, {})
        entry = rv.get("entry", {}) or {}
        levels = rv.get("levels", {}) or {}
        pts = []
        tags = s.get("tags") or []
        if "板块强势" in tags:
            pts.append("题材/板块联动前排，主线资金聚焦")
        elif s.get("break_up"):
            pts.append("放量突破站上均线，打开上行空间")
        elif _is_limit_up(s):
            pts.append("涨停封板，列入连板观察")
        sup, res, odds = levels.get("support"), levels.get("resistance"), entry.get("odds")
        if sup is not None or res is not None:
            pts.append(
                f"关键价位：支撑{_fmt(sup)}（{levels.get('support_basis', '')}）"
                f" / 压力{_fmt(res)}（{levels.get('resistance_basis', '')}），盈亏比{_fmt(odds)}"
            )
        if entry.get("trigger"):
            pts.append(
                f"介入：{entry.get('trigger')}；止损{_fmt(entry.get('stop_loss'))}"
                f"；目标{_fmt(entry.get('target'))}"
            )
        rk = rv.get("risk", []) or []
        if rk:
            pts.append("风险：" + "；".join(rk[:2]))
        if rv.get("falsify"):
            pts.append("证伪：" + rv["falsify"])
        watchlist.append({
            "code": code,
            "name": s.get("name", ""),
            "block": s.get("block_name") or board_of(code),
            "pct": s.get("pct"),
            "priority": s.get("priority"),
            "points": pts,
        })

    # 市场级「需要关注的点」
    focus_points = []
    if main_line:
        src = (thematic or {}).get("source_tool") if thematic else None
        focus_points.append(
            f"核心主线：{main_line}"
            + (f"（题材榜口径：{src}）" if src else "（block_type==2 降级口径）")
        )
    n_strong = sum(1 for s in top if "板块强势" in (s.get("tags") or []))
    n_break = sum(1 for s in top if s.get("break_up"))
    focus_points.append(
        f"明日观察候选 {len(top)} 只（高优先级·非涨停）：其中板块强势前排 {n_strong} 只、放量突破 {n_break} 只"
    )
    best_concept = max(stocks, key=lambda s: (s.get("sector_pct_weighted") or -1e9)) if stocks else {}
    if best_concept.get("sector_pct_weighted") is not None and best_concept["sector_pct_weighted"] < 0:
        focus_points.append(
            f"⚠ 最强板块「{best_concept.get('block_name')}」加权涨幅转负"
            f"（{_fmt(best_concept['sector_pct_weighted'])}%），警惕主线退潮"
        )
    focus_points.append("风险提示：以上均为盘中待验证信号，非投资建议；关注大盘情绪与量能变化。")

    summary = {
        "main_line": main_line,
        "emotion": None,
        "watchlist": watchlist,                       # 结构化公司列表（含关注点）
        "watchlist_text": [f"{w['code']} {w['name']}（{w['block']}）" for w in watchlist],  # 兼容旧渲染
        "focus_points": focus_points,                 # 市场级需要关注的点
    }

    return {
        "date": datapack.get("date"),
        "stocks": reviewed,
        "summary": summary,
        "watchlist": watch_codes,  # 顶层，供 finalize 复用
    }
