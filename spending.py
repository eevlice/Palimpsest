#!/usr/bin/env python3
"""
Append-only spending log across all projects and providers.

~/.palimpsest/spending.jsonl - one JSON object per line, one line per API call.
Lives next to keys.py's config dir so it survives project re-clones too.
"""

import os
import json
import datetime

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".palimpsest")
LOG_FILE = os.path.join(CONFIG_DIR, "spending.jsonl")

MAX_RECENT = 500


def log_call(project_id, project_name, provider, model, effort, tokens, cost):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    entry = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "project_id": project_id,
        "project_name": project_name,
        "provider": provider,
        "model": model,
        "effort": effort,
        "tokens": tokens,
        "cost": cost,
    }
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_all():
    if not os.path.exists(LOG_FILE):
        return []
    out = []
    with open(LOG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def summary():
    """Aggregated totals by (provider, model), plus a grand total and recent calls."""
    rows = _read_all()
    by_model = {}
    total_cost = 0.0
    total_calls = 0
    for r in rows:
        key = (r.get("provider", "?"), r.get("model", "?"))
        agg = by_model.setdefault(key, {
            "provider": key[0], "model": key[1], "calls": 0, "cost": 0.0,
            "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
        })
        agg["calls"] += 1
        agg["cost"] += r.get("cost", 0) or 0
        tk = r.get("tokens") or {}
        agg["input"] += tk.get("input", 0) or 0
        agg["output"] += tk.get("output", 0) or 0
        agg["cache_read"] += tk.get("cache_read", 0) or 0
        agg["cache_write"] += tk.get("cache_write", 0) or 0
        total_cost += r.get("cost", 0) or 0
        total_calls += 1

    by_model_list = sorted(by_model.values(), key=lambda x: x["cost"], reverse=True)
    for row in by_model_list:
        row["cost"] = round(row["cost"], 6)

    return {
        "total_cost": round(total_cost, 6),
        "total_calls": total_calls,
        "by_model": by_model_list,
        "recent": rows[-MAX_RECENT:][::-1],
    }
