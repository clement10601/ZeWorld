"""LLM 用戶端。走 OpenAI 相容的 /chat/completions，LiteLLM、vLLM、Ollama 都能接。

設定只從環境變數讀，金鑰不寫進任何檔案：
  ZEWORLD_LLM_BASE_URL   例如 http://ubt015:4000/v1
  ZEWORLD_LLM_API_KEY    可省略（本機模型通常不需要）
  ZEWORLD_DIRECTOR_MODEL 產內容包用的模型
  ZEWORLD_DIALOGUE_MODEL 即時台詞用的模型，省略時同導演模型
"""
import json
import os
import urllib.request


class LLMError(RuntimeError):
    pass


class OpenAICompatLLM:
    def __init__(self, base_url, models, api_key=None, timeout=90):
        self.base_url = base_url.rstrip("/")
        self.models = models
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_env(cls):
        base = os.environ.get("ZEWORLD_LLM_BASE_URL")
        director = os.environ.get("ZEWORLD_DIRECTOR_MODEL")
        if not base or not director:
            return None
        models = {"director": director, "dialogue": os.environ.get("ZEWORLD_DIALOGUE_MODEL", director)}
        return cls(base, models, os.environ.get("ZEWORLD_LLM_API_KEY"))

    def model_for(self, role):
        return self.models[role]

    def chat(self, messages, role="director", temperature=0.7, max_tokens=4000):
        body = {"model": self.models[role], "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens}
        req = urllib.request.Request(self.base_url + "/chat/completions",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        if self.api_key:
            req.add_header("Authorization", "Bearer " + self.api_key)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"] or ""
        except Exception as e:  # 網路、HTTP 錯誤、格式不符都當成 LLM 失敗
            raise LLMError(f"{type(e).__name__}: {e}") from e


class FakeLLM:
    """測試與離線開發用。replies 依角色給：字串清單（依序取用）或 callable(messages) -> str。"""

    def __init__(self, director=(), dialogue=()):
        self.replies = {"director": director, "dialogue": dialogue}
        self.calls = []

    def model_for(self, role):
        return "fake-" + role

    def chat(self, messages, role="director", temperature=0.7, max_tokens=4000):
        self.calls.append((role, messages))
        r = self.replies[role]
        if callable(r):
            return r(messages)
        n = sum(1 for c in self.calls if c[0] == role) - 1
        if n >= len(r):
            raise LLMError(f"FakeLLM ran out of {role} replies")
        return r[n]
