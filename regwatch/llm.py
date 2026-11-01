"""OpenRouter client: one model for every job, one fallback, cache, audit log, mock mode.

Live mode is on when OPENROUTER_API_KEY is set. The model comes from config/llm.yaml
(google/gemma-4-31b-it:free by default) with a single fallback slug used only when the
primary is rate-limited or unavailable. Structured output is obtained by forcing a
function call whose parameters are the JSON schema ("tools" mode), falling back to
JSON mode with the schema inlined. Every response is cached on disk and every call is
written to the llm_call audit table. Callers catch LLMError and use the rule-based path.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime
from functools import lru_cache

import httpx
import yaml
from dotenv import load_dotenv

from . import CACHE_DIR, CONFIG_DIR, ROOT, db
from .security import redact_secrets

load_dotenv(ROOT / ".env")
load_dotenv(CONFIG_DIR / ".env")

API_URL = "https://openrouter.ai/api/v1/chat/completions"


class LLMError(RuntimeError):
    pass


class RateLimited(LLMError):
    pass


@lru_cache(maxsize=1)
def _cfg() -> dict:
    try:
        return yaml.safe_load((CONFIG_DIR / "llm.yaml").read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}


def provider() -> str:
    p = (os.getenv("REGWATCH_PROVIDER", "") or _cfg().get("provider", "openrouter")).strip().lower()
    return p if p in (_cfg().get("providers") or {"openrouter": 1}) else "openrouter"


def _p() -> dict:
    return (_cfg().get("providers") or {}).get(provider(), {})


def api_url() -> str:
    return _p().get("url", "https://openrouter.ai/api/v1/chat/completions")


def api_key() -> str | None:
    key = os.getenv(_p().get("key_env", "OPENROUTER_API_KEY"), "").strip()
    return key or None


def model_name() -> str:
    env = os.getenv("REGWATCH_MODEL", "") or os.getenv(f"{provider().upper()}_MODEL", "")
    return env.strip() or _p().get("model", "google/gemma-4-31b-it:free")


def fallback_model() -> str | None:
    env = os.getenv("REGWATCH_FALLBACK_MODEL", "") or os.getenv(f"{provider().upper()}_FALLBACK_MODEL", "")
    return env.strip() or _p().get("fallback_model")


def response_mode() -> str:
    return os.getenv("REGWATCH_RESPONSE_MODE", "").strip() or _p().get("response_mode", "tools")


def is_live() -> bool:
    return api_key() is not None


def mode_label() -> str:
    return f"LIVE · {provider()} · {model_name()}" if is_live() else "MOCK · rule-based (no API key)"


def _cache_key(model: str, messages: list, schema: dict | None) -> str:
    blob = json.dumps({"m": model, "msgs": messages, "s": schema}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


# ---------------------------------------------------------------- daily-quota circuit breaker
# Free OpenRouter models allow 50 requests/day without credits. Once the daily quota is exhausted,
# every further call would fail, so we stop calling until the reset time and fall back to rules.
_QUOTA = {"until": 0.0, "message": ""}


def _note_quota(r) -> None:
    try:
        err = r.json().get("error", {})
        msg = err.get("message", "")
        reset = int((err.get("metadata") or {}).get("headers", {}).get("X-RateLimit-Reset", 0)) / 1000
    except (ValueError, TypeError, AttributeError):
        return
    if not reset:  # Groq: seconds until the limit resets
        try:
            reset = time.time() + float(r.headers.get("retry-after", 0) or 0)
        except ValueError:
            reset = 0
    if any(s in msg for s in ("per-day", "per day", "(RPD)", "(TPD)")):
        _QUOTA["until"] = reset if reset > time.time() else time.time() + 3600
        _QUOTA["message"] = msg[:160]


def _retry_after(r) -> float | None:
    """Short per-minute limits (Groq TPM/RPM): how long to wait before one retry, if reasonable."""
    try:
        s = float(r.headers.get("retry-after", "") or 0)
    except ValueError:
        return None
    msg = r.text or ""
    if "per day" in msg or "(RPD)" in msg or "(TPD)" in msg or "per-day" in msg:
        return None
    return s if 0 < s <= 65 else None


def quota_status() -> dict | None:
    """None when calls are allowed; otherwise {'until': datetime, 'message': str}."""
    if _QUOTA["until"] > time.time():
        return {"until": datetime.fromtimestamp(_QUOTA["until"]), "message": _QUOTA["message"]}
    return None


def _check_quota() -> None:
    q = quota_status()
    if q:
        raise RateLimited(f"Daily model quota used up until {q['until']:%H:%M}; using rule-based fallback")


def _post(payload: dict) -> dict:
    headers = {
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/regwatch",
        "X-Title": "RegWatch Regulatory Change Impact Agent",
    }
    with httpx.Client(timeout=180) as client:
        r = client.post(api_url(), headers=headers, json=payload)
        if r.status_code == 429 and (wait := _retry_after(r)):
            time.sleep(wait + 0.5)  # per-minute limit: wait it out once instead of failing
            r = client.post(api_url(), headers=headers, json=payload)
    if r.status_code == 429:
        _note_quota(r)
        raise RateLimited(redact_secrets(f"HTTP 429 rate limited: {r.text[:200]}"))
    if r.status_code >= 400:
        raise LLMError(redact_secrets(f"HTTP {r.status_code}: {r.text[:300]}"))
    data = r.json()
    if "error" in data:
        err = str(data["error"])[:300]
        if "429" in err or "rate" in err.lower():
            raise RateLimited(redact_secrets(err))
        raise LLMError(redact_secrets(err))
    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"Unexpected response shape: {str(data)[:300]}") from e


def _extras(model: str) -> dict:
    """Provider/model-specific options (kept out of the cache key)."""
    if provider() != "groq":
        return {}
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": _p().get("reasoning_effort", "low")}
    if model.startswith("qwen/"):
        return {"reasoning_format": "hidden"}  # keep <think> text out of the JSON answer
    return {}


def _payloads(model: str, messages: list[dict], schema: dict, name: str, max_tokens: int) -> list[tuple[str, dict]]:
    base = {"model": model, "temperature": _cfg().get("temperature", 0), "max_tokens": max_tokens, **_extras(model)}
    inline = messages[:-1] + [{
        "role": messages[-1]["role"],
        "content": messages[-1]["content"] + "\n\nRespond with ONLY a JSON object matching this JSON schema:\n"
        + json.dumps(schema),
    }]
    tools = {**base, "messages": messages,
             "tools": [{"type": "function", "function": {
                 "name": name, "description": f"Return the {name} result as structured data.", "parameters": schema}}],
             "tool_choice": {"type": "function", "function": {"name": name}}}
    json_obj = {**base, "messages": inline, "response_format": {"type": "json_object"}}
    strict = {**base, "messages": messages,
              "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}}
    order = {"tools": [("tools", tools), ("json_object", json_obj)],
             "json_object": [("json_object", json_obj)],
             "json_schema": [("json_schema", strict), ("json_object", json_obj)]}
    return order.get(response_mode(), order["tools"])


def _extract(kind: str, message: dict) -> dict:
    if kind == "tools":
        calls = message.get("tool_calls") or []
        if calls:
            args = calls[0].get("function", {}).get("arguments", "")
            return args if isinstance(args, dict) else _parse_json(args)
    return _parse_json(message.get("content") or "")


def chat_json(messages: list[dict], schema: dict, schema_name: str, use_cache: bool = True,
              purpose: str = "", doc_hash: str = "", max_tokens: int | None = None) -> dict:
    """Return a structured object for this prompt, trying the primary model then the fallback."""
    if not is_live():
        raise LLMError("No OPENROUTER_API_KEY set")
    max_tokens = max_tokens or _cfg().get("max_tokens", 4000)
    prompt_chars = sum(len(m["content"]) for m in messages)
    models = [model_name()] + ([fallback_model()] if fallback_model() and fallback_model() != model_name() else [])
    errors = []
    for model in models:
        key = _cache_key(model, messages, schema)
        audit = dict(purpose=purpose, model=model, doc_hash=doc_hash[:16], prompt_sha=key[:16], prompt_chars=prompt_chars)
        cache_file = CACHE_DIR / f"{key}.json"
        if use_cache and cache_file.exists():
            db.log_llm_call(at=_now(), cached=1, status="ok", latency_ms=0, detail="cache hit", **audit)
            return json.loads(cache_file.read_text(encoding="utf-8"))["parsed"]
        if quota_status():  # cached answers above still work; new calls are skipped until the reset
            errors.append(f"{model}: daily quota exhausted")
            continue
        t0 = time.time()
        for attempt, (kind, payload) in enumerate(_payloads(model, messages, schema, schema_name, max_tokens)):
            try:
                parsed = _extract(kind, _post(payload))
                if not isinstance(parsed, dict):
                    raise ValueError("model returned non-object JSON")
                cache_file.write_text(json.dumps({"model": model, "mode": kind, "parsed": parsed},
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
                db.log_llm_call(at=_now(), cached=0, status="ok", latency_ms=int((time.time() - t0) * 1000),
                                detail=f"{kind}", **audit)
                return parsed
            except RateLimited as e:
                errors.append(f"{model}: {e}")
                db.log_llm_call(at=_now(), cached=0, status="rate_limited", latency_ms=int((time.time() - t0) * 1000),
                                detail=str(e)[:300], **audit)
                time.sleep(2)
                break  # go straight to the fallback model
            except (LLMError, httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
                errors.append(f"{model}/{kind}: {redact_secrets(str(e))[:200]}")
        else:
            db.log_llm_call(at=_now(), cached=0, status="error", latency_ms=int((time.time() - t0) * 1000),
                            detail="; ".join(errors)[-300:], **audit)
    raise LLMError("All models failed: " + " | ".join(errors)[-600:])


def chat_with_tools(messages: list[dict], tools: list[dict], purpose: str = "assistant",
                    max_tokens: int = 1200) -> dict:
    """One assistant turn: returns the raw message (content and/or tool_calls). Not cached
    (conversations are unique); primary model then fallback; every call is audited."""
    if not is_live():
        raise LLMError("No OPENROUTER_API_KEY set")
    _check_quota()
    models = [model_name()] + ([fallback_model()] if fallback_model() and fallback_model() != model_name() else [])
    prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
    errors = []
    for model in models:
        t0 = time.time()
        audit = dict(purpose=purpose, model=model, doc_hash="", prompt_sha=_cache_key(model, messages, None)[:16],
                     prompt_chars=prompt_chars, cached=0)
        try:
            msg = _post({"model": model, "messages": messages, "tools": tools, "tool_choice": "auto",
                         "temperature": 0.2, "max_tokens": max_tokens, **_extras(model)})
            db.log_llm_call(at=_now(), status="ok", latency_ms=int((time.time() - t0) * 1000), detail="chat", **audit)
            return msg
        except RateLimited as e:
            errors.append(f"{model}: rate limited")
            db.log_llm_call(at=_now(), status="rate_limited", latency_ms=int((time.time() - t0) * 1000),
                            detail=str(e)[:300], **audit)
        except (LLMError, httpx.HTTPError, ValueError) as e:
            errors.append(f"{model}: {redact_secrets(str(e))[:160]}")
            db.log_llm_call(at=_now(), status="error", latency_ms=int((time.time() - t0) * 1000),
                            detail=redact_secrets(str(e))[:300], **audit)
    raise LLMError("All models failed: " + " | ".join(errors))


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
