"""AI 输出 JSON 解析（DESIGN / PRD P0-10）。

容忍 ```json 围栏与轻微格式容错；解析失败降级为 ``None``（调用方保留原文，不丢内容）。
"""

import json
import re


def parse_ai_json(content):
    """解析 AI 输出为 dict；失败返回 None（降级保留原文）。"""
    if content is None:
        return None
    text = content.strip()

    # 去掉 ```json ... ``` 围栏
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()

    # 直接解析
    try:
        return json.loads(text)
    except Exception:
        pass

    # 提取首个 { ... } 或 [ ... ]
    for pat in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pat, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                continue
    return None
