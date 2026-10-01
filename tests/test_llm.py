"""Tests for the Groq client fallback (llm.py) — troca automática de modelo em
429 (quota esgotada) e 404 (modelo descontinuado). Sem rede: `_groq_once` é
monkeypatched."""

import urllib.error

import src.llm as llm
def _http(code):
    return urllib.error.HTTPError("https://groq", code, "err", {}, None)


# ── _fallback_model ───────────────────────────────────────────────────────────

class TestFallbackModel:
    def test_returns_different_model(self):
        assert llm._fallback_model("llama-3.3-70b-versatile") == "llama-3.1-8b-instant"
        assert llm._fallback_model("llama-3.1-8b-instant") == "llama-3.3-70b-versatile"

    def test_unknown_current_still_returns_first(self):
        assert llm._fallback_model("modelo-inexistente") == "llama-3.1-8b-instant"

    def test_models_list_excludes_current(self):
        lst = llm._fallback_models("llama-3.1-8b-instant")
        assert "llama-3.1-8b-instant" not in lst and lst[0] == "llama-3.3-70b-versatile"


# ── _swap_model: re-deriva o reasoning_effort para o novo modelo ──────────────

class TestSwapModel:
    def test_drops_reasoning_effort_for_non_reasoning_model(self):
        # qwen (reasoning, 'none') → 8B (não-reasoning) deve PERDER o reasoning_effort
        out = llm._swap_model({"model": "qwen/qwen3-32b", "reasoning_effort": "none",
                               "messages": []}, "llama-3.1-8b-instant")
        assert out["model"] == "llama-3.1-8b-instant"
        assert "reasoning_effort" not in out

    def test_does_not_mutate_original(self):
        orig = {"model": "qwen/qwen3-32b", "reasoning_effort": "none"}
        llm._swap_model(orig, "llama-3.3-70b-versatile")
        assert orig["model"] == "qwen/qwen3-32b"   # cópia, não mutação


# ── _groq_complete retry ──────────────────────────────────────────────────────

class TestGroqCompleteFallback:
    def test_retries_on_429_with_other_model(self, monkeypatch):
        calls = []

        def fake_once(payload):
            calls.append(payload["model"])
            if len(calls) == 1:
                raise _http(429)
            return {"choices": [{"message": {"content": "ok"}}]}

        monkeypatch.setattr(llm, "_groq_once", fake_once)
        out = llm._groq_complete({"model": "llama-3.3-70b-versatile", "messages": []})
        assert out["choices"][0]["message"]["content"] == "ok"
        assert calls == ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]  # trocou

    def test_retries_on_404(self, monkeypatch):
        calls = []

        def fake_once(payload):
            calls.append(payload["model"])
            if len(calls) == 1:
                raise _http(404)
            return {"choices": [{"message": {"content": "ok"}}]}

        monkeypatch.setattr(llm, "_groq_once", fake_once)
        llm._groq_complete({"model": "qwen/qwen3-32b", "messages": []})
        assert len(calls) == 2 and calls[1] != "qwen/qwen3-32b"

    def test_retries_on_413_request_too_large_for_model(self, monkeypatch):
        # os limites de tokens/minuto do Groq variam por modelo (prompt + max_tokens)
        calls = []

        def fake_once(payload):
            calls.append(payload["model"])
            if len(calls) == 1:
                raise _http(413)
            return {"choices": [{"message": {"content": "ok"}}]}

        monkeypatch.setattr(llm, "_groq_once", fake_once)
        llm._groq_complete({"model": "qwen/qwen3-32b", "messages": []})
        assert len(calls) == 2

    def test_does_not_retry_on_other_errors(self, monkeypatch):
        calls = []

        def fake_once(payload):
            calls.append(payload["model"])
            raise _http(400)   # 400 não é fallback-able

        monkeypatch.setattr(llm, "_groq_once", fake_once)
        try:
            llm._groq_complete({"model": "llama-3.3-70b-versatile", "messages": []})
            assert False, "devia ter lançado"
        except urllib.error.HTTPError as e:
            assert e.code == 400
        assert len(calls) == 1   # uma só tentativa, sem fallback

    def test_success_first_try_no_retry(self, monkeypatch):
        calls = []
        monkeypatch.setattr(llm, "_groq_once",
                            lambda p: calls.append(p["model"]) or {"choices": []})
        llm._groq_complete({"model": "llama-3.3-70b-versatile", "messages": []})
        assert len(calls) == 1

    def test_iterates_whole_chain(self, monkeypatch):
        # 429 no 70B e no 8B; sucesso no 3.º (gpt-oss) → percorre a cadeia toda
        seen = []
        def fake_once(payload):
            seen.append(payload["model"])
            if len(seen) < 3:
                raise _http(429)
            return {"choices": [{"message": {"content": "ok"}}]}
        monkeypatch.setattr(llm, "_groq_once", fake_once)
        llm._groq_complete({"model": "llama-3.3-70b-versatile", "messages": []})
        assert seen == ["llama-3.3-70b-versatile", "llama-3.1-8b-instant",
                        "openai/gpt-oss-120b"]

    def test_fallback_drops_obsolete_reasoning_effort(self, monkeypatch):
        # qwen com reasoning_effort='none' → o payload do fallback NÃO o pode levar
        seen = []
        def fake_once(payload):
            seen.append(dict(payload))
            if len(seen) == 1:
                raise _http(429)
            return {"choices": []}
        monkeypatch.setattr(llm, "_groq_once", fake_once)
        llm._groq_complete({"model": "qwen/qwen3-32b",
                            "reasoning_effort": "none", "messages": []})
        assert seen[0]["reasoning_effort"] == "none"        # 1.ª tentativa: qwen
        assert "reasoning_effort" not in seen[1]            # fallback 8B: sem ele
        assert seen[1]["model"] == "llama-3.1-8b-instant"

    def test_all_models_fail_raises_last(self, monkeypatch):
        monkeypatch.setattr(llm, "_groq_once",
                            lambda p: (_ for _ in ()).throw(_http(429)))
        try:
            llm._groq_complete({"model": "llama-3.3-70b-versatile", "messages": []})
            assert False, "devia ter lançado"
        except urllib.error.HTTPError as e:
            assert e.code == 429
