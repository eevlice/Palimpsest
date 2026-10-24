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

import datetime
import math

import keys

EFFORT_LEVELS = ("off", "low", "medium", "high")

# Server-side ceiling on thinking/reasoning spend per effort tier - a client
# cannot get more than this no matter what it sends.
EFFORT_BUDGETS = {"low": 2000, "medium": 6000, "high": 16000}

# OpenAI's published long-context prices do not state the threshold. The code had no threshold before the
# GPT-6 catalog; 272k input tokens is ASSUMED here (the figure OpenAI printed for the GPT-5.4 generation).
# Change this one constant if the pricing page ever names a different one.
OPENAI_LONG_CONTEXT_OVER = 272_000

# Price fields are $/1M tokens, standard tier. Optional per-model keys:
#   legacy:             True = kept only so cost accounting and projects that already use the model keep working;
#                       pickers do not offer it (a project that already uses it still shows it, marked "eski").
#   long_context:       prices that replace the base ones when ONE request's total input (cache included) is larger
#                       than "over" (default: OPENAI_LONG_CONTEXT_OVER).
#   dated:              [(ISO date, {price overrides})] - from that date on the override applies (call date decides).
#   thinking_always_on: the API refuses thinking:disabled (checked against GET /v1/models capabilities.thinking);
#                       effort "off" is mapped to "low" and the UI hides the "off" step.
PROVIDERS = {
    "anthropic": {
        "label": "Claude",
        "effort_kind": "thinking_budget",
        "models": {
            # cache_write is priced for the 1h TTL (2x base input) to match the
            # ttl:"1h" set in _call_anthropic below.
            #
            # thinking_style:"adaptive" matters beyond labeling - budget_tokens
            # is flat-out removed (400) on Opus 5/4.8/4.7 and every 5.x model and deprecated on
            # Sonnet 4.6, so any model here without it falls through to the
            # old budget_tokens branch in _call_anthropic and 400s the moment
            # a paragraph is run with effort != "off".
            "claude-fable-5-1": {"label": "Fable 5.1", "in": 10.0, "out": 50.0, "cache_write": 20.0, "cache_read": 0.25, "effort": True, "thinking_style": "adaptive", "thinking_always_on": True},
            "claude-opus-5-5": {"label": "Opus 5.5", "in": 4.0, "out": 20.0, "cache_write": 8.0, "cache_read": 0.20, "effort": True, "thinking_style": "adaptive", "thinking_always_on": True},
            "claude-sonnet-5-5": {"label": "Sonnet 5.5", "in": 2.0, "out": 10.0, "cache_write": 4.0, "cache_read": 0.10, "effort": True, "thinking_style": "adaptive", "thinking_always_on": True},
            "claude-haiku-5-5": {"label": "Haiku 5.5", "in": 0.10, "out": 0.50, "cache_write": 0.20, "cache_read": 0.01, "effort": True, "thinking_style": "adaptive",
                                 "long_context": {"over": 100_000, "in": 0.50, "out": 2.50, "cache_write": 1.00, "cache_read": 0.05}},
            # --- legacy ---
            "claude-opus-5": {"label": "Opus 5", "legacy": True, "in": 5.0, "out": 25.0, "cache_write": 10.0, "cache_read": 0.50, "effort": True, "thinking_style": "adaptive"},
            "claude-opus-4-8": {"label": "Opus 4.8", "legacy": True, "in": 5.0, "out": 25.0, "cache_write": 10.0, "cache_read": 0.50, "effort": True, "thinking_style": "adaptive"},
            "claude-sonnet-5": {"label": "Sonnet 5", "legacy": True, "in": 2.0, "out": 10.0, "cache_write": 4.0, "cache_read": 0.20, "effort": True, "thinking_style": "adaptive"},
            "claude-sonnet-4-6": {"label": "Sonnet 4.6", "legacy": True, "in": 3.0, "out": 15.0, "cache_write": 6.0, "cache_read": 0.30, "effort": True, "thinking_style": "adaptive"},
            "claude-haiku-4-5-20251001": {"label": "Haiku 4.5", "legacy": True, "in": 1.0, "out": 5.0, "cache_write": 2.0, "cache_read": 0.10, "effort": True},
        },
    },
    "openai": {
        "label": "ChatGPT",
        "effort_kind": "reasoning_effort",
        "models": {
            "gpt-6-astra": {"label": "GPT-6 Astra", "in": 10.0, "out": 50.0, "cache_read": 1.00, "effort": True,
                            "long_context": {"in": 20.0, "out": 75.0, "cache_read": 2.00}},
            "gpt-6.1-sol": {"label": "GPT-6.1 Sol", "in": 2.0, "out": 10.0, "cache_read": 0.10, "effort": True,
                            "long_context": {"in": 4.0, "out": 15.0, "cache_read": 0.20}},
            "gpt-6-luna": {"label": "GPT-6 Luna", "in": 0.10, "out": 0.50, "cache_read": 0.01, "effort": True,
                           "long_context": {"in": 0.20, "out": 0.75, "cache_read": 0.02}},
            # --- legacy ---
            "gpt-5.6-sol": {"label": "GPT-5.6 Sol", "legacy": True, "in": 5.0, "out": 30.0, "cache_read": 0.50, "effort": True},
            "gpt-5.6-terra": {"label": "GPT-5.6 Terra", "legacy": True, "in": 2.5, "out": 15.0, "cache_read": 0.25, "effort": True},
            "gpt-5.6-luna": {"label": "GPT-5.6 Luna", "legacy": True, "in": 1.0, "out": 6.0, "cache_read": 0.10, "effort": True},
            "gpt-5.4-mini": {"label": "GPT-5.4 mini", "legacy": True, "in": 0.75, "out": 4.5, "cache_read": 0.075, "effort": False},
        },
    },
    "google": {
        "label": "Gemini",
        "effort_kind": "thinking_budget",
        "models": {
            "gemini-4-argon": {"label": "Gemini 4 Argon", "in": 2.0, "out": 10.0, "cache_read": 0.10, "effort": True,
                               "dated": [("2026-12-01", {"in": 4.0, "out": 20.0, "cache_read": 0.20})]},
            "gemini-3.8-flash": {"label": "Gemini 3.8 Flash", "in": 0.75, "out": 3.75, "cache_read": 0.075, "effort": True,
                                 "dated": [("2027-01-01", {"in": 1.50, "out": 7.50, "cache_read": 0.15})]},
            "gemini-3.5-flash-lite": {"label": "Gemini 3.5 Flash-Lite", "in": 0.30, "out": 2.50, "cache_read": 0.03, "effort": False},
            # --- legacy ---
            "gemini-3.1-pro": {"label": "Gemini 3.1 Pro", "legacy": True, "in": 2.0, "out": 12.0, "cache_read": 0.40, "effort": True},
            "gemini-3.5-flash": {"label": "Gemini 3.5 Flash", "legacy": True, "in": 1.5, "out": 9.0, "cache_read": 0.30, "effort": True},
            "gemini-3-flash-lite": {"label": "Gemini 3 Flash-Lite", "legacy": True, "in": 0.10, "out": 0.40, "cache_read": 0.02, "effort": False},
        },
    },
}


def _cost_level_fn():
    """Returns a fn mapping a model's blended $/1M cost to a 1-5 tier, spread log-scale across the CURRENT (non-legacy)
    catalog of every provider - linear buckets are useless since the cheapest and priciest models are ~100x apart.
    Uses today's short-context prices; recomputed from PROVIDERS so it never drifts from a price edited above."""
    def blend(provider, mid):
        p = _price(provider, mid)
        return (p["in"] + p["out"]) / 2
    avgs = [blend(pid, mid) for pid, p in PROVIDERS.items() for mid, m in p["models"].items() if not m.get("legacy")]
    lo, hi = math.log(min(avgs)), math.log(max(avgs))
    span = (hi - lo) or 1.0
    def level(provider, mid):
        t = (math.log(blend(provider, mid)) - lo) / span
        return max(1, min(5, 1 + round(4 * t)))
    return level


def list_catalog():
    """[{provider, label, models:[{id, label, effort, effort_min, legacy, cost_level}]}] - for the frontend model picker.
    effort_min is the lowest effort level the model can run at ("off" unless thinking cannot be disabled)."""
    cost_level = _cost_level_fn()
    out = []
    for pid, p in PROVIDERS.items():
        out.append({
            "provider": pid,
            "label": p["label"],
            "has_key": bool(keys.get_key(pid)),
            "models": [
                {"id": mid, "label": m["label"], "effort": m["effort"],
                 "effort_min": "low" if m.get("thinking_always_on") else "off",
                 "legacy": bool(m.get("legacy")), "cost_level": cost_level(pid, mid)}
                for mid, m in p["models"].items()
            ],
        })
    return out


def model_supports_effort(provider, model):
    m = PROVIDERS.get(provider, {}).get("models", {}).get(model)
    return bool(m and m["effort"])


def _price(provider, model, when=None, input_total=0):
    """Effective prices for one call: the model's base prices, then any dated change that has taken effect on `when`
    (default today), then the long-context prices if this single request's total input (cache included) is over the threshold."""
    m = PROVIDERS[provider]["models"][model]
    p = {k: m[k] for k in ("in", "out", "cache_write", "cache_read") if k in m}
    day = when or datetime.date.today()
    for start, over in sorted(m.get("dated", [])):
        if day >= datetime.date.fromisoformat(start):
            p.update(over)
    lc = m.get("long_context")
    if lc and input_total > lc.get("over", OPENAI_LONG_CONTEXT_OVER):
        p.update({k: v for k, v in lc.items() if k != "over"})
    return p


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


def _stop_info(stop_reason, output_tokens, cap):
    """The two fields every provider adds to its token dict. `truncated` is True when the model
    stopped because it ran out of output budget - either the provider said so, or it spent
    the whole cap. Existing callers only read input/output/cache_*, so extra keys are harmless."""
    stop = str(stop_reason).lower() if stop_reason is not None else None
    hit = bool(stop and ("max_tokens" in stop or "max_output_tokens" in stop or "length" in stop))
    return {"stop_reason": stop, "truncated": hit or (cap is not None and output_tokens >= cap)}


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------

def _call_anthropic(client, model, system_text, standing_context, per_call_text, effort, max_tokens):
    info = PROVIDERS["anthropic"]["models"][model]
    thinking_style = info.get("thinking_style", "budget")
    if info.get("thinking_always_on") and effort == "off":
        effort = "low"          # these models reject thinking:disabled; "low" is the lowest level they run at
    extra_body = {"thinking": {"type": "disabled"}}
    if effort != "off":
        if thinking_style == "adaptive":
            # Newer models (e.g. Sonnet 5) don't take budget_tokens - effort is
            # a sibling top-level field, and "enabled" isn't a valid type for them.
            extra_body = {"thinking": {"type": "adaptive"}, "output_config": {"effort": effort}}
            # thinking tokens are spent out of max_tokens: leave room so the visible answer keeps its full cap
            max_tokens += EFFORT_BUDGETS[effort]
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
        **_stop_info(getattr(resp, "stop_reason", None), output_tokens, max_tokens),
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
    status_reason = getattr(getattr(resp, "incomplete_details", None), "reason", None)
    return text, {
        "input": max(0, input_tokens - cached), "output": output_tokens,
        "cache_read": cached, "cache_write": 0,
        **_stop_info(status_reason, output_tokens, kwargs["max_output_tokens"]),
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
    cands = getattr(resp, "candidates", None) or []
    finish = getattr(cands[0], "finish_reason", None) if cands else None
    finish = getattr(finish, "name", finish)
    return text, {
        "input": max(0, prompt_tokens - cached), "output": output_tokens + thoughts,
        "cache_read": cached, "cache_write": 0,
        **_stop_info(finish, output_tokens + thoughts, config.max_output_tokens),
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


def compute_cost(provider, model, tokens, when=None):
    input_total = (tokens.get("input", 0) or 0) + (tokens.get("cache_read", 0) or 0) + (tokens.get("cache_write", 0) or 0)
    price = _price(provider, model, when, input_total)
    cost = (
        tokens["input"] / 1e6 * price["in"]
        + tokens["output"] / 1e6 * price["out"]
        + tokens.get("cache_read", 0) / 1e6 * price.get("cache_read", price["in"])
        + tokens.get("cache_write", 0) / 1e6 * price.get("cache_write", price["in"])
    )
    return round(cost, 6)
