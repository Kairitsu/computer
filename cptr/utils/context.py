"""Context usage helpers for chat compaction."""

from __future__ import annotations

from cptr.env import CHAT_COMPACT_TOKEN_THRESHOLD


def estimate_tokens(text: str) -> int:
    """Rough token estimate: len/4 for Latin text."""
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Total estimated tokens for a message list."""
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "text":
                    total += estimate_tokens(block.get("text", ""))
                elif block.get("type") in ("image", "image_url"):
                    total += 1000  # rough estimate for images
        else:
            total += estimate_tokens(content)
        # Tool call arguments
        for tc in m.get("tool_calls", []):
            total += estimate_tokens(tc.get("function", {}).get("arguments", ""))
        total += 4  # per-message overhead (role, separators)
    return total


def build_context_usage(
    tokens: int, *, threshold: int | None = None, compactions: int | None = None
) -> dict:
    """Return context fullness stats for estimated token counts.

    ``compactions`` is how often the agent compacted the chat's session, when known.
    """
    resolved_threshold = threshold or _get_threshold()
    percent = round((tokens / resolved_threshold) * 100) if resolved_threshold > 0 else 0
    usage = {
        "tokens": tokens,
        "estimated_tokens": tokens,
        "threshold": resolved_threshold,
        "percent": max(0, percent),
    }
    if compactions is not None:
        usage["compactions"] = compactions
    return usage


def normalize_usage(usage: dict | None) -> dict | None:
    """Return provider usage with cptr's canonical token field names."""
    if not usage:
        return None
    normalized = dict(usage)
    input_tokens = _parse_nonnegative_int(
        normalized.get("input_tokens", normalized.get("prompt_tokens"))
    )
    output_tokens = _parse_nonnegative_int(
        normalized.get("output_tokens", normalized.get("completion_tokens"))
    )
    total_tokens = _parse_nonnegative_int(normalized.get("total_tokens"))
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens
    normalized["input_tokens"] = input_tokens
    normalized["output_tokens"] = output_tokens
    normalized["total_tokens"] = total_tokens
    return normalized


def usage_context_tokens(usage: dict | None) -> int:
    """Best available context token count from normalized or provider usage."""
    usage = normalize_usage(usage)
    if not usage:
        return 0
    # Agent CLIs report what their window holds; their input totals span every call of a turn.
    context_tokens = _parse_nonnegative_int(usage.get("context_tokens"))
    if context_tokens:
        return context_tokens
    if usage.get("input_tokens"):
        return usage["input_tokens"] + usage.get("output_tokens", 0)
    return usage.get("total_tokens", 0)


def estimate_context_usage(
    messages: list[dict], system_prompt: str, *, threshold: int | None = None
) -> dict:
    """Return context fullness stats using the same estimate as compaction."""
    resolved_threshold = threshold or _get_threshold()
    estimated_tokens = estimate_tokens(system_prompt) + estimate_messages_tokens(messages)
    return build_context_usage(estimated_tokens, threshold=resolved_threshold)


def _parse_positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_nonnegative_int(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def resolve_compact_token_threshold(
    model: str | None = None,
    *,
    chat_models_config: dict | None = None,
    global_threshold: int | None = None,
) -> int:
    """Return the effective compaction threshold for a model.

    Model-level values override the global default.
    """
    default_threshold = global_threshold or _get_threshold()
    model_threshold = None

    if chat_models_config:
        candidate_keys = []
        if model:
            candidate_keys.append(model)
            if "/" in model:
                candidate_keys.append(model.split("/", 1)[1])
        candidate_keys.append("*")

        for key in candidate_keys:
            params = chat_models_config.get(key, {}).get("params", {})
            model_threshold = _parse_positive_int(params.get("compact_token_threshold"))
            if model_threshold:
                break

    return model_threshold or default_threshold


def chat_context_window(chat_params: object) -> int | None:
    """Context window chosen in the composer for one chat, if any.

    It replaces the model/global compaction threshold for that chat.
    """
    if not isinstance(chat_params, dict):
        return None
    return _parse_positive_int(chat_params.get("context_window"))


async def load_compact_token_threshold(model: str | None = None) -> int:
    """Load the effective compaction threshold from persisted model config."""
    try:
        from cptr.models import Config

        chat_models_config = await Config.get("chat.models") or {}
    except Exception:
        chat_models_config = {}
    return resolve_compact_token_threshold(model, chat_models_config=chat_models_config)


def _get_threshold() -> int:
    """Read threshold: app_config (admin UI) > config.toml [chat] > env var/default."""
    try:
        from cptr.utils.config import load_config

        config = load_config()
        # Admin UI saves to [app_config] with dotted keys
        val = config.get("app_config", {}).get("chat.compact_token_threshold")
        if val is not None:
            return int(val)
        # Manual config.toml [chat] section
        val = config.get("chat", {}).get("compact_token_threshold")
        if val is not None:
            return int(val)
    except Exception:
        pass
    return CHAT_COMPACT_TOKEN_THRESHOLD
