"""验证真实 LLM 市场总结路径：prompt 构造 + 解析 + 防幻觉剔除 + 本地降级。

不涉及任何真实行情/网络/API key；LLMClient 以本地 stub 替代。
"""
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.adr.config import Config
from src.adr.pipeline import Pipeline
import src.adr.llm as llm_mod


# ---- 构造合成数据包（仅含 prompt/解析所需真实字段） ----
def mk_stock(code, name, pct, block, tags, spw, srank, break_up):
    return {
        "code": code, "name": name, "priority": "高", "pct": pct,
        "price": 10.0 + pct / 10.0, "amount_yi": 5.0, "turnover": 3.0,
        "vr": 1.8, "float_mv": 1.2e10, "ma5": 10.0, "ma10": 9.8, "ma20": 9.5,
        "vma5": 1.0, "block_name": block, "sector_rank": srank,
        "sector_pct_weighted": spw, "limit_up_price": 11.0, "limit_down_price": 9.0,
        "is_broken_board": False, "is_ex_dividend": False, "trace": {},
        "bars_20": [], "yesterday_history": "N/A", "missing": {},
        "tags": tags, "break_up": break_up,
    }


def mk_review(code, support, resistance, odds):
    return {
        "code": code, "review_status": "OK", "highlight": "x", "selected_reason": ["板块强势", "放量确认"],
        "entry": {"trigger": "回踩低吸", "zone": "z", "stop_loss": round(support * 0.98, 2),
                  "target": round(resistance * 1.03, 2), "odds": odds},
        "levels": {"support": support, "support_basis": "MA20", "resistance": resistance, "resistance_basis": "20日高"},
        "risk": ["流动性波动", "退潮风险"],
    }


datapack = {
    "date": "2026-09-01",
    "stocks": [
        mk_stock("300502", "新易盛", 6.2, "CPO", ["板块强势", "涨异动"], 4.1, 1, False),
        mk_stock("002463", "沪电股份", 5.0, "PCB", ["板块强势"], 3.6, 2, False),
        mk_stock("688981", "中芯国际", 3.1, "半导体", ["突破"], 2.0, 5, True),
        mk_stock("300394", "天孚通信", 2.0, "CPO", [], 1.0, 8, False),
    ],
}
reviewed_by_code = {s["code"]: mk_review(s["code"], 9.5, 11.0, 2.4) for s in datapack["stocks"]}
thematic = {"main_line": "CPO/光模块", "source_tool": "腾讯自选股", "main_line_change_pct": 4.1,
            "main_line_main_net_inflow_yi": 12.3, "main_line_up": 3, "main_line_leader": "新易盛",
            "top_thematic": []}


# ---- 1) prompt 构造 ----
cfg = Config.load(ROOT / "config.yaml")
pipe = Pipeline(cfg)
pipe.logger = logging.getLogger("test")
prompt = pipe._build_llm_prompt(datapack, thematic, reviewed_by_code)
print("=== PROMPT (head) ===")
print(prompt[:600])
print("...[truncated]...")
assert "300502 新易盛" in prompt, "prompt 应包含真实候选"
assert "999999" not in prompt, "prompt 不得含编造代码"
assert "仅下列真实数据" in prompt
print("[OK] _build_llm_prompt 构造正常，仅含真实候选代码\n")


# ---- 2) 解析 + 防幻觉（LLM 返回了一个编造代码 600519） ----
class FakeClient:
    def __init__(self, cfg, run_date):
        self.cfg, self.date = cfg, run_date

    def chat(self, prompt, batch_id):
        fake = {
            "focus_points": [
                "核心主线 CPO/光模块 延续强势，成分加权涨 4.1%",
                "最强板块 CPO 未转负，但需警惕半导体分化",
                "风险提示：情绪退潮+量能萎缩时止损",
            ],
            "watchlist": [
                {"code": "300502", "name": "新易盛", "points": ["板块强势前排", "盈亏比 2.4 占优"]},
                {"code": "002463", "name": "沪电股份", "points": ["PCB 板块联动", "突破均线"]},
                {"code": "600519", "name": "贵州茅台", "points": ["编造的标的应被剔除"]},  # 幻觉
            ],
        }
        return json.dumps(fake), {"attempt": "live"}


llm_mod.LLMClient = FakeClient
summary = pipe._llm_market_summary(datapack, thematic, reviewed_by_code, "2026-09-01")
assert summary is not None, "summary 不应为 None"
codes = [w["code"] for w in summary["watchlist"]]
print("LLM 返回 watchlist 代码（已过滤）：", codes)
assert "600519" not in codes, "防幻觉失败：编造代码未被剔除"
assert "300502" in codes and "002463" in codes
# 真实字段被富化（pct/priority 来自真实数据，非 LLM）
w = next(x for x in summary["watchlist"] if x["code"] == "300502")
assert w["pct"] == 6.2 and w["priority"] == "高", "pct/priority 应来自真实数据包"
assert summary["llm_generated"] is True
assert summary["main_line"] == "CPO/光模块"
print("[OK] _llm_market_summary 解析正常，幻觉代码已剔除，真实字段已富化\n")


# ---- 3) LLM 不可用 → 返回 None（调用方降级本地引擎） ----
class UnavailClient:
    def __init__(self, cfg, run_date):
        self.cfg, self.date = cfg, run_date

    def chat(self, prompt, batch_id):
        from src.adr.llm import LLMUnavailableError
        raise LLMUnavailableError("no key")


llm_mod.LLMClient = UnavailClient
summary2 = pipe._llm_market_summary(datapack, thematic, reviewed_by_code, "2026-09-01")
assert summary2 is None, "LLM 不可用时应返回 None 触发降级"
print("[OK] LLM 不可用时返回 None（将降级本地引擎）\n")


# ---- 4) _generate_review 装配（use_llm=True 真实路径 / False 本地路径） ----
llm_mod.LLMClient = FakeClient  # 恢复可用
res_llm = pipe._generate_review(datapack, thematic, "2026-09-01", use_llm=True)
assert res_llm["summary"]["llm_generated"] is True
assert res_llm["stocks"], "per-stock 8 项复盘应来自本地引擎"
res_local = pipe._generate_review(datapack, thematic, "2026-09-01", use_llm=False)
assert res_local["summary"]["llm_generated"] is False
assert res_local["summary"]["focus_points"], "本地引擎应仍有 focus_points"
print("[OK] _generate_review 装配正确：per-stock 始终本地，summary 随 use_llm 切换\n")

print("ALL_TESTS_PASSED")
