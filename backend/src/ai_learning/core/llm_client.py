import httpx

from ai_learning.core.config import get_settings


class LLMClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def complete(self, prompt: str) -> str:
        if not self.settings.llm_api_key:
            raise ValueError("AI_LEARNING_LLM_API_KEY is required for real LLM answers.")

        url = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
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
        payload = response.json()
        return payload["choices"][0]["message"]["content"]
