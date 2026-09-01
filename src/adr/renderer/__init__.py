"""渲染层（原生 HTML/CSS/JS，零外链、零构建）。"""

from src.adr.renderer.index_page import render_index
from src.adr.renderer.review_page import render_review

__all__ = ["render_review", "render_index"]
