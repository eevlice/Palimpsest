#!/usr/bin/env python3
"""
Unified provider layer: Anthropic, OpenAI, Google (Gemini).

One call() covers all three. Callers always pass the same three text
blocks (system, standing_context, per_call_text) and get back the same
shape: (text, cost, token_detail). Prompt structure (system -> standing
context -> per-call text, in that fixed order) is kept identical across
providers so each SDK's own automatic prefix caching kicks in without any
provider-specific cache bookkeeping, except Anthropic where we still set
explicit cache_control breakpoints (its caching is opt-in, not automatic).

Effort is an abstract knob: "off" | "low" | "medium" | "high". Each
provider maps it to its own mechanism (thinking budget tokens, or
reasoning_effort). A model that doesn't support effort silently ignores
anything other than "off". Budgets are capped server-side (EFFORT_BUDGETS)
so a client can't request an unbounded thinking spend.
"""

import keys

EFFORT_LEVELS = ("off", "low", "medium", "high")

# Server-side ceiling on thinking/reasoning spend per effort tier - a client
# cannot get more than this no matter what it sends.
EFFORT_BUDGETS = {"low": 2000, "medium": 6000, "high": 16000}

PROVIDERS = {
    "anthropic": {
        "label": "Claude",
        "effort_kind": "thinking_budget",
        "models": {
            # cache_write is priced for the 1h TTL (2x base input) to match the
            # ttl:"1h" set in _call_anthropic below - it was 1.25x (5min TTL)
            # and quietly under-reported real spend once the TTL changed.
            "claude-opus-4-8": {"label": "Opus 4.8", "in": 5.0, "out": 25.0, "cache_write": 10.0, "cache_read": 0.50, "effort": True},
            "claude-sonnet-5": {"label": "Sonnet 5", "in": 3.0, "out": 15.0, "cache_write": 6.0, "cache_read": 0.30, "effort": True, "thinking_style": "adaptive"},
            "claude-sonnet-4-6": {"label": "Sonnet 4.6", "in": 3.0, "out": 15.0, "cache_write": 6.0, "cache_read": 0.30, "effort": True},
            "claude-haiku-4-5-20251001": {"label": "Haiku 4.5", "in": 1.0, "out": 5.0, "cache_write": 2.0, "cache_read": 0.10, "effort": True},
        },
    },
    "openai": {
        "label": "ChatGPT",
        "effort_kind": "reasoning_effort",
        "models": {
            "gpt-5.6-sol": {"label": "GPT-5.6 Sol", "in": 5.0, "out": 30.0, "cache_read": 0.50, "effort": True},
            "gpt-5.6-terra": {"label": "GPT-5.6 Terra", "in": 2.5, "out": 15.0, "cache_read": 0.25, "effort": True},
            "gpt-5.6-luna": {"label": "GPT-5.6 Luna", "in": 1.0, "out": 6.0, "cache_read": 0.10, "effort": True},
            "gpt-5.4-mini": {"label": "GPT-5.4 mini", "in": 0.75, "out": 4.5, "cache_read": 0.075, "effort": False},
        },
    },
    "google": {
        "label": "Gemini",
        "effort_kind": "thinking_budget",
        "models": {
            "gemini-3.1-pro": {"label": "Gemini 3.1 Pro", "in": 2.0, "out": 12.0, "cache_read": 0.40, "effort": True},
            "gemini-3.5-flash": {"label": "Gemini 3.5 Flash", "in": 1.5, "out": 9.0, "cache_read": 0.30, "effort": True},
            "gemini-3-flash-lite": {"label": "Gemini 3 Flash-Lite", "in": 0.10, "out": 0.40, "cache_read": 0.02, "effort": False},
        },
    },
}


def list_catalog():
    """[{provider, label, models:[{id, label, effort}]}] - for the frontend model picker."""
    out = []
    for pid, p in PROVIDERS.items():
        out.append({
            "provider": pid,
            "label": p["label"],
            "has_key": bool(keys.get_key(pid)),
            "models": [
                {"id": mid, "label": m["label"], "effort": m["effort"]}
                for mid, m in p["models"].items()
            ],
        })
    return out


def model_supports_effort(provider, model):
    m = PROVIDERS.get(provider, {}).get("models", {}).get(model)
    return bool(m and m["effort"])


def _price(provider, model):
    return PROVIDERS[provider]["models"][model]


def _client_for(provider):
    key = keys.get_key(provider)
    if not key:
        raise RuntimeError(f"no API key for {provider}")
    if provider == "anthropic":
        from anthropic import Anthropic
        return Anthropic(api_key=key)
    if provider == "openai":
        from openai import OpenAI
        return OpenAI(api_key=key)
    if provider == "google":
        from google import genai
        return genai.Client(api_key=key)
    raise ValueError(f"unknown provider: {provider}")


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------

def _call_anthropic(client, model, system_text, standing_context, per_call_text, effort, max_tokens):
    thinking_style = PROVIDERS["anthropic"]["models"][model].get("thinking_style", "budget")
    extra_body = {"thinking": {"type": "disabled"}}
    if effort != "off":
        if thinking_style == "adaptive":
            # Newer models (e.g. Sonnet 5) don't take budget_tokens - effort is
            # a sibling top-level field, and "enabled" isn't a valid type for them.
            extra_body = {"thinking": {"type": "adaptive"}, "output_config": {"effort": effort}}
        else:
            budget = EFFORT_BUDGETS[effort]
            extra_body = {"thinking": {"type": "enabled", "budget_tokens": budget}}
            max_tokens = max(max_tokens, budget + 2048)
    # 1h TTL, not the 5min default: the read-review-edit-approve loop between
    # paragraphs is human-paced and routinely outlasts 5 minutes, which would
    # otherwise cool the cache and rewrite the brief/terms/rules/style at full
    # write cost (1.25x) with none of the read discount (~0.1x). The 1h write
    # premium (2x) is paid once; staying warm across a book-length session
    # more than covers it.
    cache_control = {"type": "ephemeral", "ttl": "1h"}
    content = []
    if standing_context:
        content.append({"type": "text", "text": standing_context, "cache_control": cache_control})
    content.append({"type": "text", "text": per_call_text})
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        extra_body=extra_body,
        system=[{"type": "text", "text": system_text, "cache_control": cache_control}],
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    u = resp.usage
    input_tokens = getattr(u, "input_tokens", 0) or 0
    cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
    non_cached = max(0, input_tokens - cache_read - cache_write)
    output_tokens = getattr(u, "output_tokens", 0) or 0
    return text, {
        "input": non_cached, "output": output_tokens,
        "cache_read": cache_read, "cache_write": cache_write,
    }


# --------------------------------------------------------------------------
# OpenAI (Responses API)
# --------------------------------------------------------------------------

def _call_openai(client, model, system_text, standing_context, per_call_text, effort, max_tokens):
    kwargs = dict(
        model=model,
        instructions=system_text,
        input="\n\n".join(p for p in (standing_context, per_call_text) if p),
        max_output_tokens=max_tokens,
        prompt_cache_key="palimpsest-standing-context",
    )
    if model_supports_effort_by_name(model) and effort != "off":
        kwargs["reasoning"] = {"effort": effort}
        kwargs["max_output_tokens"] = max(max_tokens, EFFORT_BUDGETS[effort] + 2048)
    resp = client.responses.create(**kwargs)
    text = (resp.output_text or "").strip()
    u = resp.usage
    input_tokens = getattr(u, "input_tokens", 0) or 0
    cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
    output_tokens = getattr(u, "output_tokens", 0) or 0
    return text, {
        "input": max(0, input_tokens - cached), "output": output_tokens,
        "cache_read": cached, "cache_write": 0,
    }


def model_supports_effort_by_name(model):
    return model_supports_effort("openai", model)


# --------------------------------------------------------------------------
# Google (Gemini)
# --------------------------------------------------------------------------

def _call_google(client, model, system_text, standing_context, per_call_text, effort, max_tokens):
    from google.genai import types
    config = types.GenerateContentConfig(
        system_instruction=system_text,
        max_output_tokens=max_tokens,
    )
    if model_supports_effort("google", model) and effort != "off":
        budget = EFFORT_BUDGETS[effort]
        config.thinking_config = types.ThinkingConfig(thinking_budget=budget)
        config.max_output_tokens = max(max_tokens, budget + 2048)
    resp = client.models.generate_content(
        model=model,
        contents="\n\n".join(p for p in (standing_context, per_call_text) if p),
        config=config,
    )
    text = (resp.text or "").strip()
    u = resp.usage_metadata
    prompt_tokens = getattr(u, "prompt_token_count", 0) or 0
    cached = getattr(u, "cached_content_token_count", 0) or 0
    output_tokens = getattr(u, "candidates_token_count", 0) or 0
    thoughts = getattr(u, "thoughts_token_count", 0) or 0
    return text, {
        "input": max(0, prompt_tokens - cached), "output": output_tokens + thoughts,
        "cache_read": cached, "cache_write": 0,
    }


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

_DISPATCH = {"anthropic": _call_anthropic, "openai": _call_openai, "google": _call_google}


def call(provider, model, system_text, standing_context, per_call_text, effort="off", max_tokens=8192):
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider: {provider}")
    if model not in PROVIDERS[provider]["models"]:
        raise ValueError(f"unknown model for {provider}: {model}")
    if effort not in EFFORT_LEVELS:
        effort = "off"
    if not model_supports_effort(provider, model):
        effort = "off"

    client = _client_for(provider)
    text, tokens = _DISPATCH[provider](client, model, system_text, standing_context, per_call_text, effort, max_tokens)
    cost = compute_cost(provider, model, tokens)
    return text, cost, tokens


def call_simple(provider, model, system_text, user_text, max_tokens=4096):
    """One-off, non-cached call (setup pass, glossary cleanup) - no standing/per-call split."""
    return call(provider, model, system_text, "", user_text, effort="off", max_tokens=max_tokens)


def compute_cost(provider, model, tokens):
    price = _price(provider, model)
    cost = (
        tokens["input"] / 1e6 * price["in"]
        + tokens["output"] / 1e6 * price["out"]
        + tokens.get("cache_read", 0) / 1e6 * price.get("cache_read", price["in"])
        + tokens.get("cache_write", 0) / 1e6 * price.get("cache_write", price["in"])
    )
    return round(cost, 6)
