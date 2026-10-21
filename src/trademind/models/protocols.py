"""Multi-protocol wire adapters for the LLM provider (TASK-20261021-02).

Four upstream HTTP protocols are supported for the flash strategy LLM:

- openai_chat:             OpenAI-compatible chat completions (DeepSeek official; default)
- anthropic_messages:      Anthropic Messages API (native)
- openai_responses:        OpenAI Responses API
- gemini_generate_content: Gemini native generateContent

Each adapter maps one request to (url, payload) and parses one response body
into a normalized ProtocolReply, so the provider keeps a single
circuit-breaker / budget / audit / repair pipeline. No vendor SDK: the project
speaks raw HTTP for every vendor (same as the TypeSafe JEV adapter), following
each vendor's official HTTP contract. Protocol and model are orthogonal; the
model lock in config.py is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import VALID_LLM_PROTOCOLS

ANTHROPIC_VERSION = "2023-06-01"

DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"

PROTOCOL_DEFAULT_BASE_URL = {
    "openai_chat": DEEPSEEK_DEFAULT_BASE_URL,
    "anthropic_messages": "https://api.anthropic.com",
    "openai_responses": "https://api.openai.com/v1",
    "gemini_generate_content": "https://generativelanguage.googleapis.com",
}

REPAIR_INSTRUCTION = (
    "Your previous reply was not valid JSON. Reply again with "
    "only a JSON object matching the requested schema."
)


@dataclass
class ProtocolReply:
    """Normalized completion result across protocols (diagnostic keys are the
    SAFE_DIAGNOSTIC_KEYS whitelist in domain.modelcall — never extended here)."""

    text: str = ""
    finish_reason: str | None = None
    explicit_refusal: bool = False
    reasoning_present: bool = False
    content_type: str = "missing"
    content_length: int = 0
    content_empty: bool = True
    non_string_content: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    total_tokens: int | None = None
    usage_known: bool = False
    structure_error: str | None = None
    error_message: str | None = None


def _pos_int(v) -> int | None:
    return v if type(v) is int and v >= 0 else None


def _finalize(reply: ProtocolReply) -> None:
    reply.content_length = len(reply.text)
    reply.content_empty = not reply.text.strip()


def effective_base_url(protocol: str, base_url: str) -> str:
    """Resolve the endpoint base for a protocol.

    An unset base_url, or one still pointing at the DeepSeek default, is
    replaced by the protocol's official default so switching the protocol via
    the admin page reaches a sane endpoint; an explicit custom (relay) URL is
    kept verbatim.
    """
    base = (base_url or "").strip().rstrip("/")
    if not base or base == DEEPSEEK_DEFAULT_BASE_URL:
        return PROTOCOL_DEFAULT_BASE_URL[protocol]
    return base


def session_headers(protocol: str, api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if protocol == "anthropic_messages":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = ANTHROPIC_VERSION
    elif protocol == "gemini_generate_content":
        headers["x-goog-api-key"] = api_key
    else:  # openai_chat / openai_responses
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def build_request(protocol: str, *, base_url: str, model: str, system: str,
                  user: str, history: list[tuple[str, str]], temperature: float,
                  max_tokens: int | None, thinking: str) -> tuple[str, dict]:
    """history: repair turns as (role, text) with role in {'assistant', 'user'}."""
    if protocol not in VALID_LLM_PROTOCOLS:
        raise ValueError(f"unknown llm protocol {protocol!r}")
    if protocol == "openai_chat":
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        messages += [{"role": role, "content": text} for role, text in history]
        payload: dict = {"model": model, "messages": messages,
                         "temperature": temperature,
                         "thinking": {"type": thinking},
                         "response_format": {"type": "json_object"},
                         "stream": False}
        if max_tokens:
            payload["max_tokens"] = max_tokens
        return f"{base_url}/chat/completions", payload
    if protocol == "anthropic_messages":
        messages = [{"role": "user", "content": user}]
        messages += [{"role": role, "content": text} for role, text in history]
        payload = {"model": model, "max_tokens": max_tokens or 4096,
                   "temperature": temperature, "system": system,
                   "messages": messages}
        return f"{base_url}/v1/messages", payload
    if protocol == "openai_responses":
        items = [{"role": "user",
                  "content": [{"type": "input_text", "text": user}]}]
        items += [{"role": role,
                   "content": [{"type": "output_text" if role == "assistant"
                                else "input_text", "text": text}]}
                  for role, text in history]
        payload = {"model": model, "instructions": system, "input": items,
                   "temperature": temperature, "stream": False}
        if max_tokens:
            payload["max_output_tokens"] = max_tokens
        return f"{base_url}/responses", payload
    # gemini_generate_content
    contents = [{"role": "user", "parts": [{"text": user}]}]
    contents += [{"role": "model" if role == "assistant" else role,
                  "parts": [{"text": text}]} for role, text in history]
    generation: dict = {"temperature": temperature}
    if max_tokens:
        generation["maxOutputTokens"] = max_tokens
    payload = {"contents": contents,
               "systemInstruction": {"parts": [{"text": system}]},
               "generationConfig": generation}
    return f"{base_url}/v1beta/models/{model}:generateContent", payload


def parse_response(protocol: str, body) -> ProtocolReply:
    if protocol == "openai_chat":
        return _parse_openai_chat(body)
    if protocol == "anthropic_messages":
        return _parse_anthropic_messages(body)
    if protocol == "openai_responses":
        return _parse_openai_responses(body)
    return _parse_gemini_generate_content(body)


def probe_request(protocol: str, base_url: str) -> tuple[str, str, str, bool]:
    """Returns (url, list_key, model_id_key, strip_models_prefix)."""
    if protocol == "anthropic_messages":
        return f"{base_url}/v1/models", "data", "id", False
    if protocol == "openai_responses":
        return f"{base_url}/models", "data", "id", False
    if protocol == "gemini_generate_content":
        return f"{base_url}/v1beta/models", "models", "name", True
    return f"{base_url}/models", "data", "id", False


def _parse_openai_chat(body) -> ProtocolReply:
    reply = ProtocolReply()
    if not isinstance(body, dict):
        reply.structure_error = "invalid response JSON"
        return reply
    raw_usage = body.get("usage")
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    inp = _pos_int(usage.get("prompt_tokens"))
    out = _pos_int(usage.get("completion_tokens"))
    reply.usage_known = isinstance(raw_usage, dict) and inp is not None and out is not None
    reply.input_tokens, reply.output_tokens = inp, out
    comp = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
    prompt = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    reply.reasoning_tokens = _pos_int(comp.get("reasoning_tokens"))
    reply.cached_tokens = _pos_int(prompt.get("cached_tokens"))
    if reply.cached_tokens is None:
        reply.cached_tokens = _pos_int(usage.get("prompt_cache_hit_tokens"))
    reply.total_tokens = _pos_int(usage.get("total_tokens"))
    choices = body.get("choices")
    if (not isinstance(choices, list) or not choices
            or not isinstance(choices[0], dict)
            or not isinstance(choices[0].get("message"), dict)):
        reply.structure_error = "missing choices or message"
        return reply
    choice = choices[0]
    message = choice["message"]
    fr = choice.get("finish_reason")
    reply.finish_reason = fr if isinstance(fr, str) else None
    raw_content = message.get("content")
    reply.content_type = ("missing" if "content" not in message
                          else ("null" if raw_content is None else type(raw_content).__name__))
    reply.text = raw_content if isinstance(raw_content, str) else ""
    reply.non_string_content = raw_content is not None and not isinstance(raw_content, str)
    refusal_val = message.get("refusal")
    reply.explicit_refusal = (bool(isinstance(refusal_val, str) and refusal_val.strip())
                              or reply.finish_reason == "content_filter")
    reasoning_val = message.get("reasoning_content")
    reply.reasoning_present = bool(
        (isinstance(reasoning_val, str) and reasoning_val.strip())
        or (reply.reasoning_tokens is not None and reply.reasoning_tokens > 0))
    _finalize(reply)
    return reply


def _parse_anthropic_messages(body) -> ProtocolReply:
    reply = ProtocolReply()
    if not isinstance(body, dict):
        reply.structure_error = "invalid response JSON"
        return reply
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    inp = _pos_int(usage.get("input_tokens"))
    out = _pos_int(usage.get("output_tokens"))
    reply.usage_known = inp is not None and out is not None
    reply.input_tokens, reply.output_tokens = inp, out
    if inp is not None and out is not None:
        reply.total_tokens = inp + out
    reply.cached_tokens = _pos_int(usage.get("cache_read_input_tokens"))
    blocks = body.get("content")
    if not isinstance(blocks, list):
        reply.structure_error = "missing content blocks"
        return reply
    texts: list[str] = []
    thinking_present = False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            if isinstance(block.get("text"), str):
                texts.append(block["text"])
            else:
                reply.non_string_content = True
        elif block.get("type") == "thinking":
            thinking_present = True
    reply.text = "".join(texts)
    reply.content_type = "content_blocks" if blocks else "missing"
    stop = body.get("stop_reason")
    mapping = {"end_turn": "stop", "stop_sequence": "stop",
               "max_tokens": "length", "refusal": "content_filter",
               "pause_turn": "pause_turn", "tool_use": "tool_use"}
    reply.finish_reason = mapping.get(stop) if isinstance(stop, str) else None
    reply.explicit_refusal = stop == "refusal"
    reply.reasoning_present = thinking_present
    _finalize(reply)
    return reply


def _parse_openai_responses(body) -> ProtocolReply:
    reply = ProtocolReply()
    if not isinstance(body, dict):
        reply.structure_error = "invalid response JSON"
        return reply
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    inp = _pos_int(usage.get("input_tokens"))
    out = _pos_int(usage.get("output_tokens"))
    reply.usage_known = inp is not None and out is not None
    reply.input_tokens, reply.output_tokens = inp, out
    reply.total_tokens = _pos_int(usage.get("total_tokens"))
    out_details = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), dict) else {}
    in_details = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), dict) else {}
    reply.reasoning_tokens = _pos_int(out_details.get("reasoning_tokens"))
    reply.cached_tokens = _pos_int(in_details.get("cached_tokens"))
    status = body.get("status")
    if status == "failed":
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        msg = err.get("message")
        reply.error_message = (f"responses request failed: {msg}" if isinstance(msg, str) and msg
                               else "responses request failed")
        return reply
    output = body.get("output")
    if not isinstance(output, list):
        reply.structure_error = "missing output items"
        return reply
    texts: list[str] = []
    refusal = False
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif part.get("type") == "refusal":
                refusal = True
    reply.text = "".join(texts)
    reply.content_type = "output_items" if output else "missing"
    if status == "incomplete":
        details = body.get("incomplete_details") if isinstance(body.get("incomplete_details"), dict) else {}
        reply.finish_reason = "length" if details.get("reason") == "max_output_tokens" else None
    elif status == "completed":
        reply.finish_reason = "stop"
    reply.explicit_refusal = refusal
    _finalize(reply)
    return reply


_GEMINI_REFUSAL_REASONS = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION"}


def _parse_gemini_generate_content(body) -> ProtocolReply:
    reply = ProtocolReply()
    if not isinstance(body, dict):
        reply.structure_error = "invalid response JSON"
        return reply
    usage = body.get("usageMetadata") if isinstance(body.get("usageMetadata"), dict) else {}
    inp = _pos_int(usage.get("promptTokenCount"))
    out = _pos_int(usage.get("candidatesTokenCount"))
    reply.usage_known = inp is not None and out is not None
    reply.input_tokens, reply.output_tokens = inp, out
    reply.total_tokens = _pos_int(usage.get("totalTokenCount"))
    reply.reasoning_tokens = _pos_int(usage.get("thoughtsTokenCount"))
    reply.cached_tokens = _pos_int(usage.get("cachedContentTokenCount"))
    feedback = body.get("promptFeedback") if isinstance(body.get("promptFeedback"), dict) else {}
    if feedback.get("blockReason"):
        reply.explicit_refusal = True
        reply.finish_reason = "content_filter"
        reply.content_type = "missing"
        _finalize(reply)
        return reply
    candidates = body.get("candidates")
    if (not isinstance(candidates, list) or not candidates
            or not isinstance(candidates[0], dict)):
        reply.structure_error = "missing candidates"
        return reply
    candidate = candidates[0]
    content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
    parts = content.get("parts") if isinstance(content.get("parts"), list) else []
    texts: list[str] = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            texts.append(part["text"])
    reply.text = "".join(texts)
    reply.content_type = "parts" if parts else "missing"
    reason = candidate.get("finishReason")
    if reason == "STOP":
        reply.finish_reason = "stop"
    elif reason == "MAX_TOKENS":
        reply.finish_reason = "length"
    elif isinstance(reason, str) and reason in _GEMINI_REFUSAL_REASONS:
        reply.finish_reason = "content_filter"
        reply.explicit_refusal = True
    _finalize(reply)
    return reply
