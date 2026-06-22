import logging
import time

import httpx

from ai_learning.core.config import get_settings

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def complete(self, prompt: str) -> str:
        if not self.settings.llm_api_key:
            raise ValueError("AI_LEARNING_LLM_API_KEY is required for real LLM answers.")

        url = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
        start = time.perf_counter()
        if self.settings.rag_debug_logs:
            logger.info(
                "LLM 调用开始 | base_url=%s model=%s prompt_length=%d",
                self.settings.llm_base_url,
                self.settings.llm_model,
                len(prompt),
            )
        try:
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                json={
                    "model": self.settings.llm_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
                timeout=30,
            )
            response.raise_for_status()
            elapsed_ms = int((time.perf_counter() - start) * 1000)
        except httpx.HTTPStatusError as exc:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            if self.settings.rag_debug_logs:
                logger.warning(
                    "LLM 调用失败 | status_code=%d elapsed_ms=%d",
                    exc.response.status_code,
                    elapsed_ms,
                )
            raise
        except httpx.HTTPError:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            if self.settings.rag_debug_logs:
                logger.warning(
                    "LLM 调用失败 | error_type=network elapsed_ms=%d",
                    elapsed_ms,
                )
            raise
        payload = response.json()
        answer = payload["choices"][0]["message"]["content"]
        if self.settings.rag_debug_logs:
            logger.info(
                "LLM 调用成功 | answer_length=%d elapsed_ms=%d",
                len(answer),
                elapsed_ms,
            )
        return answer
