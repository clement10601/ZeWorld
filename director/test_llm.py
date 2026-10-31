"""Claude API 後端的轉換與設定測試，不打網路。需要 pip install anthropic。"""
from types import SimpleNamespace

import pytest

pytest.importorskip("anthropic")

from director.llm import AnthropicLLM, LLMError, OpenAICompatLLM, llm_from_env  # noqa: E402


class FakeMessages:
    def __init__(self, stop_reason="end_turn"):
        self.calls = []
        self.stop_reason = stop_reason

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason=self.stop_reason, stop_details=None,
                               content=[SimpleNamespace(type="thinking", thinking=""),
                                        SimpleNamespace(type="text", text='{"ok": true}')])


def make(stop_reason="end_turn"):
    llm = AnthropicLLM("test-key", dict(AnthropicLLM.DEFAULT_MODELS))
    llm.client = SimpleNamespace(messages=FakeMessages(stop_reason),
                                 beta=SimpleNamespace(messages=FakeMessages(stop_reason)))
    return llm


MSGS = [{"role": "system", "content": "rules"}, {"role": "user", "content": "example"},
        {"role": "assistant", "content": "ok"}, {"role": "user", "content": "go"}]


def test_director_uses_opus_with_server_fallback():
    llm = make()
    assert llm.chat(MSGS, role="director", temperature=0.7) == '{"ok": true}'
    kw = llm.client.beta.messages.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["system"] == [{"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}]
    assert [m["role"] for m in kw["messages"]] == ["user", "assistant", "user"]
    # 範例包之後設快取點，最後一則（玩家狀態）每次不同不快取
    assert kw["messages"][1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["messages"][2]["content"] == "go"
    assert MSGS[2]["content"] == "ok"  # 不改呼叫端的訊息
    assert "temperature" not in kw and kw["output_config"] == {"effort": "medium"}


def test_dialogue_uses_sonnet_low_effort():
    llm = make()
    llm.chat(MSGS, role="dialogue", max_tokens=200)
    kw = llm.client.beta.messages.calls[0]
    assert kw["model"] == "claude-sonnet-5-5"
    assert kw["messages"][1]["content"] == "ok"  # 台詞不設快取點
    assert kw["max_tokens"] >= 2000  # 思考也算在 max_tokens 裡
    assert kw["output_config"] == {"effort": "low"}


def test_model_without_server_fallback():
    llm = AnthropicLLM("test-key", {"director": "claude-opus-5-5", "dialogue": "claude-haiku-5-5"})
    llm.client = SimpleNamespace(messages=FakeMessages(), beta=SimpleNamespace(messages=FakeMessages()))
    llm.chat(MSGS, role="dialogue")
    assert "fallbacks" not in llm.client.messages.calls[0] and not llm.client.beta.messages.calls


def test_refusal_and_truncation_raise():
    with pytest.raises(LLMError):
        make("refusal").chat(MSGS)
    with pytest.raises(LLMError):
        make("max_tokens").chat(MSGS)


def test_env_selection(monkeypatch):
    for k in ("ZEWORLD_ANTHROPIC_API_KEY", "ZEWORLD_LLM_BASE_URL", "ZEWORLD_DIRECTOR_MODEL", "ZEWORLD_DIALOGUE_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "someone-elses-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://elsewhere.invalid")
    assert llm_from_env() is None  # 不會撿別人的金鑰
    monkeypatch.setenv("ZEWORLD_ANTHROPIC_API_KEY", "k")
    llm = llm_from_env()
    assert isinstance(llm, AnthropicLLM) and str(llm.client.base_url).startswith("https://api.anthropic.com")
    monkeypatch.setenv("ZEWORLD_DIRECTOR_MODEL", "claude-sonnet-5-5")
    assert llm_from_env().model_for("director") == "claude-sonnet-5-5"
    monkeypatch.delenv("ZEWORLD_ANTHROPIC_API_KEY")
    monkeypatch.setenv("ZEWORLD_LLM_BASE_URL", "http://x/v1")
    assert isinstance(llm_from_env(), OpenAICompatLLM)
