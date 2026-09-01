"""渲染每日单页 review-{date}.html（DESIGN / PRQ P0-13 / 8.6）。

CSS/JS 全内联，零外链，双击即开；取色统一从 assets/palette.py 注入（禁止写死颜色）。
图表用原生 SVG 手绘（20 日 K 线 sparkline），关闭 JS 时正文仍静态可读。
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from assets.palette import PALETTE

_TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "templates"


def make_sparkline(bars20: list, palette: dict = None) -> str:
    """手绘 20 日 K 线 sparkline（SVG）。涨红跌绿。失败返回空串。"""
    pal = palette or PALETTE
    if not bars20:
        return ""
    try:
        highs = [float(b["high"]) for b in bars20 if b.get("high") is not None]
        lows = [float(b["low"]) for b in bars20 if b.get("low") is not None]
        if not highs or not lows:
            return ""
        mx, mn = max(highs), min(lows)
        rng = (mx - mn) or 1.0
        n = len(bars20)
        W, H, pad = 132, 40, 3

        def _y(v):
            return pad + (mx - float(v)) / rng * (H - 2 * pad)

        cw = (W - 2 * pad) / max(n, 1)
        parts = [f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none">']
        for i, b in enumerate(bars20):
            o = float(b["open"])
            c = float(b["close"])
            h = float(b["high"])
            l = float(b["low"])
            x = pad + i * cw + cw / 2
            col = pal["UP"] if c >= o else pal["DOWN"]
            parts.append(f'<line x1="{x:.1f}" y1="{_y(h):.1f}" x2="{x:.1f}" y2="{_y(l):.1f}" stroke="{col}" stroke-width="0.6"/>')
            ytop = _y(max(o, c))
            ybot = _y(min(o, c))
            bh = max(ybot - ytop, 0.6)
            bw = max(cw * 0.6, 0.6)
            parts.append(f'<rect x="{x - bw / 2:.1f}" y="{ytop:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="{col}"/>')
        parts.append("</svg>")
        return "".join(parts)
    except Exception:
        return ""


def render_review(date: str, payload: dict, out_path: str) -> None:
    """渲染单页 HTML 并写入 out_path。"""
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=False)
    tpl = env.get_template("review.html.j2")
    html = tpl.render(**payload)
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
