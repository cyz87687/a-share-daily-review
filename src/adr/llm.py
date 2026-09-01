"""LLM 调用（DeepSeek OpenAI 兼容）+ 分批 5 只/批 + 断点续跑 + 失败指数退避 ≤3 + AI_FAILED 降级。

DESIGN Q6 / PRD P0-9：
- provider/model/api_key 可配；OpenAI 兼容协议，低成本切换通义/智谱。
- 每批 5 只 + 市场总结单独一批；已完成批次落盘 ``data/reviews/{date}.json``（按 batch_id），
  重跑跳过；``--force`` 强制重跑。
- 单批失败 → 标记 AI_FAILED 待人工补充，不中断整轮。
降级方案：openai SDK 不可用时，保持同名函数签名改用 requests 直连 /chat/completions。
"""

import json
import time
from pathlib import Path

from src.adr.parser import parse_ai_json


class LLMUnavailableError(Exception):
    """LLM 不可用（无 key / 网络/鉴权失败）。上层应降级为 AI_FAILED，不中断。"""


class LLMClient:
    """DeepSeek（OpenAI 兼容）客户端。"""

    def __init__(self, cfg, run_date: str):
        self.cfg = cfg
        self.llm = cfg.llm
        self.date = run_date
        self.force = False
        self.reviews_path = Path(cfg.data_dir) / "reviews" / f"{run_date}.json"
        self.reviews_path.parent.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------- 断点续跑
    def _load_all(self) -> dict:
        if self.reviews_path.exists():
            try:
                return json.loads(self.reviews_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_all(self, data: dict) -> None:
        self.reviews_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _check_key(self) -> None:
        if not self.llm.is_available():
            raise LLMUnavailableError("未配置 llm.api_key，auto 模式无法联机（prepare 模式无需 key）")

    # ------------------------------------------------------------- 单次调用
    def chat(self, prompt: str, batch_id: str):
        """调用 LLM；已完成批次直接返回（断点续跑）。返回 (content, meta)。"""
        existing = self._load_all()
        if batch_id in existing and not self.force:
            rec = existing[batch_id]
            return rec.get("content"), {"skipped": True, "status": rec.get("status")}
        self._check_key()
        content = self._call_once(prompt)
        return content, {"attempt": "live"}

    def _call_once(self, prompt: str) -> str:
        last_err = None
        for attempt in range(3):
            try:
                return self._do_openai(prompt)
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(2 ** attempt)  # 指数退避
        raise LLMUnavailableError(f"LLM 调用失败（重试 3 次）：{last_err}")

    def _do_openai(self, prompt: str) -> str:
        try:
            from openai import OpenAI
        except ImportError:
            return self._do_requests(prompt)
        client = OpenAI(api_key=self.llm.api_key, base_url=self.llm.base_url)
        resp = client.chat.completions.create(
            model=self.llm.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.llm.temperature,
            max_tokens=self.llm.max_tokens,
        )
        return resp.choices[0].message.content

    def _do_requests(self, prompt: str) -> str:
        import requests

        url = self.llm.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self.llm.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.llm.temperature,
            "max_tokens": self.llm.max_tokens,
        }
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {self.llm.api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    # ------------------------------------------------------------- 分批执行
    def run_batches(self, prompts: list, force: bool = False) -> list:
        """prompts: list[(batch_id, prompt)]。返回每批结果 list[dict]。"""
        self.force = force
        all_data = self._load_all()
        results = []
        for batch_id, prompt in prompts:
            try:
                content, meta = self.chat(prompt, batch_id)
                parsed = parse_ai_json(content)
                rec = {
                    "batch_id": batch_id,
                    "content": content,
                    "parsed": parsed,
                    "status": "ok" if parsed is not None else "parsed_failed",
                    "meta": meta,
                }
            except LLMUnavailableError as e:
                rec = {
                    "batch_id": batch_id,
                    "content": None,
                    "parsed": None,
                    "status": "AI_FAILED",
                    "error": str(e),
                }
            except Exception as e:  # noqa: BLE001
                rec = {
                    "batch_id": batch_id,
                    "content": None,
                    "parsed": None,
                    "status": "AI_FAILED",
                    "error": str(e),
                }
            all_data[batch_id] = rec
            results.append(rec)
        self._save_all(all_data)
        return results
