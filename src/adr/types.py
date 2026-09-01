"""核心数据结构定义（dataclass）。

层间以 dataclass / JSON 可序列化 dict 传递，无任何反向依赖。
零幻觉硬约束：取不到的字段一律为 ``None``（数值）或 ``"N/A"``（字符串），
禁止用 0 / 空串 / 行业均值 / 历史值顶替（DESIGN 8.4）。
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Bar:
    """单根日线（含自实现前复权字段）。"""

    date: str
    open: float
    high: float
    low: float
    close: float
    vol: float
    amount: float
    open_qfq: float
    high_qfq: float
    low_qfq: float
    close_qfq: float
    adj_factor: float


@dataclass
class Snapshot:
    """当日行情快照（派生字段齐全，均标注来源）。"""

    code: str
    name: str
    price: float
    last_close: float
    open: float
    high: float
    low: float
    vol: float
    amount: float
    pct: float
    amount_yi: float
    turnover: Optional[float] = None          # 需 liutongguben（finance）
    float_mv: Optional[float] = None           # 需 liutongguben（finance）
    industry_code: Optional[str] = None        # 需 industry（finance）
    industry_name: Optional[str] = None        # 需 industry_map 映射
    ipo_date: Optional[date] = None            # 需 ipo_date（finance）
    listed_days: Optional[int] = None          # 由 ipo_date 推算
    limit_pct: Optional[float] = None          # 由代码前缀推断
    is_st: bool = False
    is_suspended: bool = False
    is_yiziban: bool = False
    is_limit_up: bool = False
    is_broken_board: bool = False
    # 缺失字段 → 原因（零幻觉溯源）
    missing: dict = field(default_factory=dict)

    def na(self, field_name: str, reason: str) -> None:
        """登记某字段缺失原因（不赋值，渲染层据此显示 N/A）。"""
        self.missing[field_name] = reason


@dataclass
class StockMetrics:
    """派生指标（前复权口径算均线/形态；不复权口径算涨跌幅/影线）。"""

    code: str
    ma5: Optional[float] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    vma5: Optional[float] = None               # 5日均量（原始 vol）
    vma10: Optional[float] = None              # 10日均量（原始 vol）
    vma20: Optional[float] = None              # 20日均量（原始 vol）
    vr: Optional[float] = None                 # 量比 = 当日量 / 5日均量（日线近似，标注口径）
    amplitude: Optional[float] = None          # 振幅 %
    upper_shadow: Optional[float] = None       # 上影线 %
    lower_shadow: Optional[float] = None       # 下影线 %
    break_up: bool = False                     # 收盘站上 MA（前收≤MA）
    break_down: bool = False                   # 收盘跌破 MA（前收≥MA）
    big_bull: bool = False                     # 大阳（按 limit_pct 分板）
    big_bear: bool = False                     # 大阴（按 limit_pct 分板）
    long_shadow: bool = False                  # 长影线
    is_ex_dividend: bool = False               # 当日为除权除息日
    tags: list = field(default_factory=list)   # 形态维度标签


@dataclass
class SectorStat:
    """板块（通达信 block_type==2 板块维度）统计。

    双轨承载：``block_name``/``block_rank`` 为板块维度（20 板块，名称干净）；
    ``industry_code``/``industry_name`` 保留用于行业维度（可插拔映射，板块级一般为 None）。
    """

    block_name: str = ""                       # 板块名（block_type==2 干净名，如 创业板指/沪深300）
    block_rank: int = 0                         # 板块排名（按 pct_weighted 降序）
    industry_code: Optional[str] = None        # 行业维度兼容（板块级一般 None）
    industry_name: Optional[str] = None         # 行业维度兼容（板块级一般 None）
    pct_weighted: Optional[float] = None        # 成分股流通市值加权涨幅
    rank: int = 0                               # 兼容别名 = block_rank
    amount_yi: Optional[float] = None
    amount_chg: Optional[float] = None          # 环比（P0 多数为 N/A，见 DESIGN A5）
    member_count: int = 0
    members: list = field(default_factory=list)


@dataclass
class Candidate:
    """四维筛选后留存的个股（含全部溯源信息）。"""

    code: str
    name: str
    tags: list
    priority: str                              # 高 / 中 / 低
    strength: float                            # 量价强度 = |pct| × 量比
    snap: Snapshot
    metrics: StockMetrics
    sector: Optional[SectorStat]
    trace: dict                                # 每个标签触发时的原始数值（供溯源）

    def to_dict(self) -> dict:
        """序列化为 dict（供数据包 / 渲染使用）。"""
        return {
            "code": self.code,
            "name": self.name,
            "tags": list(self.tags),
            "priority": self.priority,
            "strength": round(self.strength, 4) if self.strength is not None else None,
            "snap": _snap_to_dict(self.snap),
            "metrics": _metrics_to_dict(self.metrics),
            "sector": _sector_to_dict(self.sector),
            "trace": self.trace,
        }


@dataclass
class QualityReport:
    """质量门报告（6 项；不通过=标红不阻断）。"""

    numeric_traceable: bool = False            # ① 数值溯源
    all_have_falsify: bool = False             # ② 每只有证伪条件
    reconcile_done: bool = False               # ③ 非首日对账已执行
    no_naked_prediction: bool = False          # ④ 无裸预测词
    holdings_updated: bool = False             # ⑤ 留存日志已更新
    has_data_date: bool = False                # ⑥ 输出含数据截止日期
    violations: list = field(default_factory=list)
    passed: bool = False
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "numeric_traceable": self.numeric_traceable,
            "all_have_falsify": self.all_have_falsify,
            "reconcile_done": self.reconcile_done,
            "no_naked_prediction": self.no_naked_prediction,
            "holdings_updated": self.holdings_updated,
            "has_data_date": self.has_data_date,
            "violations": list(self.violations),
            "passed": self.passed,
            "summary": self.summary,
        }


@dataclass
class ReviewResult:
    """AI 精复盘结构化结果（对齐上游提示词第八节 Schema）。"""

    date: str
    market: dict
    stocks: list
    summary: dict
    reconcile: list
    quality: QualityReport


# ---------------------------------------------------------------------------
# 序列化辅助（None 保持为 None，渲染层统一判 N/A）
# ---------------------------------------------------------------------------
def _snap_to_dict(s: Snapshot) -> dict:
    return {
        "code": s.code,
        "name": s.name,
        "price": s.price,
        "last_close": s.last_close,
        "open": s.open,
        "high": s.high,
        "low": s.low,
        "vol": s.vol,
        "amount": s.amount,
        "pct": s.pct,
        "amount_yi": s.amount_yi,
        "turnover": s.turnover,
        "float_mv": s.float_mv,
        "industry_code": s.industry_code,
        "industry_name": s.industry_name,
        "ipo_date": s.ipo_date.isoformat() if s.ipo_date else None,
        "listed_days": s.listed_days,
        "limit_pct": s.limit_pct,
        "is_st": s.is_st,
        "is_suspended": s.is_suspended,
        "is_yiziban": s.is_yiziban,
        "is_limit_up": s.is_limit_up,
        "is_broken_board": s.is_broken_board,
        "missing": s.missing,
    }


def _metrics_to_dict(m: StockMetrics) -> dict:
    return {
        "code": m.code,
        "ma5": m.ma5,
        "ma10": m.ma10,
        "ma20": m.ma20,
        "vma5": m.vma5,
        "vma10": m.vma10,
        "vma20": m.vma20,
        "vr": m.vr,
        "amplitude": m.amplitude,
        "upper_shadow": m.upper_shadow,
        "lower_shadow": m.lower_shadow,
        "break_up": m.break_up,
        "break_down": m.break_down,
        "big_bull": m.big_bull,
        "big_bear": m.big_bear,
        "long_shadow": m.long_shadow,
        "is_ex_dividend": m.is_ex_dividend,
        "tags": list(m.tags),
    }


def _sector_to_dict(s: Optional[SectorStat]) -> Optional[dict]:
    if s is None:
        return None
    return {
        "block_name": s.block_name,
        "block_rank": s.block_rank,
        "industry_code": s.industry_code,
        "industry_name": s.industry_name,
        "pct_weighted": s.pct_weighted,
        "rank": s.rank,
        "amount_yi": s.amount_yi,
        "amount_chg": s.amount_chg,
        "member_count": s.member_count,
        "members": list(s.members),
    }
