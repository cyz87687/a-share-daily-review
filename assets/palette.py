"""A股配色单一真源（涨红跌绿，与欧美相反）。

所有前端取色必须从本模块注入，禁止在模板里写死颜色值（DESIGN 8.6 / PRD P0-15）。
"""

# 主色：涨 / 放量 / 兑现 / 主线
UP = "#E1251B"
UP_BG = "#FFF1F0"

# 主色：跌 / 缩量 / 证伪 / 弱势
DOWN = "#17A673"
DOWN_BG = "#E8F8F2"

# 平盘 / 未触发 / N/A
FLAT = "#8C8C8C"

# 警示：证伪条件 / 赔率不足 / 质量门告警
WARN = "#FA8C16"

# 文字与中性色
TEXT = "#1F2329"
TEXT_SUB = "#646A73"
BG = "#FFFFFF"
CARD = "#FAFAFA"
BORDER = "#E5E6EB"

# 供 Jinja2 模板统一注入的字典（以 CSS 自定义属性形式落地）
PALETTE = {
    "UP": UP,
    "UP_BG": UP_BG,
    "DOWN": DOWN,
    "DOWN_BG": DOWN_BG,
    "FLAT": FLAT,
    "WARN": WARN,
    "TEXT": TEXT,
    "TEXT_SUB": TEXT_SUB,
    "BG": BG,
    "CARD": CARD,
    "BORDER": BORDER,
}
