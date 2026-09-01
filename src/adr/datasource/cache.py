"""原始行情本地缓存（DESIGN 8.7 幂等）。

原始行情首次拉取后缓存到 ``data/cache/{date}/``，重跑直接读缓存 → 不重复打网、结果一致。
使用 pandas pickle（纯 stdlib，无额外依赖）；缓存失败不影响主流程。
"""

from pathlib import Path

import pandas as pd


class Cache:
    """按日期 + 类型 + 代码缓存原始行情。"""

    def __init__(self, cfg):
        self.root = Path(cfg.data_dir) / "cache"

    def _path(self, date, kind: str, code: str = None) -> Path:
        d = self.root / str(date)
        d.mkdir(parents=True, exist_ok=True)
        if kind == "snapshot":
            return d / "snapshot.pkl"
        if kind == "bars":
            return d / f"bars_{code}.pkl"
        if kind == "xdxr":
            return d / f"xdxr_{code}.pkl"
        if kind == "finance":
            return d / f"finance_{code}.pkl"
        if kind == "blocks":
            return d / "blocks.pkl"
        raise ValueError(f"未知缓存类型: {kind}")

    def load(self, kind: str, date, code: str = None):
        """读取缓存 DataFrame；不存在或读取失败返回 None。"""
        p = self._path(date, kind, code)
        if p.exists():
            try:
                return pd.read_pickle(p)
            except Exception:
                return None
        return None

    def save(self, df: pd.DataFrame, kind: str, date, code: str = None) -> None:
        """写入缓存；失败静默忽略。"""
        try:
            df.to_pickle(self._path(date, kind, code))
        except Exception:
            pass
