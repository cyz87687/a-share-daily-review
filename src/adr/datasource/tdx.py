"""mootdx 在线行情封装（DESIGN 第二章 / 零踩坑 K1–K5）。

关键约束落地：
- K1 双市场合并：``stocks(market=1) + stocks(market=0)``（默认仅沪市，缺失深市）。
- K2 去重 + 覆盖率：``quotes()`` 按 code 去重；覆盖率 <0.90 直接断言失败退出。
- K3 脏零值：``price>0 and amount>0 and vol>0`` 判为可交易，否则停牌/无效剔除。
- K4 自实现前复权：禁止 ``bars(adjust=...)``（pandas 3.0.3 崩溃），走 ``adjust.py``。
- K5 ``block()`` 仅取 ``block_type==2``：板块分组走干净的 20 个板块（含 ST板块），
  其余 block_type 绝大多数 blockname 乱码，视为乱码丢弃。

4 道数据可用性断言（``assert_data_ready``）：任一失败抛 ``DataUnavailableError``，
上层 ``sys.exit(1)`` 且不写任何输出文件（DESIGN 2.4）。
"""

import re

import pandas as pd

from src.adr.datasource.cache import Cache

# A股判定正则（DESIGN 8.3）：60/68/00/30 开头 6 位
_A_SHARE_RE = re.compile(r"^(60|68|00|30)\d{4}$")
# 基准指数（用于断言2最新交易日 & 市场温度上证/深证）
_BENCH_INDEX = "999999"
_SHENZHEN_INDEX = "399001"


class DataUnavailableError(Exception):
    """数据可用性断言失败。上层应 sys.exit(1) 且不写任何输出。"""


def is_a_share(code) -> bool:
    """判断代码是否为 A股（保留前导零）。"""
    return bool(_A_SHARE_RE.match(str(code)))


def limit_pct_of(code):
    """涨跌幅限幅：30/68→20%，60/00→10%，其余→None（渲染 N/A）。"""
    s = str(code)
    if s.startswith(("30", "68")):
        return 20.0
    if s.startswith(("60", "00")):
        return 10.0
    return None


class TdxClient:
    """通达信在线行情客户端。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self._q = None
        self.cache = Cache(cfg)
        self._date = None
        self._all_stocks = None
        self._full_snap = None

    # ------------------------------------------------------------------ 连接
    def connect(self) -> None:
        """连接在线行情服务器（180.153.18.170:7709）。"""
        from mootdx.quotes import Quotes

        self._q = Quotes.factory(market="std")

    # ----------------------------------------------------- 4 道可用性断言
    def assert_data_ready(self, date: str) -> None:
        """4 道断言：双市场列表非空 / 基准指数最新交易日==date / 快照覆盖率≥0.90 /
        有效标的≥3000。任一失败抛 ``DataUnavailableError``（不写任何输出）。"""
        self._date = date

        # ① 沪深双市场列表非空
        sh = self._q.stocks(market=1)
        sz = self._q.stocks(market=0)
        if sh is None or len(sh) == 0 or sz is None or len(sz) == 0:
            raise DataUnavailableError("断言①失败：沪深股票列表为空（在线服务不可用或返回异常）")

        # ② 基准指数最新交易日 vs 请求日：仅禁止「未来日期」；历史日期允许重跑（依赖当日快照缓存）。
        # 旧逻辑 last_date == date 硬相等 → 历史日期一律被拦，无法复核旧复盘（DESIGN 缺陷）。
        # 新逻辑：date > last_date（未来）→ 拦；date == last_date（当日）→ 用在线行情；
        #         date < last_date（历史）→ 必须存在当日快照缓存，否则无法重建历史行情。
        idx = self._q.bars(_BENCH_INDEX, frequency=9, offset=1)
        if idx is None or len(idx) == 0:
            raise DataUnavailableError("断言②失败：无法获取基准指数(999999)日线")
        last_date = str(idx["datetime"].iloc[-1])[:10]
        if date > last_date:
            raise DataUnavailableError(
                f"断言②失败：请求日 {date} 晚于基准指数最新交易日 {last_date}"
                f"（未来日期，禁止用预测数据运行）"
            )
        if date < last_date:
            # 历史日期：必须存在当日快照缓存，禁止用今日行情顶替（零幻觉 / 防旧数据污染）
            cached = self.cache.load("snapshot", date)
            if cached is None or len(cached) == 0:
                raise DataUnavailableError(
                    f"断言②失败：请求日 {date} 早于最新交易日 {last_date}，但无当日快照缓存"
                    f"（期望 data/cache/{date}/snapshot.pkl），无法重建历史行情，禁止用今日数据顶替"
                )

        # ③ & ④ 快照覆盖率与有效标的数
        snap = self._snapshot_all(date, allow_cache=True, save=False)
        total = len(self.list_all_stocks())
        if total == 0:
            raise DataUnavailableError("断言③失败：A股列表为 0")
        valid = snap[(snap["price"] > 0) & (snap["amount"] > 0) & (snap["vol"] > 0)] if len(snap) else snap
        coverage = (len(valid) / total) if total else 0.0
        if coverage < 0.90:
            raise DataUnavailableError(
                f"断言③失败：快照覆盖率 {coverage:.3f} < 0.90（有效 {len(valid)} / 总数 {total}）"
            )
        if len(valid) < 3000:
            raise DataUnavailableError(
                f"断言④失败：有效标的数 {len(valid)} < 3000（脏零值过多，K3 未过滤）"
            )

        # 全部通过：落盘缓存（幂等重跑直接读缓存，结果一致）
        self.cache.save(snap, "snapshot", date)

    # --------------------------------------------------- 全市场列表（双市场）
    def list_all_stocks(self) -> pd.DataFrame:
        """沪深双市场合并，过滤 A股正则，返回 code/name/pre_close。

        关键修正（指数/个股混淆）：mootdx 的 ``stocks(market=1)``（沪市清单）会把一批
        **指数**误列为「股票」，代码形如 000158=上证环保 / 000300=沪深300 / 000688=科创50 /
        000016=上证50 / 000010=上证180（含 000xxx/399xxx/880xxx/0009xx）。这些指数代码与
        深市个股代码（000xxx）碰撞，会在快照合并时与真实深市个股（如 000158 常山北明）互相覆盖，
        导致「指数被当个股」或「个股被错命名」。

        修复：沪市清单仅保留沪市真实股票前缀（60/68/900）；其余一律视为指数剔除。
        深市清单本身干净（指数不落 market=0），由 is_a_share 正则兜底。
        """
        if self._all_stocks is not None:
            return self._all_stocks
        sh = self._q.stocks(market=1)
        sz = self._q.stocks(market=0)
        sh = sh if sh is not None else pd.DataFrame()
        sz = sz if sz is not None else pd.DataFrame()

        # 沪市真实股票代码前缀：600-605 / 688-689 / 900(B股)；其余 000xxx/399xxx/880xxx/0009xx 为指数
        _SH_STOCK_RE = re.compile(r"^(60\d{4}|68\d{4}|900\d{3})$")
        if len(sh):
            sh = sh.copy()
            sh["code"] = sh["code"].astype(str).str.zfill(6)
            sh = sh[sh["code"].str.match(_SH_STOCK_RE)].copy()

        df = pd.concat([sh, sz], ignore_index=True)
        df["code"] = df["code"].astype(str).str.zfill(6)
        df = df[df["code"].apply(is_a_share)].copy()
        df["name"] = df["name"].astype(str).str.strip()
        df = df.rename(columns={"pre_close": "pre_close"})
        self._all_stocks = df[["code", "name", "pre_close"]].reset_index(drop=True)
        return self._all_stocks

    # --------------------------------------------------- 全市场快照（去重）
    def _snapshot_all(self, date, allow_cache: bool = True, save: bool = False) -> pd.DataFrame:
        """拉取全市场行情快照（分批 ≤80、按 code 去重、合并名称/昨收）。

        缓存优先（幂等）。``save`` 仅在所有断言通过后被调用。"""
        if self._full_snap is not None and self._date == date:
            return self._full_snap
        if allow_cache:
            cached = self.cache.load("snapshot", date)
            if cached is not None:
                self._full_snap = cached
                self._date = date
                return cached

        stocks = self.list_all_stocks()
        codes = stocks["code"].tolist()
        rows = []
        batch = max(1, int(self.cfg.batch_size))
        for i in range(0, len(codes), batch):
            chunk = codes[i : i + batch]
            try:
                r = self._q.quotes(chunk)
            except Exception:
                r = None
            if r is not None and len(r):
                rows.append(r)

        if rows:
            snap = pd.concat(rows, ignore_index=True)
        else:
            snap = pd.DataFrame(
                columns=["market", "code", "price", "last_close", "open", "high", "low", "vol", "amount", "servertime"]
            )

        snap["code"] = snap["code"].astype(str).str.zfill(6)
        # K2：按 code 去重（保留首条）
        snap = snap.drop_duplicates(subset=["code"], keep="first")
        # 合并名称与昨收（来自 stocks 列表）
        snap = snap.merge(stocks[["code", "name", "pre_close"]], on="code", how="left")
        self._full_snap = snap
        self._date = date
        if save:
            self.cache.save(snap, "snapshot", date)
        return snap

    def get_full_snapshot(self, date: str) -> pd.DataFrame:
        """返回全市场快照（缓存优先）。"""
        return self._snapshot_all(date, allow_cache=True, save=False)

    def snapshot(self, codes, date: str = None) -> pd.DataFrame:
        """返回给定代码子集的快照（从全市场快照筛选）。"""
        d = date or self._date
        full = self._snapshot_all(d, allow_cache=True, save=False)
        codes = [str(c).zfill(6) for c in codes]
        return full[full["code"].isin(codes)].copy()

    # ----------------------------------------------------------- 日线历史
    def daily(self, symbol: str, offset: int = 120) -> pd.DataFrame:
        """日线历史（不复权）。禁止 bars(adjust=...)（K4），前复权走 adjust.py。"""
        cached = self.cache.load("bars", self._date, symbol)
        if cached is not None:
            return cached
        b = self._q.bars(str(symbol), frequency=9, offset=offset)
        if b is None or len(b) == 0:
            b = pd.DataFrame(columns=["date", "open", "close", "high", "low", "vol", "amount"])
            self.cache.save(b, "bars", self._date, symbol)
            return b
        b = b.reset_index(drop=True)
        if "datetime" in b.columns:
            b["date"] = b["datetime"].astype(str).str[:10]
        elif isinstance(b.index, pd.DatetimeIndex):
            b = b.reset_index()
            b["date"] = b["datetime"].astype(str).str[:10]
        else:
            b["date"] = None
        b = b[["date", "open", "close", "high", "low", "vol", "amount"]].copy()
        self.cache.save(b, "bars", self._date, symbol)
        return b

    # ----------------------------------------------------- 除权除息信息
    def xdxr(self, symbol: str) -> pd.DataFrame:
        """除权除息信息（category=1 除权除息，category=5 股本变化）。"""
        cached = self.cache.load("xdxr", self._date, symbol)
        if cached is not None:
            return cached
        x = self._q.xdxr(str(symbol))
        if x is None:
            x = pd.DataFrame(columns=["year", "month", "day", "category", "fenhong", "peigujia", "songzhuangu", "peigu"])
        self.cache.save(x, "xdxr", self._date, symbol)
        return x

    # ------------------------------------------------------------- 财务信息
    def finance(self, symbol: str) -> dict:
        """财务/股本信息（industry 编码、ipo_date、liutongguben、zongguben）。

        返回首行 dict；缺失返回 {}。零幻觉：取不到的字段即不在 dict 中（上层判 None）。"""
        cached = self.cache.load("finance", self._date, symbol)
        if cached is not None:
            return {} if len(cached) == 0 else cached.iloc[0].to_dict()
        f = self._q.finance(str(symbol))
        if f is None or len(f) == 0:
            empty = pd.DataFrame(columns=["industry", "ipo_date", "liutongguben", "zongguben", "market", "code"])
            self.cache.save(empty, "finance", self._date, symbol)
            return {}
        self.cache.save(f, "finance", self._date, symbol)
        return f.iloc[0].to_dict()

    # ------------------------------------------------------- 板块指数 K 线
    def block_index_bars(self, code: str, offset: int = 30) -> pd.DataFrame:
        """板块指数（880xxx）K 线，含 up_count/down_count（DESIGN 0.7）。"""
        b = self._q.index_bars(str(code), frequency=9, offset=offset)
        if b is None or len(b) == 0:
            return pd.DataFrame()
        b = b.reset_index(drop=True)
        if "datetime" in b.columns:
            b["date"] = b["datetime"].astype(str).str[:10]
        return b

    # ----------------------------------------------- 板块成员（block_type==2）
    def blocks_type2(self) -> pd.DataFrame:
        """返回 ``block_type==2`` 的 20 个干净板块（含 ST板块）成员表（DESIGN 918 禁令）。

        列：``block_name``（板块名，干净可用，如 创业板指/沪深300）、
        ``block_code``（板块自身代码）、``code``（成分股 6 位代码）。

        鲁棒性（零幻觉 / 零崩溃）：mootdx ``block()`` 返回列 Schema **非确定性**，线上
        观测到两种形态：

        - A: ``[blockname, block_type, code_index, code]``（含 block_type 列）
        - B: ``[block_name, block_code, code]``（**无 block_type 列**）

        处理策略：
        - 探测 name / type / code 列（按列名模式，不写死）；
        - 有 ``block_type`` 列 → 仅取 ``block_type == 2`` 子集；
        - 无 ``block_type`` 列（形态 B）→ 退化为 DESIGN 0.7「20 干净板块名」白名单过滤
          （该白名单即 block_type==2 板块集合，等价于禁令口径）；
        - 成分股 code 统一 zfill(6)；其余板块一律丢弃，绝不编造乱码名。

        接口失败 / 无数据 → 返回空 DataFrame（上层 build_sectors 退化为无板块维度，不崩溃）。
        结果按日缓存（DESIGN 8.7 幂等），重跑直接读缓存不打网。"""
        # ① 读缓存（按日，幂等）
        if self._date is not None:
            cached = self.cache.load("blocks", self._date)
            if cached is not None:
                return cached

        try:
            b = self._q.block()
        except Exception:
            b = None
        if b is None or len(b) == 0:
            out = pd.DataFrame(columns=["block_name", "block_code", "code"])
            self._save_blocks(out)
            return out

        # ② 列探测（不写死列名，兼容形态 A / B）
        cols_lower = [str(c).lower() for c in b.columns]
        name_col = next(
            (c for c, lc in zip(b.columns, cols_lower) if "name" in lc and "block" in lc),
            None,
        )
        type_col = next((c for c, lc in zip(b.columns, cols_lower) if lc == "block_type"), None)
        code_col = next((c for c, lc in zip(b.columns, cols_lower) if lc == "code"), None)
        # 板块自身代码列（可有可无）：code_index / block_code
        block_code_col = next(
            (c for c, lc in zip(b.columns, cols_lower) if "code_index" in lc or "block_code" in lc),
            None,
        )
        if name_col is None or code_col is None:
            # 列结构异常：无法定位板块名/成分股 → 退化为无板块，不编造
            out = pd.DataFrame(columns=["block_name", "block_code", "code"])
            self._save_blocks(out)
            return out

        # ③ 选板块子集：block_type==2 优先；否则用 20 干净板块名白名单（= type2 集合）
        if type_col is not None:
            sub = b[b[type_col] == 2].copy()
        else:
            allow = {
                "ST板块", "一带一路", "上海自贸", "上证50", "专精特新", "中证A100",
                "中证A50", "创业板指", "北证50", "沪深300", "海南自贸", "海峡西岸",
                "深证50", "深证成指", "科创50", "粤港澳", "精选指数", "融资融券",
                "通达信88", "雄安新区",
            }
            sub = b[b[name_col].astype(str).str.strip().isin(allow)].copy()

        if len(sub) == 0:
            out = pd.DataFrame(columns=["block_name", "block_code", "code"])
            self._save_blocks(out)
            return out

        # ④ 归一化输出列
        names = sub[name_col].astype(str).str.strip().values
        codes = sub[code_col].astype(str).str.zfill(6).str.strip().values
        block_codes = (
            sub[block_code_col].astype(str).str.strip().values
            if block_code_col is not None
            else [""] * len(sub)
        )
        out = pd.DataFrame(
            {"block_name": names, "block_code": block_codes, "code": codes}
        )
        # 仅保留合法 6 位成分股代码（过滤指数/乱码代码）
        out = out[out["code"].str.fullmatch(r"\d{6}")].reset_index(drop=True)
        self._save_blocks(out)
        return out

    def _save_blocks(self, df: pd.DataFrame) -> None:
        """按日缓存板块成员表（DESIGN 8.7 幂等）；_date 未就绪时跳过。"""
        if self._date is not None:
            try:
                self.cache.save(df, "blocks", self._date)
            except Exception:
                pass

    # ----------------------------------------------- 市场指数（上证/深证）
    def index_quotes(self, codes=None) -> pd.DataFrame:
        """上证/深证指数现价与涨跌幅（用 index_bars 取近 2 根，可靠）。"""
        codes = codes or [_BENCH_INDEX, _SHENZHEN_INDEX]
        rows = []
        for code in codes:
            b = self._q.index_bars(str(code), frequency=9, offset=2)
            if b is None or len(b) == 0:
                continue
            b = b.reset_index(drop=True)
            last = b.iloc[-1]
            prev = b.iloc[-2] if len(b) >= 2 else None
            price = float(last["close"])
            last_close = float(prev["close"]) if prev is not None else None
            pct = (price - last_close) / last_close * 100.0 if last_close else None
            amount = float(last["amount"]) if "amount" in last else None
            rows.append(
                {
                    "code": str(code).zfill(6),
                    "price": price,
                    "last_close": last_close,
                    "pct": pct,
                    "amount": amount,
                }
            )
        return pd.DataFrame(rows)

