"""
Segment types, derived from the source markdown every time - never stored.

    "![Görsel · s.94](gorsel-94-1)" -> image (a placeholder; never translated)
    '::: {custom-style="Caption"}' / text / ':::' -> caption (translated; Pandoc gives it the Word style "Caption")
    "| a | b |" / "|---|---|" ...    -> table (a markdown table; translated cell by cell)
    "# Title", "## Sub" ...        -> heading (with level)
    "[^label]: text"              -> footnote (with label)
    anything else                 -> paragraph

The source text is kept exactly as written. The markers only come off on the
way to the model (to_prompt / strip_for_context), and the translation box
never shows them. index.html's segType() mirrors seg_type() - keep the two
regexes identical (tests/segtypes_fixture.json checks both).
"""

import re
from collections import Counter

# [^\]\r\n\t ] instead of [^\]\s]: Python's \s and JS's \s disagree on a few
# exotic characters, and the frontend must classify exactly like this does.
HEADING = re.compile(r"^(#{1,6})[ \t]+")
FOOTNOTE = re.compile(r"^\[\^([^\]\r\n\t ]+)\]:[ \t]*")
REF = re.compile(r"\[\^([^\]\r\n\t ]+)\](?!:)")
PLACEHOLDER = re.compile(r"⟦(\d+)⟧")
# whole-segment forms. Python's \Z and JS's $ (no m flag) both mean "end of the text".
IMAGE = re.compile(r"^!\[[^\]\r\n]*\]\([^)\s]+\)\Z")
CAPTION_OPEN, CAPTION_CLOSE = '::: {custom-style="Caption"}\n', "\n:::"
CAPTION = re.compile(r'^::: \{custom-style="Caption"\}\n([\s\S]*)\n:::\Z')
TABLE = re.compile(r"^\|[^\n]*\|[ \t]*\n\|(?:[ \t]*:?-{3,}:?[ \t]*\|)+[ \t]*(?:\n\|[^\n]*\|[ \t]*)*\Z")


def seg_type(s):
    if IMAGE.match(s):
        return {"type": "image"}
    if CAPTION.match(s):
        return {"type": "caption"}
    if TABLE.match(s):
        return {"type": "table"}
    m = HEADING.match(s)
    if m:
        return {"type": "heading", "level": len(m.group(1))}
    m = FOOTNOTE.match(s)
    if m:
        return {"type": "footnote", "label": m.group(1)}
    return {"type": "para"}


def strip_marker(s):
    """Drop the leading '## ' or '[^label]: ', or a caption's fence lines - nothing else."""
    m = CAPTION.match(s)
    if m:
        return m.group(1)
    m = HEADING.match(s) or FOOTNOTE.match(s)
    return s[m.end():] if m else s


def table_shape(s):
    """(rows, columns per row) of a markdown table text; an escaped \\| is not a column border"""
    rows = [r for r in s.strip().split("\n") if r.strip()]
    return len(rows), [len(re.findall(r"(?<!\\)\|", r)) - 1 for r in rows]


def to_prompt(s):
    """The text of ONE segment as the model should see it: marker gone, each
    footnote reference a neutral ⟦n⟧ (n = order of first appearance in this
    segment; a repeated label reuses its number). Returns (text, labels)
    where labels[n-1] is the original label."""
    labels = []

    def swap(m):
        label = m.group(1)
        if label not in labels:
            labels.append(label)
        return "⟦%d⟧" % (labels.index(label) + 1)

    return REF.sub(swap, strip_marker(s)), labels


def from_model(out, prompt_text, labels, src_type):
    """Undo to_prompt on the model's answer. Returns (text, warnings). The model
    sometimes echoes the marker back; it comes off again here."""
    warnings = []
    if src_type["type"] == "heading":
        out = HEADING.sub("", out, count=1)
    elif src_type["type"] == "footnote":
        out = FOOTNOTE.sub("", out, count=1)
    elif src_type["type"] == "caption":
        m = CAPTION.match(out.strip())
        out = m.group(1) if m else out
    elif src_type["type"] == "table":
        out = out.strip()
        (rs, cs), (ro, co) = table_shape(prompt_text), table_shape(out)
        if rs != ro or cs != co:
            warnings.append("Tablo yapısı uyuşmuyor: kaynakta %d satır × %s sütun, çeviride %d satır × %s sütun" % (
                rs, "/".join(map(str, sorted(set(cs)))), ro, "/".join(map(str, sorted(set(co))))))
    want = Counter(int(k) for k in PLACEHOLDER.findall(prompt_text))
    got = Counter(int(k) for k in PLACEHOLDER.findall(out))
    if want != got:
        warnings.append("Referanslar uyuşmuyor: kaynakta %s, çeviride %s" % (
            _fmt(want), _fmt(got)))

    def swap(m):
        k = int(m.group(1))
        return "[^%s]" % labels[k - 1] if 1 <= k <= len(labels) else m.group(0)

    return PLACEHOLDER.sub(swap, out), warnings


def _fmt(counter):
    return ", ".join("⟦%d⟧×%d" % (k, n) if n > 1 else "⟦%d⟧" % k
                     for k, n in sorted(counter.items())) or "yok"


def strip_for_context(s):
    """For context only (neighbours, approved pairs, brief, style sample): marker
    off and every reference removed outright - no placeholders."""
    return REF.sub("", strip_marker(s))


def strip_block(text):
    """strip_for_context over every blank-line-separated paragraph of a block."""
    parts = re.split(r"\n\s*\n", text)
    return "\n\n".join(strip_for_context(p.strip()) for p in parts if p.strip())


def reorder_footnotes(segs):
    """Put each footnote right behind the first segment that cites it. Run once,
    at import. Segment text is untouched; only positions change. Returns
    (segments, warnings) - warnings are dicts {kind, label, message}:
    duplicate (same label defined twice; every definition is kept, placed
    together), orphan (defined, never cited; stays at the end), undefined
    (cited, never defined)."""
    types = [seg_type(s) for s in segs]
    notes, warnings = {}, []
    for s, t in zip(segs, types):
        if t["type"] == "footnote":
            notes.setdefault(t["label"], []).append(s)
    for label, defs in notes.items():
        if len(defs) > 1:
            warnings.append({"kind": "duplicate", "label": label,
                             "message": "Yinelenen dipnot etiketi: [^%s] %d kez tanımlı" % (label, len(defs))})
    body = [s for s, t in zip(segs, types) if t["type"] != "footnote"]
    out, placed, undefined = [], set(), set()
    for s in body:
        out.append(s)
        for label in dict.fromkeys(REF.findall(s)):
            if label in notes:
                if label not in placed:
                    out.extend(notes[label])
                    placed.add(label)
            elif label not in undefined:
                undefined.add(label)
                warnings.append({"kind": "undefined", "label": label,
                                 "message": "Tanımsız dipnot atfı: [^%s]" % label})
    for label, defs in notes.items():
        if label not in placed:
            out.extend(defs)
            warnings.append({"kind": "orphan", "label": label,
                             "message": "Atıf yapılmayan dipnot: [^%s]" % label})
    return out, warnings
