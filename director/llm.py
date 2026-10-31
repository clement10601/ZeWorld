"""LLM 用戶端。兩種後端，設定只從環境變數讀，金鑰不寫進任何檔案。

Claude API（官方 anthropic SDK，pip install anthropic）：
  ZEWORLD_ANTHROPIC_API_KEY  Claude API 金鑰。刻意不讀 ANTHROPIC_API_KEY，免得誤用其他工具的金鑰
  ZEWORLD_DIRECTOR_MODEL     省略時 claude-opus-5-5
  ZEWORLD_DIALOGUE_MODEL     省略時 claude-sonnet-5-5（實測 Haiku 常混簡體、把思考寫進台詞）

OpenAI 相容端點（LiteLLM、vLLM、Ollama），沒設 Claude 金鑰時使用：
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


def llm_from_env():
    """有 Claude 金鑰就用 Claude API，否則用 OpenAI 相容端點；都沒設定回傳 None。"""
    return AnthropicLLM.from_env() or OpenAICompatLLM.from_env()


class AnthropicLLM:
    DEFAULT_MODELS = {"director": "claude-opus-5-5", "dialogue": "claude-sonnet-5-5"}
    # 導演要想清楚，台詞要快
    EFFORT = {"director": "medium", "dialogue": "low"}
    # 安全分類器拒答時由伺服器改用其他模型重跑；不在清單裡的模型（如 Haiku）沒有伺服器端備援
    FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")

    def __init__(self, api_key, models, timeout=180):
        import anthropic
        self._anthropic = anthropic
        # base_url 寫死官方端點，不受 ANTHROPIC_BASE_URL 影響
        self.client = anthropic.Anthropic(api_key=api_key, base_url="https://api.anthropic.com", timeout=timeout)
        self.models = models
        self.last_usage = None  # 最近一次呼叫的 token 用量，給 bench 算成本

    @classmethod
    def from_env(cls):
        key = os.environ.get("ZEWORLD_ANTHROPIC_API_KEY")
        if not key:
            return None
        models = {"director": os.environ.get("ZEWORLD_DIRECTOR_MODEL", cls.DEFAULT_MODELS["director"]),
                  "dialogue": os.environ.get("ZEWORLD_DIALOGUE_MODEL", cls.DEFAULT_MODELS["dialogue"])}
        return cls(key, models)

    def model_for(self, role):
        return self.models[role]

    def chat(self, messages, role="director", temperature=None, max_tokens=16000):
        """messages 是 OpenAI 格式；開頭的 system 訊息轉成 system 參數。temperature 新模型不收，忽略。"""
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
        model = self.models[role]
        # 提示快取：系統提示（規則、素材目錄、schema）與範例包每次都一樣，標成快取點，重複呼叫只算快取讀取價
        if role == "director" and len(convo) > 1:
            convo[-2]["content"] = [{"type": "text", "text": convo[-2]["content"], "cache_control": {"type": "ephemeral"}}]
        kwargs = dict(model=model, max_tokens=max(max_tokens, 2000), messages=convo,
                      output_config={"effort": self.EFFORT[role]})
        if system:
            kwargs["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        a = self._anthropic
        try:
            if model in self.FALLBACK_MODELS:
                resp = self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                        fallbacks="default", **kwargs)
            else:
                resp = self.client.messages.create(**kwargs)
        except a.APIStatusError as e:
            raise LLMError(f"Claude API HTTP {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise LLMError(f"Claude API unreachable: {e}") from e
        u = getattr(resp, "usage", None)
        self.last_usage = u and {"input": u.input_tokens, "output": u.output_tokens,
                           "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                           "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
                           "model": getattr(resp, "model", model)}
        if resp.stop_reason == "refusal":
            raise LLMError(f"Claude declined: {getattr(resp, 'stop_details', None)}")
        if resp.stop_reason == "max_tokens":
            raise LLMError("Claude reply hit max_tokens")
        return "".join(b.text for b in resp.content if b.type == "text")


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
