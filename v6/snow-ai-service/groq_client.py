"""
Central Groq client for the SoftProdigy Snow Agent.

Every AI call in the service goes through here, so the API key and model
selection live in ONE place instead of being copied into each file.

What it does
------------
  1. Reads GROQ_API_KEY from the environment or the service `.env` file.
  2. Asks Groq which models your key can actually use (GET /models, cached)
     and skips any model Groq refuses (fixes the old `404 model_not_found`).
  3. Throttles parallel calls (GROQ_MAX_CONCURRENCY, default 3) so generating
     BRD + FRD + TSD at once does not trip Groq's rate limits.
  4. Waits and retries on 429 rate limits, shrinks max_tokens when Groq says a
     request is too large for your tokens-per-minute budget (413), and falls
     back to the next model when one keeps failing.
  5. Returns clean JSON for structured prompts (JSON mode + a tolerant parser
     that handles ```json fences and extra prose).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import requests
from dotenv import load_dotenv

# Always load the .env that sits next to this file, whatever the working dir.
load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger("groq_client")

# Never embed provider credentials in source.  `load_dotenv()` above supports a
# local .env file for development while deployment environments can inject it.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
MAX_CONCURRENCY = max(1, int(os.getenv("GROQ_MAX_CONCURRENCY", "3")))
MAX_RATE_LIMIT_WAIT = float(os.getenv("GROQ_MAX_RATE_LIMIT_WAIT", "60"))

# Preferred chat models, best first. Only ones your key can access are used.
FALLBACK_MODELS: List[str] = [
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "moonshotai/kimi-k2-instruct-0905",
    "moonshotai/kimi-k2-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "qwen/qwen3-32b",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
]
# Models that accept images (used for whiteboard / screenshot input).
VISION_MODELS: List[str] = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
]
WHISPER_MODELS: List[str] = ["whisper-large-v3", "whisper-large-v3-turbo"]

# Models returned by /models that are not general chat models.
_NON_CHAT = re.compile(r"whisper|tts|playai|guard|distil|orpheus|compound|allam", re.I)

_lock = threading.Lock()
_slots = threading.BoundedSemaphore(MAX_CONCURRENCY)
_available_models: Optional[List[str]] = None
_available_fetched_at = 0.0
_working_model: Optional[str] = None
_dead_models: set = set()


class GroqError(RuntimeError):
    pass


def get_api_key() -> str:
    key = GROQ_API_KEY.strip()
    if not key:
        raise GroqError("No Groq API key available")
    return key


def current_model() -> Optional[str]:
    return _working_model


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {get_api_key()}", "Content-Type": "application/json"}


# ─── Model discovery ─────────────────────────────────────────────────────────

def list_available_models(force: bool = False) -> List[str]:
    """Models this API key can use (cached for 10 minutes)."""
    global _available_models, _available_fetched_at
    with _lock:
        if not force and _available_models is not None and time.time() - _available_fetched_at < 600:
            return _available_models
    try:
        resp = requests.get(f"{GROQ_BASE_URL}/models", headers=_headers(), timeout=20)
        if resp.status_code == 401:
            raise GroqError("Groq rejected GROQ_API_KEY (401 Invalid API key). Check the environment or .env file")
        resp.raise_for_status()
        models = [m["id"] for m in resp.json().get("data", []) if m.get("active", True)]
        with _lock:
            _available_models = models
            _available_fetched_at = time.time()
        return models
    except GroqError:
        raise
    except Exception as e:  # network hiccup: don't block, just try the list blindly
        logger.warning(f"Could not list Groq models ({e}); trying fallback list directly")
        return []


def _candidate_models(vision: bool = False) -> List[str]:
    preferred = [] if vision else [m.strip() for m in (os.getenv("GROQ_MODEL") or "").split(",") if m.strip()]
    base = VISION_MODELS if vision else FALLBACK_MODELS
    ordered: List[str] = []
    head = [] if vision or not _working_model else [_working_model]
    for m in head + preferred + base:
        if m and m not in ordered:
            ordered.append(m)

    available = list_available_models()
    if available:
        avail_set = set(available)
        filtered = [m for m in ordered if m in avail_set]
        if vision:
            ordered = filtered or [m for m in available if re.search(r"llama-4|vision|maverick|scout", m)]
        else:
            extra = sorted(m for m in available if m not in filtered and not _NON_CHAT.search(m))
            ordered = filtered + extra
            missing = [m for m in preferred if m not in avail_set]
            if missing:
                logger.warning(f"GROQ_MODEL {missing} is not available to this key; using {ordered[:1]} instead")
    ordered = [m for m in ordered if m not in _dead_models] or ordered
    if not ordered:
        raise GroqError("No suitable Groq models are available to this API key")
    return ordered


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _strip_thinking(text: str) -> str:
    # qwen3 and some reasoning models wrap chain-of-thought in <think> tags
    return re.sub(r"<think>.*?</think>\s*", "", text or "", flags=re.S).strip()


def _retry_after_seconds(resp: requests.Response, attempt: int) -> float:
    wait = resp.headers.get("retry-after")
    try:
        if wait:
            return min(float(wait), MAX_RATE_LIMIT_WAIT)
    except ValueError:
        pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", resp.text)
    if m:
        return min(int(m.group(1) or 0) * 60 + float(m.group(2)) + 0.5, MAX_RATE_LIMIT_WAIT)
    return min(2.0 * (attempt + 1), MAX_RATE_LIMIT_WAIT)


def parse_json(text: str) -> Any:
    """Parse JSON from a model reply that may include prose, ```json fences or trailing commas."""
    if text is None:
        raise ValueError("empty reply")
    t = _strip_thinking(text).strip()
    candidates = []
    for m in re.finditer(r"```(?:json|JSON)?\s*(.*?)```", t, re.S):
        candidates.append(m.group(1).strip())
    candidates.append(t)
    # outermost {...} or [...] block
    for open_c, close_c in (("{", "}"), ("[", "]")):
        s, e = t.find(open_c), t.rfind(close_c)
        if s != -1 and e > s:
            candidates.append(t[s:e + 1])
    last_err: Optional[Exception] = None
    for c in candidates:
        for variant in (c, re.sub(r",\s*([}\]])", r"\1", c)):
            try:
                return json.loads(variant)
            except Exception as e:  # noqa: PERF203
                last_err = e
    raise ValueError(f"AI reply was not valid JSON ({last_err})")


# ─── Core call ───────────────────────────────────────────────────────────────

def chat(
    messages: List[Dict[str, Any]],
    max_tokens: int = 4096,
    temperature: float = 0.3,
    model: Optional[str] = None,
    json_mode: bool = False,
    vision: bool = False,
) -> str:
    """Chat completion with throttling, rate-limit handling and model fallback."""
    global _working_model
    models = [model] if model else _candidate_models(vision=vision)
    last_error = "unknown error"

    with _slots:
        for m in models:
            tokens = max(256, int(max_tokens))
            use_json = json_mode
            attempt = 0
            while attempt < 6:
                payload: Dict[str, Any] = {"model": m, "messages": messages, "max_tokens": tokens,
                                           "temperature": temperature}
                if use_json:
                    payload["response_format"] = {"type": "json_object"}
                try:
                    resp = requests.post(f"{GROQ_BASE_URL}/chat/completions", headers=_headers(),
                                         json=payload, timeout=180)
                except requests.RequestException as e:
                    last_error = f"network error: {e}"
                    attempt += 1
                    time.sleep(1.5 * attempt)
                    continue

                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"].get("content") or ""
                    if not vision and _working_model != m:
                        logger.info(f"Groq model in use: {m}")
                        _working_model = m
                    return _strip_thinking(content)

                body = resp.text[:600]
                low = body.lower()
                last_error = f"{resp.status_code} on {m}: {body}"

                if resp.status_code == 401:
                    raise GroqError("Groq rejected GROQ_API_KEY (401 Invalid API key). Check the environment or .env file")
                if resp.status_code in (403, 404):  # model missing / blocked for this key
                    logger.warning(f"Groq model {m} unavailable ({resp.status_code}); trying next model")
                    _dead_models.add(m)
                    break
                if resp.status_code == 413 or "request too large" in low:
                    lim = re.search(r"limit (\d+), requested (\d+)", low)
                    if lim:
                        over = int(lim.group(2)) - int(lim.group(1))
                        new_tokens = tokens - over - 256
                        if new_tokens >= 700:
                            logger.info(f"{m}: request over TPM budget by {over} tokens; retrying with max_tokens={new_tokens}")
                            tokens = new_tokens
                            attempt += 1
                            continue
                    logger.warning(f"Prompt too large for {m}; trying next model")
                    break
                if resp.status_code == 400 and use_json and ("json" in low or "response_format" in low):
                    use_json = False  # model can't do JSON mode or failed validation: ask again in plain mode
                    attempt += 1
                    continue
                if resp.status_code == 400 and "max_" in low and "token" in low:
                    found = re.search(r"less than or equal to [`']?(\d+)", body)
                    tokens = int(found.group(1)) if found else tokens // 2
                    attempt += 1
                    continue
                if "context_length" in low or "reduce the length" in low:
                    logger.warning(f"Prompt too long for {m}'s context; trying next model")
                    break
                if resp.status_code == 429 or resp.status_code >= 500:
                    if "tokens per day" in low or "(tpd)" in low or "requests per day" in low:
                        logger.warning(f"{m}: daily quota reached; trying next model")
                        break
                    wait_s = _retry_after_seconds(resp, attempt)
                    if attempt < 5:
                        logger.info(f"{m}: rate limited, waiting {wait_s:.1f}s")
                        time.sleep(wait_s)
                        attempt += 1
                        continue
                    break
                break  # other 4xx: try next model

    raise GroqError(f"All Groq models failed. Last error: {last_error}")


# ─── Convenience wrappers ────────────────────────────────────────────────────

def _messages(prompt: str, system: str = "") -> List[Dict[str, str]]:
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return msgs


def generate(prompt: str, system: str = "", max_tokens: int = 4096, temperature: float = 0.3) -> str:
    return chat(_messages(prompt, system), max_tokens=max_tokens, temperature=temperature)


async def agenerate(prompt: str, system: str = "", max_tokens: int = 4096, temperature: float = 0.3) -> str:
    """Async wrapper — runs in a thread so the FastAPI event loop is never blocked."""
    return await asyncio.to_thread(generate, prompt, system, max_tokens, temperature)


def generate_json(prompt: str, system: str = "", max_tokens: int = 6000, temperature: float = 0.2) -> Union[dict, list]:
    """Ask for JSON and return the parsed object. Repairs the reply once if it isn't valid JSON."""
    sys_msg = (system + "\n" if system else "") + "Respond with a single valid JSON object only. No prose, no markdown fences."
    text = chat(_messages(prompt, sys_msg), max_tokens=max_tokens, temperature=temperature, json_mode=True)
    try:
        return parse_json(text)
    except ValueError:
        logger.warning("AI reply was not valid JSON; asking the model to repair it")
        fix = chat(_messages("Convert the following into ONE valid JSON object. Keep all content, fix syntax only. "
                             "Return JSON only.\n\n" + text[:20000]),
                   max_tokens=max_tokens, temperature=0, json_mode=True)
        return parse_json(fix)


async def agenerate_json(prompt: str, system: str = "", max_tokens: int = 6000, temperature: float = 0.2):
    return await asyncio.to_thread(generate_json, prompt, system, max_tokens, temperature)


def describe_image(image_b64: str, media_type: str, instruction: str, max_tokens: int = 3000) -> str:
    """Read an image (whiteboard photo, screenshot, scanned page) with a Groq vision model."""
    content = [
        {"type": "text", "text": instruction},
        {"type": "image_url", "image_url": {"url": f"data:{media_type or 'image/png'};base64,{image_b64}"}},
    ]
    return chat([{"role": "user", "content": content}], max_tokens=max_tokens, temperature=0.1, vision=True)


def transcribe(file_bytes: bytes, filename: str, language: str = "en") -> str:
    """Speech-to-text with Groq Whisper, falling back across Whisper models."""
    last_error = "unknown error"
    for m in WHISPER_MODELS:
        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{GROQ_BASE_URL}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {get_api_key()}"},
                    files={"file": (filename, file_bytes)},
                    data={"model": m, "response_format": "text", "language": language},
                    timeout=300,
                )
            except requests.RequestException as e:
                last_error = str(e)
                continue
            if resp.status_code == 200:
                return resp.text.strip()
            last_error = f"{resp.status_code} on {m}: {resp.text[:300]}"
            if resp.status_code == 429:
                time.sleep(_retry_after_seconds(resp, attempt))
                continue
            break
        if "401" in last_error:
            break
    raise GroqError(f"Audio transcription failed. {last_error}")


def quick_status(timeout: float = 20) -> Dict[str, object]:
    """Fast start-up check (no retries): is api.groq.com reachable, is the key valid, does one model answer?"""
    try:
        r = requests.get(f"{GROQ_BASE_URL}/models", headers=_headers(), timeout=timeout)
    except GroqError as e:
        return {"ok": False, "error": str(e)}
    except requests.RequestException as e:
        return {"ok": False, "error": f"cannot reach api.groq.com ({type(e).__name__}) - check internet/proxy/firewall"}
    if r.status_code == 401:
        return {"ok": False, "error": "Groq rejected the API key (401) - update GROQ_API_KEY in the environment or .env file"}
    if not r.ok:
        return {"ok": False, "error": f"Groq returned {r.status_code}: {r.text[:120]}"}
    avail = {m["id"] for m in r.json().get("data", [])}
    model = next((m for m in FALLBACK_MODELS if m in avail), None) or next(iter(sorted(avail)), None)
    if not model:
        return {"ok": False, "error": "no chat models available for this key"}
    try:
        c = requests.post(f"{GROQ_BASE_URL}/chat/completions", headers=_headers(), timeout=timeout,
                          json={"model": model, "max_tokens": 8, "temperature": 0,
                                "messages": [{"role": "user", "content": "Reply with exactly: OK"}]})
    except requests.RequestException as e:
        return {"ok": False, "error": f"Groq did not answer in time ({type(e).__name__})"}
    if c.status_code == 429:
        return {"ok": True, "model": model, "note": "rate-limited right now; requests will wait and retry"}
    if not c.ok:
        return {"ok": False, "error": f"{model}: {c.status_code} {c.text[:120]}"}
    return {"ok": True, "model": model}


def status() -> Dict[str, object]:
    """Quick diagnostics used by the /snow/ai-status endpoint and the startup check."""
    try:
        reply = generate("Reply with exactly: OK", max_tokens=16, temperature=0)
        return {"ok": True, "model": _working_model, "reply": reply}
    except Exception as e:
        return {"ok": False, "model": _working_model, "error": str(e)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        models = list_available_models(force=True)
        print(f"Key OK. {len(models)} models available:")
        for m in sorted(models):
            print("  -", m)
    except Exception as e:
        print("Model listing failed:", e)
    print("Test call:", status())
