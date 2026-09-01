"""质量门 6 项校验（DESIGN / PRD P0-11 / 8.5）。

① 数值溯源 ② 每只有证伪条件 ③ 非首日对账已执行 ④ 无裸预测词 ⑤ 留存日志已更新
⑥ 输出含数据截止日期。
不通过 → 标红发布（不阻断，exit 2）。prepare 模式 AI 相关项（②④）跳过，视为通过。
"""

import re

from src.adr.types import QualityReport

# 裸预测词（上游禁止清单）
_NAKED = ["有望", "应该会", "谨慎乐观", "我觉得", "大概率涨", "必然涨", "必定"]


def check(
    payload: dict,
    datapack: dict,
    has_ai: bool,
    reconcile_done: bool,
    holdings_updated: bool,
    data_date: str,
) -> QualityReport:
    """执行 6 项质量门校验，返回 QualityReport（passed=无违规项）。"""
    qr = QualityReport()
    violations: list = []

    # ① 数值溯源：数据包存在且每只标的含价格/涨跌幅（缺失为 None 仍是合法 N/A）
    stocks = datapack.get("stocks") if datapack else None
    if stocks:
        qr.numeric_traceable = True
        for s in stocks:
            if s.get("price") is None and s.get("pct") is None:
                qr.numeric_traceable = False
                violations.append("① 数值溯源：存在标的无价格且无涨跌幅，无法溯源")
                break
    else:
        qr.numeric_traceable = False
        violations.append("① 数值溯源：数据包为空，无法溯源")

    # ② 每只有证伪条件（仅 AI 模式）
    if has_ai:
        ok = True
        for s in payload.get("stocks", []):
            rev = s.get("review")
            if isinstance(rev, dict) and not rev.get("falsify"):
                ok = False
        qr.all_have_falsify = ok
        if not ok:
            violations.append("② 部分个股缺少证伪条件")
    else:
        qr.all_have_falsify = True  # prepare 无 AI，跳过

    # ③ 非首日对账已执行
    qr.reconcile_done = bool(reconcile_done)
    if not reconcile_done:
        violations.append("③ 非首日对账未执行")

    # ④ 无裸预测词（仅 AI 模式，扫描 AI 输出全文）
    if has_ai:
        text = json_dumps(payload.get("stocks", []))
        hit = [w for w in _NAKED if w in text]
        qr.no_naked_prediction = not hit
        if hit:
            violations.append(f"④ 发现裸预测词：{hit}")
    else:
        qr.no_naked_prediction = True  # prepare 无 AI，跳过

    # ⑤ 留存日志已更新
    qr.holdings_updated = bool(holdings_updated)
    if not holdings_updated:
        violations.append("⑤ 留存日志未更新")

    # ⑥ 输出含数据截止日期
    qr.has_data_date = bool(data_date)
    if not data_date:
        violations.append("⑥ 输出缺少数据截止日期")

    qr.violations = violations
    qr.passed = len(violations) == 0
    qr.summary = "质量门 6/6 通过" if qr.passed else f"质量门 {len(violations)} 项未通过：" + "；".join(violations)
    return qr


def json_dumps(obj) -> str:
    """安全序列化（忽略非序列化对象）。"""
    import json

    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)
