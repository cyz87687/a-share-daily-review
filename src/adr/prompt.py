"""提示词装配（DESIGN / PRD P0-8）。

以 ``prompt_src/a-share-daily-review-prompt.md`` 为模板，注入 N/D/Q_UP/Q_LOW/P_TH/K/M 与 date，
拼接数据包 → ``output/{date}/prompt.txt``。prepare 模式在此停止并打印人工操作指引。
"""

import json
from pathlib import Path


def _inject_params(tpl: str, cfg, run_date: str) -> str:
    """将模板中的参数占位符替换为实际值。"""
    repl = {
        "{YYYY-MM-DD}": run_date,
        "{D=20}": str(cfg.D),
        "{N=500}": str(cfg.N),
        "{Q_UP=1.6}": str(cfg.Q_UP),
        "{Q_LOW=0.55}": str(cfg.Q_LOW),
        "{P_TH=2.5%}": f"{cfg.P_TH}%",
        "{K=20}": str(cfg.K),
        "{M=40-80}": str(cfg.M),
    }
    text = tpl
    for k, v in repl.items():
        text = text.replace(k, v)
    return text


def build_prompt(datapack: dict, cfg, run_date: str, template_path: str = None) -> str:
    """装配提示词并写入 output/{date}/prompt.txt，返回文件路径。"""
    root = Path(cfg.root)
    template_path = template_path or (root / "prompt_src" / "a-share-daily-review-prompt.md")
    tpl = Path(template_path).read_text(encoding="utf-8")
    text = _inject_params(tpl, cfg, run_date)

    payload = (
        "\n\n# ================= 候选数据包（机器筛选结果，供 AI 精复盘）=================\n"
        + json.dumps(datapack, ensure_ascii=False, indent=2, default=str)
    )
    out = text + payload

    out_dir = Path(cfg.output_dir) / run_date
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "prompt.txt"
    p.write_text(out, encoding="utf-8")
    return str(p)
