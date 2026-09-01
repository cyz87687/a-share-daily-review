"""渲染历史归档索引页 output/index.html（DESIGN / PRD P0-14）。

按年/月分组卡片网格，每卡展示日期、主要指数涨跌、留存数、当日主线、对账命中率；
点击直达当日单页；支持前端检索（日期/代码/板块/主线关键词）。
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "templates"


def render_index(metas: list, out_path: str) -> None:
    """渲染归档索引页。``metas``：各期 run_meta 列表。"""
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=False)
    tpl = env.get_template("index.html.j2")
    html = tpl.render(metas=metas)
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
