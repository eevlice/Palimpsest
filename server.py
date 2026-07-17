#!/usr/bin/env python3
"""
Palimpsest — a local, self-contained CAT tool built on the self-refine loop.

Runs entirely on your own machine. Your book text and your API keys never
leave your computer except for the calls to the translation API itself.

Setup:
    pip install flask anthropic openai google-genai python-docx cryptography
    Add your API key(s) from the app's Settings screen (or, for Anthropic
    only, put a key in key.txt in this folder for backward compatibility).

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

import keys
import spending
import providers
import prefs

app = Flask(__name__, static_folder=".")

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECTS_DIR = os.path.join(HERE, "projects")
os.makedirs(PROJECTS_DIR, exist_ok=True)

DEFAULT_PROVIDER, DEFAULT_MODEL = "anthropic", "claude-sonnet-4-6"


def parse_model_key(s):
    """'provider::model_id' -> (provider, model_id), falling back to the default."""
    if s and "::" in s:
        provider, model = s.split("::", 1)
        if provider in providers.PROVIDERS and model in providers.PROVIDERS[provider]["models"]:
            return provider, model
    return DEFAULT_PROVIDER, DEFAULT_MODEL


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

def condense_brief(provider, model, synopsis):
    """Compress the brief to minimum tokens without losing any instruction.
    Runs once at setup; saves tokens on every later translation call."""
    if not synopsis or len(synopsis) < 200:
        return synopsis, None
    system = ("You compress translation briefs to the fewest words possible "
              "while preserving every instruction, nuance, and stylistic note. "
              "Telegraphic phrasing is fine. Drop nothing meaningful.")
    user = ("Condense this translation brief to roughly half its length or less. "
            "Keep every distinct instruction about voice, tone, register, POV, and "
            "style. Output ONLY the condensed brief.\n\n" + synopsis)
    try:
        text, cost, tokens = providers.call_simple(provider, model, system, user, max_tokens=1024)
        return text, (cost, tokens)
    except Exception:
        return synopsis, None


def build_context(provider, model, src, tgt, full_text):
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
    return providers.call_simple(provider, model, system, user, max_tokens=4096)


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
    return jsonify(providers.list_catalog())


@app.route("/keys/status")
def keys_status():
    return jsonify(keys.key_status())


@app.route("/keys", methods=["POST"])
def keys_set():
    d = request.get_json()
    provider = d.get("provider")
    if provider not in providers.PROVIDERS:
        return jsonify({"error": "Unknown provider."}), 400
    keys.set_key(provider, d.get("api_key", ""))
    return jsonify({"ok": True, "has_key": bool(keys.get_key(provider))})


@app.route("/keys/clear", methods=["POST"])
def keys_clear():
    d = request.get_json()
    provider = d.get("provider")
    if provider not in providers.PROVIDERS:
        return jsonify({"error": "Unknown provider."}), 400
    keys.clear_key(provider)
    return jsonify({"ok": True})


@app.route("/spending")
def spending_summary():
    return jsonify(spending.summary())


@app.route("/prefs")
def prefs_get():
    return jsonify(prefs.get_all())


@app.route("/prefs", methods=["POST"])
def prefs_set():
    patch = request.get_json() or {}
    return jsonify(prefs.update(patch))


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
    project_name = request.form.get("name", "")
    provider, model = parse_model_key(request.form.get("model"))
    if not f:
        return jsonify({"error": "No file received."}), 400
    try:
        text = extract_text(f.filename, f.read())
    except Exception as e:
        return jsonify({"error": f"Could not read the file: {e}"}), 400
    segs = segment(text)
    if not segs:
        return jsonify({"error": "No text found in the file."}), 400
    if not keys.get_key(provider):
        return jsonify({"error": f"No API key for {provider}. Add one from Settings."}), 500
    try:
        blob, cost1, tokens1 = build_context(provider, model, src, tgt, text)
        synopsis, terms = split_context(blob)
        spending.log_call("", project_name, provider, model, "off", tokens1, cost1)
        # Condense the brief once now, to cut tokens on every later call.
        condensed, condense_info = condense_brief(provider, model, synopsis)
        total_cost = cost1
        if condense_info:
            cost2, tokens2 = condense_info
            spending.log_call("", project_name, provider, model, "off", tokens2, cost2)
            total_cost += cost2
        return jsonify({"segments": segs, "synopsis": condensed, "terms": terms,
                        "setup_cost": round(total_cost, 4), "count": len(segs)})
    except Exception as e:
        return jsonify({"error": f"Context pass failed: {e}"}), 500


@app.route("/clean_glossary", methods=["POST"])
def clean_glossary():
    """Re-filter an existing glossary: drop X=X identities, one-offs, and noise.
    Driven by language pair and genre. Returns a cleaned term list."""
    d = request.get_json()
    provider, model = parse_model_key(d.get("model"))
    src, tgt = d.get("source_lang", "English"), d.get("target_lang", "Turkish")
    terms = (d.get("terms") or "").strip()
    synopsis = (d.get("synopsis") or "").strip()
    project_id, project_name = d.get("id", ""), d.get("name", "")
    if not terms:
        return jsonify({"terms": "", "cost": 0})
    if not keys.get_key(provider):
        return jsonify({"error": f"No API key for {provider}. Add one from Settings."}), 500
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
        text, cost, tokens = providers.call_simple(provider, model, system, user, max_tokens=2048)
        spending.log_call(project_id, project_name, provider, model, "off", tokens, cost)
        return jsonify({"terms": text.strip(), "cost": round(cost, 4)})
    except Exception as e:
        return jsonify({"error": f"Cleanup failed: {e}"}), 500


@app.route("/translate_simple", methods=["POST"])
def translate_simple():
    """Single-pass translation with prompt caching."""
    d = request.get_json()
    provider, model = parse_model_key(d.get("model"))
    src, tgt = d.get("source_lang", "English"), d.get("target_lang", "Turkish")
    text = d.get("text", "").strip()
    effort = d.get("effort", "off")
    project_id, project_name = d.get("id", ""), d.get("name", "")
    if not text:
        return jsonify({"error": "Empty segment."}), 400
    if not keys.get_key(provider):
        return jsonify({"error": f"No API key for {provider}. Add one from Settings."}), 500
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

        result, cost, tokens = providers.call(
            provider, model, system_text, standing, per_call, effort=effort)
        spending.log_call(project_id, project_name, provider, model, effort, tokens, cost)

        return jsonify({
            "final": result, "cost": cost,
            "tokens": tokens
        })
    except Exception as e:
        return jsonify({"error": f"Translation failed: {e}"}), 500


def project_path(pid):
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", pid)[:80]
    return os.path.join(PROJECTS_DIR, safe + ".json")


if __name__ == "__main__":
    import webbrowser, threading
    if not any(keys.key_status().values()):
        print("\n  ! No API key found for any provider.")
        print("  ! Open the app and add one from the Settings screen,")
        print("  ! or (Anthropic only) put a key in key.txt and restart.\n")
    else:
        print("\n  Palimpsest is running.")
        print("  Open  http://localhost:5001  in your browser (opening it for you now).\n")
        threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5001")).start()
    app.run(port=5001, debug=False)
