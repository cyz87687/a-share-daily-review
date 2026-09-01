"""板块双轨分组与强度排名（DESIGN Q1 / 0.7）。

双轨（DESIGN Q1 已锁定）：
① **P0 板块维度**：使用 ``block_type==2`` 的 20 个板块（含 ST板块），名称直接取自
   block（干净可用）。对每个板块取其成分股中属于候选池的部分，算流通市值加权涨幅
   ``pct_weighted``、成交额及排名，输出 Top K。
② **行业维度（可插拔增强）**：经本地映射表（CSV 或 YAML）将 ``industry`` 编码映射为
   行业名 + 分类；文件缺失或编码未命中时该字段为 ``"N/A"``（渲染层显示 N/A），绝不编造
   ``TDX#{code}``。行业维度由本模块 ``build_sectors`` 经 ``sector_map`` 就地写入
   ``candidates["industry_name"]``（缺省 "N/A"），供 universe/snapshot/payload 使用。

零幻觉：成分股取不到市值/涨幅 → 加权值退化为 None 并排末位；某板块在候选池无成员
（如精选指数含指数代码）→ pct_weighted=None，不计入 Top K。

``blocks`` 入参列约定：``block_name``（板块名）、``block_code``（板块自身代码）、``code``（成分股 6 位）。
"""

from pathlib import Path

import pandas as pd
import yaml

from src.adr.types import SectorStat


def load_sector_map(path: str) -> dict | None:
    """读取 industry 编码→{name, category} 映射。

    支持 CSV（列 ``industry_code,name,category``）或 YAML（``"37": "酿酒"`` 或
    ``"37": {name: 酿酒, category: 食品饮料}``）。文件缺失/为空 → 返回 ``None``
    （上层行业维度整体 N/A）。"""
    p = Path(path)
    if not p.exists():
        return None
    try:
        if p.suffix.lower() == ".csv":
            df = pd.read_csv(p)
            col_code = "industry_code" if "industry_code" in df.columns else "code"
            col_name = (
                "industry_name" if "industry_name" in df.columns
                else ("name" if "name" in df.columns else None)
            )
            m: dict = {}
            for _, r in df.iterrows():
                c = str(r.get(col_code) or "")
                if not c or c.lower() == "nan":
                    continue
                raw_name = r.get(col_name) if col_name is not None else None
                m[str(c)] = {
                    "name": str(raw_name) if pd.notna(raw_name) else "",
                    "category": str(r.get("category")) if pd.notna(r.get("category")) else None,
                }
            return m or None
        # YAML
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        out: dict = {}
        for k, v in data.items():
            if not v:
                continue
            if isinstance(v, dict):
                out[str(k)] = {
                    "name": str(v.get("name", "")),
                    "category": v.get("category"),
                }
            else:
                out[str(k)] = {"name": str(v), "category": None}
        return out or None
    except Exception:
        return None


def build_sectors(candidates: pd.DataFrame, cfg, blocks: pd.DataFrame,
                  sector_map: dict | None = None) -> dict[str, SectorStat]:
    """双轨板块：返回 ``{block_name: SectorStat}``（含 rank）。

    ① **板块维度（P0）**：用 ``blocks``（block_type==2 的 20 干净板块）对候选池分组，
       算流通市值加权涨幅 ``pct_weighted``、成交额、Top K 排名；板块名取自 block（干净名）。
    ② **行业维度（可插拔）**：用 ``sector_map``（industry_code→{name,category}）将候选池
       的 ``industry_code`` 映射为 ``industry_name``，就地写入 ``candidates``（供下游
       snapshot/payload 使用）；编码缺失或未命中 → ``"N/A"``，绝不编造。

    Args:
        candidates: 候选池 DataFrame（含 code/float_mv/pct/amount_yi/industry_code，code 已 zfill 6）。
        cfg: Config（用 cfg.K 控制 Top K 排名口径，仅影响排名展示）。
        blocks: ``block_type==2`` 板块成员表（block_name, block_code, code）。可空。
        sector_map: 行业映射（可插拔；None / 文件缺失 → 行业维度整体 N/A）。

    返回：每个 block_type==2 板块一个 SectorStat。候选池无成员的板块 pct_weighted=None，
    排末位（不计入 Top K）。"""
    result: dict = {}
    if candidates is None or len(candidates) == 0:
        return result

    # ---- ② 行业维度（可插拔）：industry_code → industry_name，就地写入 candidates ----
    _apply_industry(candidates, sector_map)

    cand_codes = set(candidates["code"].astype(str).str.zfill(6))
    cand_idx = candidates.set_index(candidates["code"].astype(str).str.zfill(6))

    if blocks is not None and len(blocks):
        for bname, g in blocks.groupby("block_name"):
            key = str(bname)
            members = [str(c).zfill(6) for c in g["code"] if str(c).zfill(6) in cand_codes]
            if not members:
                # 该板块在候选池无成员（如精选指数成分多为指数代码）→ 不计入排名
                result[key] = SectorStat(
                    block_name=key, block_rank=0,
                    pct_weighted=None, rank=0, amount_yi=None, amount_chg=None,
                    member_count=0, members=[],
                )
                continue
            sub = cand_idx.loc[members]
            fmv = sub["float_mv"]
            valid = sub[fmv.notna() & sub["pct"].notna()]
            if len(valid) and valid["float_mv"].sum() > 0:
                pct_w = float((valid["pct"] * valid["float_mv"]).sum() / valid["float_mv"].sum())
            elif sub["pct"].notna().any():
                pct_w = float(sub["pct"].mean())
            else:
                pct_w = None
            amount_yi = float(sub["amount_yi"].sum()) if sub["amount_yi"].notna().any() else None
            result[key] = SectorStat(
                block_name=key, block_rank=0,
                pct_weighted=pct_w, rank=0, amount_yi=amount_yi, amount_chg=None,
                member_count=int(len(members)), members=members,
            )

    # 排名：有加权涨幅者按涨幅降序；无者（候选池无成员/无数据）排末位
    ranked = sorted([s for s in result.values() if s.pct_weighted is not None],
                    key=lambda s: s.pct_weighted, reverse=True)
    for i, s in enumerate(ranked, 1):
        s.rank = i
        s.block_rank = i
    for s in result.values():
        if s.pct_weighted is None:
            s.rank = len(ranked) + 1
            s.block_rank = len(ranked) + 1
    return result


def _apply_industry(candidates: pd.DataFrame, sector_map: dict | None) -> None:
    """把 ``industry_code`` 映射为 ``industry_name`` 就地写入 candidates（行业维度，可插拔）。

    编码缺失 / 映射表 None / 未命中 → ``"N/A"``（零幻觉：绝不编造行业名）。下游
    ``universe.snapshot_from_row`` / ``screener`` / payload 会自动读到行业维度。"""
    if "industry_code" not in candidates.columns:
        candidates["industry_name"] = "N/A"
        return

    ic = candidates["industry_code"]

    def _name(v) -> str:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "N/A"
        s = str(v).strip()
        if not s or s.lower() in ("nan", "none"):
            return "N/A"
        if sector_map and s in sector_map and sector_map[s].get("name"):
            nm = str(sector_map[s].get("name", "")).strip()
            return nm if nm else "N/A"
        return "N/A"

    candidates["industry_name"] = ic.apply(_name)
