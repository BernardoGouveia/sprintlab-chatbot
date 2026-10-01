"""
Cliente Groq (OpenAI-compatible): escolha de modelo, chamada não-streaming,
limpeza de <think> e parsing das sugestões de seguimento.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from src.config import (GROQ_API_KEY, GROQ_MODEL, GROQ_MODELS,
                    GROQ_REASONING_EFFORT, GROQ_UA, GROQ_URL)

log = logging.getLogger("sprintlab")

# Códigos que justificam tentar outro modelo: 429 (quota), 404 (descontinuado) e
# 413 (pedido maior que o limite de tokens/minuto DESSE modelo — os limites do
# Groq contam prompt + max_tokens e variam por modelo).
FALLBACK_CODES = (429, 404, 413)

# Ordem de fallback quando um modelo dá 429 (quota) ou 404 (descontinuado):
# o 8B tem o maior limite grátis; o 70B é o mais capaz. Saltar de modelo = saltar
# de "balde" de quota (os limites do Groq são por-modelo) ou para um que exista.
_FALLBACK_CHAIN = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile",
                   "openai/gpt-oss-120b"]


def _pick_model(requested):
    """(model, reasoning_effort) for an allowed request, else the env default
    (GROQ_MODEL) — also used when the UI leaves the model on "server default"."""
    if isinstance(requested, str) and requested in GROQ_MODELS:
        return requested, GROQ_MODELS[requested]
    return GROQ_MODEL, GROQ_REASONING_EFFORT


def _fallback_models(current):
    """Modelos da cadeia diferentes do atual e que existam — por ordem de tentativa."""
    return [m for m in _FALLBACK_CHAIN if m != current and m in GROQ_MODELS]


def _fallback_model(current):
    """Primeiro fallback (compat). None se não houver."""
    fb = _fallback_models(current)
    return fb[0] if fb else None


def _swap_model(payload, fb):
    """Cópia do payload com o modelo trocado e o `reasoning_effort` RE-DERIVADO
    para o novo modelo (removido se o novo não for de raciocínio). Sem isto, o
    parâmetro do modelo antigo (ex.: qwen com 'none') seria enviado a um Llama
    que o rejeita com 400 — e o próprio fallback falharia."""
    np = {**payload, "model": fb}
    np.pop("reasoning_effort", None)
    eff = GROQ_MODELS.get(fb, "")
    if eff:
        np["reasoning_effort"] = eff
    return np


def _strip_think(text: str) -> str:
    """Remove <think>...</think> blocks that reasoning models (e.g. Qwen3)
    prepend, so they never leak into a saved issue description. For PROSE only —
    JSON with code inside goes through _parse_json_obj, which leaves it intact."""
    text = text or ""
    pos = 0
    while True:
        start = text.find("<think>", pos)
        if start == -1:
            break
        end = text.find("</think>", start)
        if end == -1:
            break
        text = text[:start] + text[end + len("</think>"):]
        pos = start
    return text.strip()


def _strip_leading_think(text: str) -> str:
    """Remove only a reasoning block at the very START of a reply."""
    t = (text or "").lstrip()
    if t.startswith("<think>"):
        end = t.find("</think>")
        if end != -1:
            t = t[end + len("</think>"):]
    return t


def _groq_once(payload: dict) -> dict:
    """Uma chamada Groq não-streaming (sem fallback)."""
    req = urllib.request.Request(
        GROQ_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "User-Agent": GROQ_UA,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _groq_complete(payload: dict) -> dict:
    """Chamada Groq não-streaming com FALLBACK em 429 (quota), 404 (modelo
    descontinuado) ou 413 (acima do limite de tokens do modelo): itera a cadeia de
    modelos até um responder. O reasoning_effort é re-derivado para cada modelo
    (_swap_model)."""
    try:
        return _groq_once(payload)
    except urllib.error.HTTPError as e:
        if e.code not in FALLBACK_CODES:
            raise
        last = e
        for fb in _fallback_models(payload.get("model")):
            try:
                log.warning("groq %s em '%s' → fallback '%s'", last.code,
                            payload.get("model"), fb)
                return _groq_once(_swap_model(payload, fb))
            except urllib.error.HTTPError as e2:
                if e2.code not in FALLBACK_CODES:
                    raise
                last = e2
        raise last  # toda a cadeia falhou


# ── Follow-up suggestions (contextual chips shown after each answer) ──────────

DEFAULT_SUGGESTIONS = [
    "Qual o progresso do projeto?",
    "Issues em atraso?",
    "Quem tem mais commits?",
    "O que é este projeto?",
    "Relatório do projeto",
]


_JSON_DECODER = json.JSONDecoder(strict=False)   # tolerate raw newlines in strings


def _parse_json_obj(text):
    """Extract the first JSON object {...} from a model reply, tolerating
    markdown fences, a leading <think> block and surrounding prose. None if invalid.

    Decodes from each '{' and stops at the end of that object, so a '}' in prose
    AFTER the JSON can't break it, and nothing inside the JSON strings (e.g. code
    that mentions <think>) is ever altered."""
    t = _strip_leading_think(text)
    empty = None
    pos = t.find("{")
    while pos != -1:
        try:
            obj, _ = _JSON_DECODER.raw_decode(t, pos)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            if obj:
                return obj
            if empty is None:
                empty = obj
        pos = t.find("{", pos + 1)
    return empty


def _parse_suggestions(text: str) -> list:
    """Pull a JSON array of short strings out of the model's reply, tolerating
    markdown fences / extra prose."""
    t = (text or "").strip()
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        arr = json.loads(t[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    out = []
    for s in arr:
        if isinstance(s, str) and s.strip():
            out.append(s.strip()[:60])
        if len(out) == 5:
            break
    return out
