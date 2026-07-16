#!/usr/bin/env python3
"""
Palimpsest — a local, self-contained CAT tool built on the self-refine loop.

Runs entirely on your own machine. Your book text and your API key never
leave your computer except for the calls to the translation API itself.

Setup:
    pip install flask anthropic python-docx
    Put your API key in key.txt in this folder.

Run:
    Double-click start.command (Mac) or start.bat (Windows).
    Or: python server.py
"""

import os
import re
import json
import glob
import datetime
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder=".")

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECTS_DIR = os.path.join(HERE, "projects")
os.makedirs(PROJECTS_DIR, exist_ok=True)

MODELS = {
    "Opus 4.8": "claude-opus-4-8",
    "Sonnet 4.6": "claude-sonnet-4-6",
    "Haiku 4.5": "claude-haiku-4-5-20251001",
}
PRICES = {
    "claude-opus-4-8":          {"in": 5.0,  "out": 25.0, "cache_write": 6.25, "cache_read": 0.50},
    "claude-sonnet-4-6":        {"in": 3.0,  "out": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5-20251001":{"in": 1.0,  "out": 5.0,  "cache_write": 1.25, "cache_read": 0.10},
}
DEFAULT_MODEL = "claude-sonnet-4-6"


# --------------------------------------------------------------------------
# API key
# --------------------------------------------------------------------------

def load_api_key():
    key_file = os.path.join(HERE, "key.txt")
    if os.path.exists(key_file):
        with open(key_file, "r", encoding="utf-8") as f:
            k = f.read().strip()
            if k:
                return k
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def get_client():
    from anthropic import Anthropic
    key = load_api_key()
    if not key:
        raise RuntimeError("no key")
    return Anthropic(api_key=key)


# --------------------------------------------------------------------------
# Cached call: splits standing context (cacheable) from per-segment content.
# --------------------------------------------------------------------------

def call_cached(client, model, system_text, standing_context, per_call_text, max_tokens=8192):
    """
    Make an API call with prompt caching.
    - system_text: the role instruction (cached)
    - standing_context: synopsis, glossary, rules, style (cached)
    - per_call_text: neighbors, memory, the segment itself (NOT cached)
    """
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"}
        }],
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": standing_context,
                    "cache_control": {"type": "ephemeral"}
                },
                {
                    "type": "text",
                    "text": per_call_text
                }
            ]
        }]
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    usage = resp.usage
    return text, usage


def call_simple(client, model, system, user, max_tokens=8192):
    """Non-cached call for setup pass."""
    resp = client.messages.create(
        model=model, max_tokens=max_tokens,
        system=system, messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return text, resp.usage.input_tokens, resp.usage.output_tokens


def compute_cost(usage, model):
    """Compute cost from usage object, accounting for cache hits."""
    price = PRICES.get(model, PRICES[DEFAULT_MODEL])
    input_tokens = getattr(usage, 'input_tokens', 0) or 0
    output_tokens = getattr(usage, 'output_tokens', 0) or 0
    cache_read = getattr(usage, 'cache_read_input_tokens', 0) or 0
    cache_write = getattr(usage, 'cache_creation_input_tokens', 0) or 0
    # input_tokens from the API includes non-cached input; cache_read/write are separate
    non_cached_input = input_tokens - cache_read - cache_write
    if non_cached_input < 0:
        non_cached_input = 0
    cost = (non_cached_input / 1e6 * price["in"]
            + cache_read / 1e6 * price["cache_read"]
            + cache_write / 1e6 * price["cache_write"]
            + output_tokens / 1e6 * price["out"])
    return round(cost, 6), {
        "input": non_cached_input, "output": output_tokens,
        "cache_read": cache_read, "cache_write": cache_write
    }


# --------------------------------------------------------------------------
# File reading + segmentation
# --------------------------------------------------------------------------

def extract_text(filename, raw_bytes):
    ext = filename.lower().rsplit(".", 1)[-1]
    if ext in ("txt", "md"):
        return raw_bytes.decode("utf-8", errors="replace")
    if ext == "docx":
        import io
        from docx import Document
        doc = Document(io.BytesIO(raw_bytes))
        return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
    raise ValueError(f"Unsupported file type: .{ext}")


def segment(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


# --------------------------------------------------------------------------
# One-time whole-book context pass (no caching needed — runs once)
# --------------------------------------------------------------------------

def condense_brief(client, model, synopsis):
    """Compress the brief to minimum tokens without losing any instruction.
    Runs once at setup; saves tokens on every later translation call."""
    if not synopsis or len(synopsis) < 200:
        return synopsis, 0, 0
    system = ("You compress translation briefs to the fewest words possible "
              "while preserving every instruction, nuance, and stylistic note. "
              "Telegraphic phrasing is fine. Drop nothing meaningful.")
    user = ("Condense this translation brief to roughly half its length or less. "
            "Keep every distinct instruction about voice, tone, register, POV, and "
            "style. Output ONLY the condensed brief.\n\n" + synopsis)
    try:
        text, ti, to = call_simple(client, model, system, user, max_tokens=1024)
        return text, ti, to
    except Exception:
        return synopsis, 0, 0


def build_context(client, model, src, tgt, full_text):
    system = (
        "You are a senior literary translator preparing to translate a book. "
        "Before translating, you read the whole text and produce a working brief."
    )
    sample = full_text
    if len(full_text) > 120000:
        third = len(full_text) // 3
        sample = (full_text[:40000] + "\n\n[...]\n\n"
                  + full_text[third:third + 40000] + "\n\n[...]\n\n"
                  + full_text[-40000:])
    user = (
        f"This is a book to be translated from {src} into {tgt}. Read it and "
        f"produce two things, clearly separated:\n\n"
        f"=== SYNOPSIS ===\nA concise brief: genre, narrative voice, point of "
        f"view, tone, register, and stylistic features a translator must "
        f"preserve. 150-250 words.\n\n"
        f"=== TERM SHEET ===\n"
        f"A glossary of ONLY terms where a translation DECISION exists and "
        f"consistency across the book matters. Include: invented or culture-specific "
        f"terms, recurring motifs/refrains, titles and forms of address/honorifics, "
        f"terms with an established {tgt} convention, and names that change FORM in "
        f"{tgt} (transliteration, declension, vowel harmony). "
        f"EXCLUDE: any name or word that renders identically in {tgt} (do NOT list "
        f"X = X), one-off proper nouns that never recur, and anything mechanical. "
        f"If a term needs no decision, omit it entirely. "
        f"Reason specifically about the {src}->{tgt} direction and the book's genre. "
        f"Format one per line as `source = ` leaving the target BLANK for the "
        f"translator to decide, unless a {tgt} rendering is strongly conventional. "
        f"Prefer a short, high-value list over an exhaustive one.\n\n"
        f"BOOK:\n{sample}"
    )
    return call_simple(client, model, system, user, max_tokens=4096)


def split_context(blob):
    syn, terms = blob, ""
    m = re.search(r"===\s*TERM SHEET\s*===", blob, re.I)
    if m:
        syn, terms = blob[:m.start()], blob[m.end():]
    syn = re.sub(r"===\s*SYNOPSIS\s*===", "", syn, flags=re.I).strip()
    return syn, terms.strip()


# --------------------------------------------------------------------------
# Per-segment translation (single pass, with caching)
# --------------------------------------------------------------------------

def build_standing_context(synopsis, terms, rules, style_sample):
    """The part that stays IDENTICAL across calls — cacheable."""
    parts = []
    if synopsis: parts.append(f"PROJECT BRIEF:\n{synopsis}")
    if terms: parts.append(f"TERM SHEET (translate these consistently):\n{terms}")
    if rules: parts.append(f"RULES (follow strictly, no exceptions):\n{rules}")
    if style_sample: parts.append(f"STYLE TO MATCH:\n{style_sample}")
    return "\n\n".join(parts) if parts else "(no project context provided)"


def build_per_call(src, tgt, text, before, after, memory):
    """The part that changes per paragraph — NOT cached."""
    parts = []
    if memory: parts.append(f"EARLIER APPROVED TRANSLATIONS (match these):\n{memory}")
    if before: parts.append(f"PRECEDING TEXT (context only, do NOT translate):\n{before}")
    if after: parts.append(f"FOLLOWING TEXT (context only, do NOT translate):\n{after}")
    parts.append(
        f"Translate ONLY the SEGMENT below into {tgt}. "
        f"Follow the brief, term sheet, rules, and style. "
        f"Output ONLY the translation.\n\nSEGMENT:\n{text}"
    )
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/models")
def models():
    return jsonify(list(MODELS.keys()))


@app.route("/key_status")
def key_status():
    return jsonify({"has_key": bool(load_api_key())})


@app.route("/projects")
def list_projects():
    out = []
    for p in sorted(glob.glob(os.path.join(PROJECTS_DIR, "*.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            done = sum(1 for x in d.get("finals", []) if x)
            out.append({"id": d.get("id"), "name": d.get("name", d.get("id")),
                        "count": len(d.get("segments", [])), "done": done,
                        "updated": d.get("updated", "")})
        except Exception:
            continue
    out.sort(key=lambda x: x.get("updated", ""), reverse=True)
    return jsonify(out)


@app.route("/project/<pid>")
def load_project(pid):
    p = project_path(pid)
    if not os.path.exists(p):
        return jsonify({"error": "Project not found."}), 404
    with open(p, encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/save", methods=["POST"])
def save_project():
    d = request.get_json()
    if not d.get("id"):
        return jsonify({"error": "missing id"}), 400
    d["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(project_path(d["id"]), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "updated": d["updated"]})


@app.route("/rename", methods=["POST"])
def rename_project():
    d = request.get_json()
    pid, name = d.get("id"), (d.get("name") or "").strip()
    if not pid or not name:
        return jsonify({"error": "missing id or name"}), 400
    p = project_path(pid)
    if not os.path.exists(p):
        return jsonify({"error": "Project not found."}), 404
    with open(p, encoding="utf-8") as f:
        proj = json.load(f)
    proj["name"] = name
    proj["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True})


@app.route("/duplicate", methods=["POST"])
def duplicate_project():
    d = request.get_json()
    pid = d.get("id")
    p = project_path(pid)
    if not os.path.exists(p):
        return jsonify({"error": "Project not found."}), 404
    with open(p, encoding="utf-8") as f:
        proj = json.load(f)
    proj["id"] = "p_" + str(int(datetime.datetime.now().timestamp() * 1000))
    proj["name"] = proj.get("name", "Untitled") + " (copy)"
    proj["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(project_path(proj["id"]), "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "id": proj["id"]})


@app.route("/delete", methods=["POST"])
def delete_project():
    d = request.get_json()
    pid = d.get("id")
    p = project_path(pid)
    if not os.path.exists(p):
        return jsonify({"error": "Project not found."}), 404
    trash = os.path.join(PROJECTS_DIR, "_trash")
    os.makedirs(trash, exist_ok=True)
    os.replace(p, os.path.join(trash, os.path.basename(p)))
    return jsonify({"ok": True})


@app.route("/setup", methods=["POST"])
def setup():
    f = request.files.get("file")
    src = request.form.get("source_lang", "English")
    tgt = request.form.get("target_lang", "Turkish")
    model = MODELS.get(request.form.get("model"), DEFAULT_MODEL)
    if not f:
        return jsonify({"error": "No file received."}), 400
    try:
        text = extract_text(f.filename, f.read())
    except Exception as e:
        return jsonify({"error": f"Could not read the file: {e}"}), 400
    segs = segment(text)
    if not segs:
        return jsonify({"error": "No text found in the file."}), 400
    try:
        client = get_client()
    except Exception:
        return jsonify({"error": "No API key. Put your key in key.txt and restart."}), 500
    try:
        blob, ti, to = build_context(client, model, src, tgt, text)
        synopsis, terms = split_context(blob)
        # Condense the brief once now, to cut tokens on every later call.
        condensed, ti2, to2 = condense_brief(client, model, synopsis)
        price = PRICES.get(model, PRICES[DEFAULT_MODEL])
        cost = (ti + ti2) / 1e6 * price["in"] + (to + to2) / 1e6 * price["out"]
        return jsonify({"segments": segs, "synopsis": condensed, "terms": terms,
                        "setup_cost": round(cost, 4), "count": len(segs)})
    except Exception as e:
        return jsonify({"error": f"Context pass failed: {e}"}), 500


@app.route("/clean_glossary", methods=["POST"])
def clean_glossary():
    """Re-filter an existing glossary: drop X=X identities, one-offs, and noise.
    Driven by language pair and genre. Returns a cleaned term list."""
    d = request.get_json()
    model = MODELS.get(d.get("model"), DEFAULT_MODEL)
    src, tgt = d.get("source_lang", "English"), d.get("target_lang", "Turkish")
    terms = (d.get("terms") or "").strip()
    synopsis = (d.get("synopsis") or "").strip()
    if not terms:
        return jsonify({"terms": "", "cost": 0})
    try:
        client = get_client()
    except Exception:
        return jsonify({"error": "No API key. Put your key in key.txt and restart."}), 500
    try:
        system = (
            "You are a translation glossary editor. You ruthlessly prune glossaries "
            "down to only entries that represent a real, consistency-critical "
            f"translation decision in the {src}->{tgt} direction."
        )
        user = (
            f"Book context (for genre/register):\n{synopsis or '(none given)'}\n\n"
            f"Here is a raw {src}->{tgt} glossary. Clean it:\n"
            f"- REMOVE any entry that renders identically in {tgt} (X = X).\n"
            f"- REMOVE one-off proper nouns with no recurring significance.\n"
            f"- REMOVE mechanical or trivial entries.\n"
            f"- KEEP invented terms, motifs, honorifics/forms of address, "
            f"culture-specific terms, and names that change FORM in {tgt}.\n"
            f"- Preserve any target renderings already filled in.\n"
            f"- Sort alphabetically by source term.\n"
            f"Output ONLY the cleaned glossary, one entry per line as `source = target` "
            f"(target may be blank). No commentary.\n\n"
            f"GLOSSARY:\n{terms}"
        )
        text, ti, to = call_simple(client, model, system, user, max_tokens=2048)
        price = PRICES.get(model, PRICES[DEFAULT_MODEL])
        cost = ti / 1e6 * price["in"] + to / 1e6 * price["out"]
        return jsonify({"terms": text.strip(), "cost": round(cost, 4)})
    except Exception as e:
        return jsonify({"error": f"Cleanup failed: {e}"}), 500


@app.route("/translate_simple", methods=["POST"])
def translate_simple():
    """Single-pass translation with prompt caching."""
    d = request.get_json()
    model = MODELS.get(d.get("model"), DEFAULT_MODEL)
    src, tgt = d.get("source_lang", "English"), d.get("target_lang", "Turkish")
    text = d.get("text", "").strip()
    if not text:
        return jsonify({"error": "Empty segment."}), 400
    try:
        client = get_client()
    except Exception:
        return jsonify({"error": "No API key. Put your key in key.txt and restart."}), 500
    try:
        system_text = (
            f"You are a professional literary translator from {src} into {tgt}. "
            f"Translate within the book's context. Follow all rules and the term sheet exactly."
        )
        standing = build_standing_context(
            d.get("synopsis", ""), d.get("terms", ""),
            d.get("rules", ""), d.get("style_sample", ""))
        per_call = build_per_call(
            src, tgt, text,
            d.get("before", ""), d.get("after", ""),
            d.get("memory", ""))

        result, usage = call_cached(client, model, system_text, standing, per_call)
        cost, token_detail = compute_cost(usage, model)

        return jsonify({
            "final": result, "cost": cost,
            "tokens": token_detail
        })
    except Exception as e:
        return jsonify({"error": f"Translation failed: {e}"}), 500


def project_path(pid):
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", pid)[:80]
    return os.path.join(PROJECTS_DIR, safe + ".json")


if __name__ == "__main__":
    import webbrowser, threading
    if not load_api_key():
        print("\n  ! No API key found.")
        print("  ! Put your Anthropic key in key.txt in this folder,")
        print("  ! then run this again.\n")
    else:
        print("\n  Palimpsest is running.")
        print("  Open  http://localhost:5001  in your browser (opening it for you now).\n")
        threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5001")).start()
    app.run(port=5001, debug=False)
