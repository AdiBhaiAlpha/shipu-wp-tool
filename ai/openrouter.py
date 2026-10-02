"""OpenRouter AI gateway for ShiPu WP.

Design notes (learned from the live free tier, not from theory)
---------------------------------------------------------------
The free model pool is *hostile* to naive clients, so this module handles the
four failure modes that were observed in production probing:

1. **429 upstream rate limits.** Almost every free model returns
   ``Provider returned error`` / ``temporarily rate-limited``. Handled with
   bounded retries and then **failover** to the next configured model.
2. **Reasoning models return ``content: null``.** Some free models
   (``nvidia/nemotron-3-super-120b-a12b:free`` and friends) spend the whole
   token budget on a ``reasoning`` field and never emit ``content``. We detect
   that, retry once with a larger budget, and fall back to the reasoning text
   as a last resort.
3. **Truncation.** ``finish_reason == "length"`` means the reply was cut off -
   unacceptable in a chat app, so we treat it as a retryable failure.
4. **Model retirement.** Never hard-code a single model; keep a list.

The key is read from the environment only (see ``config.settings.AIConfig``).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config import Config, get, get_config

# Concise, non-inventive system prompt (idea.txt item 27).
SYSTEM_PROMPT = (
    "You generate replies for a WhatsApp assistant.\n"
    "Reply naturally and concisely, in the SAME language as the incoming message.\n"
    "Do not invent facts, names, dates or prices.\n"
    "Do not claim to have performed actions you did not perform.\n"
    "Do not add explanations, notes or quotation marks.\n"
    "Return ONLY the reply text."
)

# Failure codes worth retrying (transient), versus hard failures.
RETRYABLE_HTTP = (408, 425, 429, 500, 502, 503, 504)
RETRYABLE_MARKERS = (
    "rate-limit",
    "rate limited",
    "temporarily",
    "resourceexhausted",
    "provider returned error",
    "timeout",
    "overloaded",
    "try again",
    "upstream error",
)

# Reasoning models need a much larger budget before they emit `content`.
REASONING_BUDGET = 1200
NORMAL_BUDGET = 220

_WHITESPACE = re.compile(r"\s+")
# Some models still wrap output despite instructions.
_QUOTED = re.compile(r'^\s*["“\'](.*)["”\']\s*$', re.DOTALL)
_PREFIXES = re.compile(
    r"^\s*(reply|response|answer|output|assistant)\s*[:\-]\s*", re.IGNORECASE
)


class AIError(Exception):
    """Any AI failure with a message safe to show in the terminal."""

    def __init__(self, message: str, code: str = "ai_error") -> None:
        super().__init__(message)
        self.code = code


class AINotConfigured(AIError):
    def __init__(self) -> None:
        super().__init__(
            "OpenRouter is not configured. Set OPENROUTER_API_KEY and "
            "OPENROUTER_MODEL in .env",
            "not_configured",
        )


class AIQuotaExceeded(AIError):
    """The free-plan request allowance for this key is used up."""


@dataclass
class AIResult:
    """One completed reply plus the metadata worth logging."""

    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    attempts: List[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def clean_reply(text: str) -> str:
    """Strip the scaffolding models add around a short reply."""
    if not text:
        return ""
    value = text.strip()
    # Reasoning models wrap their chain-of-thought in a tag. Keep whatever
    # follows it (the actual answer), in either the closed or unclosed case.
    for open_tag, close_tag in (("<think>", "</think>"), ("<thinking>", "</thinking>"), ("<reasoning>", "</reasoning>")):
        if close_tag in value:
            value = value.rsplit(close_tag, 1)[1].strip()
        elif open_tag in value:
            # Unclosed tag: the answer is what follows it.
            value = value.split(open_tag, 1)[1].strip()
    quoted = _QUOTED.match(value)
    if quoted:
        value = quoted.group(1).strip()
    value = _PREFIXES.sub("", value)
    value = _WHITESPACE.sub(" ", value)
    # A WhatsApp reply should not run to paragraphs.
    if len(value) > 600:
        value = value[:597].rsplit(" ", 1)[0] + "..."
    return value


class OpenRouterClient:
    """Minimal, dependency-free OpenRouter chat client."""

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or get_config()
        self.ai = self.config.ai

    # ------------------------------------------------------------- helpers
    @property
    def configured(self) -> bool:
        return self.ai.configured

    def models(self) -> List[str]:
        """Ordered candidate models: primary first, then fallbacks."""
        configured = self.config.ai
        candidates: List[str] = []
        for value in (configured.model,):
            if value:
                candidates.append(value)
        fallback_raw = get("OPENROUTER_FALLBACK_MODELS") or (
            "liquid/lfm-2.5-2.6b:free,dots-studio/dots-3-note-preview:free"
        )
        for value in fallback_raw.split(","):
            model = value.strip()
            if model and model not in candidates:
                candidates.append(model)
        return candidates

    def _post(self, model: str, messages: List[Dict[str, str]], max_tokens: int) -> Dict:
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.7,
                "top_p": 0.95,
            }
        ).encode()
        request = urllib.request.Request(
            f"{self.ai.base_url.rstrip('/')}/chat/completions",
            data=payload,
            method="POST",
        )
        request.add_header("Authorization", f"Bearer {self.ai.api_key}")
        request.add_header("Content-Type", "application/json")
        request.add_header("HTTP-Referer", self.ai.referer)
        request.add_header("X-Title", self.ai.app_title)
        try:
            with urllib.request.urlopen(request, timeout=self.ai.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = _error_detail(exc)
            message = detail.get("message", "")
            if "daily requests" in message or "free_model" in message:
                raise AIQuotaExceeded(
                    "OpenRouter free-tier daily allowance is used up. "
                    "Try again tomorrow or use a paid key."
                ) from exc
            if exc.code in RETRYABLE_HTTP or _is_retryable(message):
                raise AIError(message or f"HTTP {exc.code}", "retryable") from exc
            raise AIError(message or f"HTTP {exc.code}", "ai_error") from exc
        except urllib.error.URLError as exc:
            raise AIError(f"Network error: {exc.reason}", "retryable") from exc
        except TimeoutError as exc:
            raise AIError("Request timed out", "retryable") from exc

    # ------------------------------------------------------------- request
    def reply(
        self,
        sender: str,
        message: str,
        context: Optional[List[Dict[str, str]]] = None,
        per_model_attempts: int = 2,
    ) -> AIResult:
        """Generate one WhatsApp reply, failing over across models.

        Raises :class:`AIQuotaExceeded` when the account allowance is spent and
        :class:`AIError` when every candidate failed.
        """
        if not self.configured:
            raise AINotConfigured()

        messages: List[Dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(context or [])
        messages.append(
            {
                "role": "user",
                "content": f"sender: {sender}\nmessage: {message}",
            }
        )

        attempts: List[str] = []
        last_error: Optional[Exception] = None

        for model in self.models():
            budget = NORMAL_BUDGET
            for attempt in range(per_model_attempts):
                try:
                    data = self._post(model, messages, budget)
                except AIQuotaExceeded:
                    raise
                except AIError as exc:
                    last_error = exc
                    attempts.append(f"{model}: {exc}")
                    if exc.code == "retryable" and attempt < per_model_attempts - 1:
                        time.sleep(0.6 * (attempt + 1))
                        continue
                    break  # move to the next model

                choice = (data.get("choices") or [{}])[0]
                message_obj = choice.get("message") or {}
                content = message_obj.get("content")
                reasoning = message_obj.get("reasoning")
                finish = choice.get("finish_reason")

                # Reasoning models need a bigger budget before they answer.
                if (not content or not str(content).strip()) and reasoning:
                    if budget < REASONING_BUDGET:
                        budget = REASONING_BUDGET
                        continue
                    text = clean_reply(str(reasoning))
                    if text:
                        return _result(text, model, data, attempts)
                    attempts.append(f"{model}: empty content")
                    break

                if not content or not str(content).strip():
                    attempts.append(f"{model}: empty content")
                    break

                if finish == "length":
                    attempts.append(f"{model}: truncated at {budget} tokens")
                    if budget < REASONING_BUDGET:
                        budget = REASONING_BUDGET
                        continue
                    break

                text = clean_reply(str(content))
                if not text:
                    attempts.append(f"{model}: reply cleaned to empty")
                    break
                return _result(text, str(data.get("model") or model), data, attempts)

        detail = "; ".join(attempts[-3:]) if attempts else "no models available"
        raise AIError(f"All AI models failed ({detail})", "unavailable")


# --------------------------------------------------------------------------
# module-level convenience
# --------------------------------------------------------------------------
_client: Optional[OpenRouterClient] = None


def get_client() -> OpenRouterClient:
    global _client
    if _client is None:
        _client = OpenRouterClient()
    return _client


def reply(sender: str, message: str, **kwargs) -> AIResult:
    return get_client().reply(sender, message, **kwargs)


def status_line() -> str:
    """One-line state for the agent UI (never shows the key)."""
    client = get_client()
    if not client.configured:
        return "NOT CONFIGURED"
    return f"READY · {len(client.models())} model(s)"


# --------------------------------------------------------------------------
# internals
# --------------------------------------------------------------------------
def _result(text: str, model: str, data: Dict, attempts: List[str]) -> AIResult:
    usage = data.get("usage") or {}
    return AIResult(
        text=text,
        model=model,
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        attempts=list(attempts),
    )


def _error_detail(exc: urllib.error.HTTPError) -> Dict:
    try:
        return json.loads(exc.read().decode("utf-8")).get("error", {}) or {}
    except Exception:
        return {}


def _is_retryable(message: str) -> bool:
    lowered = (message or "").lower()
    return any(marker in lowered for marker in RETRYABLE_MARKERS)


__all__ = [
    "AIError",
    "AINotConfigured",
    "AIQuotaExceeded",
    "AIResult",
    "OpenRouterClient",
    "SYSTEM_PROMPT",
    "clean_reply",
    "get_client",
    "reply",
    "status_line",
]