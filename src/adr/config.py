"""配置加载与校验。

``Config`` 从 ``config.yaml`` 冻结为 dataclass；所有路径相对项目根解析，
代码中禁止硬编码绝对路径。``api_key`` 在 ``redacted()`` 中脱敏（DESIGN 8.4 / PRD P0-19）。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# 项目根（src/adr/config.py → parents[2]）；仅作兜底，实际以 config.yaml 所在目录为准
DEFAULT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class LLMConfig:
    """LLM 供应商配置（OpenAI 兼容协议）。"""

    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    api_key: str = ""
    temperature: float = 0.3
    max_tokens: int = 4096

    def is_available(self) -> bool:
        """是否已配置可用的 api_key（auto 模式联机前置条件）。"""
        return bool(self.api_key) and self.api_key.strip() != ""


@dataclass
class Config:
    """全局配置（冻结）。"""

    N: int = 500
    D: int = 20
    Q_UP: float = 1.6
    Q_LOW: float = 0.55
    P_TH: float = 2.5
    K: int = 20
    M: int = 60
    mode: str = "prepare"
    exclude_bj: bool = True
    batch_size: int = 80
    output_dir: str = "output"
    data_dir: str = "data"
    logs_dir: str = "logs"
    industry_map_path: str = "config/industry_map.yaml"
    sector_map_path: str = "data/sector_map.csv"
    llm: LLMConfig = field(default_factory=LLMConfig)
    root: Path = field(default_factory=lambda: DEFAULT_ROOT)

    @classmethod
    def load(cls, path: str) -> "Config":
        """从 YAML 加载并解析相对路径（相对 config.yaml 所在目录）。"""
        p = Path(path).resolve()
        root = p.parent
        with open(p, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        llm_raw = raw.get("llm") or {}
        llm = LLMConfig(
            provider=str(llm_raw.get("provider", "deepseek")),
            base_url=str(llm_raw.get("base_url", "https://api.deepseek.com/v1")),
            model=str(llm_raw.get("model", "deepseek-chat")),
            api_key=str(llm_raw.get("api_key", "") or ""),
            temperature=float(llm_raw.get("temperature", 0.3)),
            max_tokens=int(llm_raw.get("max_tokens", 4096)),
        )

        cfg = cls(
            N=int(raw.get("N", 500)),
            D=int(raw.get("D", 20)),
            Q_UP=float(raw.get("Q_UP", 1.6)),
            Q_LOW=float(raw.get("Q_LOW", 0.55)),
            P_TH=float(raw.get("P_TH", 2.5)),
            K=int(raw.get("K", 20)),
            M=int(raw.get("M", 60)),
            mode=str(raw.get("mode", "prepare")),
            exclude_bj=bool(raw.get("exclude_bj", True)),
            batch_size=int(raw.get("batch_size", 80)),
            output_dir=str(raw.get("output_dir", "output")),
            data_dir=str(raw.get("data_dir", "data")),
            logs_dir=str(raw.get("logs_dir", "logs")),
            industry_map_path=str(raw.get("industry_map_path", "config/industry_map.yaml")),
            sector_map_path=str(raw.get("sector_map_path", "data/sector_map.csv")),
            llm=llm,
            root=root,
        )

        def _resolve(p_str: str) -> str:
            return str((root / p_str).resolve()) if not os.path.isabs(p_str) else p_str

        cfg.output_dir = _resolve(cfg.output_dir)
        cfg.data_dir = _resolve(cfg.data_dir)
        cfg.logs_dir = _resolve(cfg.logs_dir)
        cfg.industry_map_path = _resolve(cfg.industry_map_path)
        cfg.sector_map_path = _resolve(cfg.sector_map_path)
        return cfg

    def redacted(self) -> dict:
        """返回脱敏配置（api_key 不落明文）。"""
        return {
            "N": self.N,
            "D": self.D,
            "Q_UP": self.Q_UP,
            "Q_LOW": self.Q_LOW,
            "P_TH": self.P_TH,
            "K": self.K,
            "M": self.M,
            "mode": self.mode,
            "exclude_bj": self.exclude_bj,
            "batch_size": self.batch_size,
            "output_dir": self.output_dir,
            "data_dir": self.data_dir,
            "logs_dir": self.logs_dir,
            "industry_map_path": self.industry_map_path,
            "llm": {
                "provider": self.llm.provider,
                "base_url": self.llm.base_url,
                "model": self.llm.model,
                "api_key": ("***" if self.llm.api_key else ""),
                "temperature": self.llm.temperature,
                "max_tokens": self.llm.max_tokens,
            },
        }

    def ensure_dirs(self) -> None:
        """创建输出/数据/日志目录（幂等）。"""
        for d in (
            self.output_dir,
            self.data_dir,
            self.logs_dir,
            Path(self.data_dir) / "logs",
            Path(self.data_dir) / "reviews",
            Path(self.data_dir) / "snapshots",
            Path(self.data_dir) / "cache",
        ):
            Path(d).mkdir(parents=True, exist_ok=True)
