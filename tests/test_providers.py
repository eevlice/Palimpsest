"""Run: python3 -I tests/test_providers.py   (pure arithmetic on the catalog; no network)"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import providers as P

failed = 0


def check(name, cond, extra=""):
    global failed
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        failed += 1


def cost(pid, mid, i, o, cr=0, cw=0, when=None):
    return P.compute_cost(pid, mid, {"input": i, "output": o, "cache_read": cr, "cache_write": cw}, when)


approx = lambda a, b: abs(a - b) < 6e-7      # compute_cost rounds to 6 decimals
D = datetime.date

# new catalog: [in, cache write, cache read, out]
for mid, want in {"claude-fable-5-1": (10, 20, 0.25, 50), "claude-opus-5-5": (4, 8, 0.20, 20), "claude-sonnet-5-5": (2, 4, 0.10, 10)}.items():
    p = P._price("anthropic", mid)
    check(f"{mid} prices", (p["in"], p["cache_write"], p["cache_read"], p["out"]) == want, p)
# legacy Anthropic prices as published
for mid, want in {"claude-opus-5": (5, 10, 0.50, 25), "claude-opus-4-8": (5, 10, 0.50, 25), "claude-sonnet-5": (2, 4, 0.20, 10),
                  "claude-sonnet-4-6": (3, 6, 0.30, 15), "claude-haiku-4-5-20251001": (1, 2, 0.10, 5)}.items():
    p = P._price("anthropic", mid)
    check(f"legacy {mid} prices", (p["in"], p["cache_write"], p["cache_read"], p["out"]) == want and P.PROVIDERS["anthropic"]["models"][mid]["legacy"], p)

# Haiku 5.5: the threshold is the request's whole input, cache included, and the higher rate applies to everything
check("haiku 5.5 at 100k input is the low tier", approx(cost("anthropic", "claude-haiku-5-5", 100_000, 2000), 100_000 * 0.10 / 1e6 + 2000 * 0.50 / 1e6))
check("haiku 5.5 over 100k is the high tier for input AND output", approx(cost("anthropic", "claude-haiku-5-5", 120_000, 2000), 120_000 * 0.50 / 1e6 + 2000 * 2.50 / 1e6))
check("haiku 5.5 threshold counts cache tokens", approx(cost("anthropic", "claude-haiku-5-5", 20_000, 0, cr=100_000), 20_000 * 0.50 / 1e6 + 100_000 * 0.05 / 1e6))
check("haiku 5.5 high tier cache write", approx(cost("anthropic", "claude-haiku-5-5", 1000, 0, cw=120_000), 1000 * 0.50 / 1e6 + 120_000 * 1.00 / 1e6))

# OpenAI long context
for mid, base, long_ in (("gpt-6-astra", (10, 50), (20, 75)), ("gpt-6.1-sol", (2, 10), (4, 15)), ("gpt-6-luna", (0.10, 0.50), (0.20, 0.75))):
    t = P.OPENAI_LONG_CONTEXT_OVER
    check(f"{mid} short context", approx(cost("openai", mid, t, 1000), (t * base[0] + 1000 * base[1]) / 1e6))
    check(f"{mid} long context", approx(cost("openai", mid, t + 1, 1000), ((t + 1) * long_[0] + 1000 * long_[1]) / 1e6))
check("openai long-context cached read price", approx(cost("openai", "gpt-6-astra", 1000, 0, cr=300_000), (1000 * 20 + 300_000 * 2.00) / 1e6))

# dated Google prices: the call date decides
a = lambda d: cost("google", "gemini-4-argon", 30_000, 2_000, when=d)
check("argon before 1 Dec 2026", approx(a(D(2026, 11, 30)), (30_000 * 2 + 2_000 * 10) / 1e6))
check("argon from 1 Dec 2026", approx(a(D(2026, 12, 1)), (30_000 * 4 + 2_000 * 20) / 1e6))
f = lambda d: cost("google", "gemini-3.8-flash", 30_000, 2_000, cr=10_000, when=d)
check("3.8 flash before 1 Jan 2027", approx(f(D(2026, 12, 31)), (30_000 * 0.75 + 2_000 * 3.75 + 10_000 * 0.075) / 1e6))
check("3.8 flash from 1 Jan 2027", approx(f(D(2027, 1, 1)), (30_000 * 1.5 + 2_000 * 7.5 + 10_000 * 0.15) / 1e6))
check("flash-lite has no dated change", approx(cost("google", "gemini-3.5-flash-lite", 1000, 1000, when=D(2030, 1, 1)), (1000 * 0.30 + 1000 * 2.50) / 1e6))
check("today's price is used when no date is given", approx(cost("google", "gemini-4-argon", 1000, 1000), cost("google", "gemini-4-argon", 1000, 1000, when=datetime.date.today())))

# catalog flags
cat = {m["id"]: m for p in P.list_catalog() for m in p["models"]}
check("every old model is still in the catalog and marked legacy",
      all(cat[m]["legacy"] for m in ("claude-opus-5", "claude-opus-4-8", "claude-sonnet-5", "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
                                     "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.4-mini", "gemini-3.1-pro", "gemini-3.5-flash", "gemini-3-flash-lite")))
check("new models are not legacy", not any(cat[m]["legacy"] for m in ("claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5",
                                                                     "gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna", "gemini-4-argon", "gemini-3.8-flash", "gemini-3.5-flash-lite")))
check("models that cannot run without thinking have effort_min low", [m for m in cat if cat[m]["effort_min"] == "low"] == ["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5"])
check("every new Claude model is adaptive", all(P.PROVIDERS["anthropic"]["models"][m].get("thinking_style") == "adaptive"
                                               for m in ("claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")))
cur = [cat[m]["cost_level"] for m in cat if not cat[m]["legacy"]]
check("cost levels span 1..5 over the current catalog", min(cur) == 1 and max(cur) == 5 and all(1 <= v <= 5 for v in (c["cost_level"] for c in cat.values())), cur)
check("cost level is monotone in price", cat["claude-haiku-5-5"]["cost_level"] <= cat["claude-sonnet-5-5"]["cost_level"] <= cat["claude-opus-5-5"]["cost_level"] <= cat["claude-fable-5-1"]["cost_level"])

# request building for always-on thinking models (stub client, no network)
class O:
    def __init__(s, **k): s.__dict__.update(k)
seen = {}
class C:
    class messages:
        @staticmethod
        def create(**kw):
            seen.clear(); seen.update(kw)
            return O(content=[O(type="text", text="x")], stop_reason="end_turn",
                     usage=O(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0))
P._call_anthropic(C, "claude-opus-5-5", "s", "", "u", "off", 4096)
check("opus 5.5 effort off is sent as adaptive/low (thinking cannot be disabled)", seen["extra_body"] == {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}, seen["extra_body"])
check("thinking headroom is added to max_tokens", seen["max_tokens"] == 4096 + P.EFFORT_BUDGETS["low"], seen["max_tokens"])
P._call_anthropic(C, "claude-haiku-5-5", "s", "", "u", "off", 4096)
check("haiku 5.5 can still run with thinking disabled", seen["extra_body"] == {"thinking": {"type": "disabled"}} and seen["max_tokens"] == 4096)
P._call_anthropic(C, "claude-opus-5", "s", "", "u", "off", 4096)
check("legacy opus 5 keeps thinking disabled at effort off", seen["extra_body"] == {"thinking": {"type": "disabled"}})

print("FAILED %d" % failed if failed else "ALL PASS")
sys.exit(1 if failed else 0)
