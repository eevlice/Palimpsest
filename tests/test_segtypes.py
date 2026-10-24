"""Run: python3 -I tests/test_segtypes.py   (plain asserts, no pytest needed)"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from segtypes import (seg_type, to_prompt, from_model, reorder_footnotes,
                      strip_for_context, strip_block, strip_marker)

failed = 0


def check(name, cond):
    global failed
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failed += 1


# ---- seg_type table (shared with the node equivalence test) ----
with open(os.path.join(HERE, "segtypes_fixture.json"), encoding="utf-8") as f:
    for row in json.load(f):
        check("seg_type %r" % row["text"], seg_type(row["text"]) == row["type"])

# ---- to_prompt / from_model ----
t, labels = to_prompt("## Birinci Bölüm")
check("heading marker stripped", (t, labels) == ("Birinci Bölüm", []))
t, labels = to_prompt("[^b1-1]: Dipnot metni.")
check("footnote marker stripped", (t, labels) == ("Dipnot metni.", []))
src = "Paragraf[^b1-1] devam[^b1-2], **kalın** ve *italik*, yine[^b1-1]."
t, labels = to_prompt(src)
check("refs -> placeholders, repeat reuses number",
      t == "Paragraf⟦1⟧ devam⟦2⟧, **kalın** ve *italik*, yine⟦1⟧." and labels == ["b1-1", "b1-2"])
out, w = from_model("Metin⟦1⟧ sürer⟦2⟧, **kalın**, yine⟦1⟧.", t, labels, {"type": "para"})
check("round trip, no warning", out == "Metin[^b1-1] sürer[^b1-2], **kalın**, yine[^b1-1]." and w == [])
out, w = from_model("Metin⟦1⟧ sürer, yine⟦1⟧.", t, labels, {"type": "para"})
check("missing placeholder warns", len(w) == 1 and "⟦2⟧" in w[0])
out, w = from_model("Metin⟦1⟧ sürer⟦2⟧⟦3⟧, yine⟦1⟧.", t, labels, {"type": "para"})
check("extra placeholder warns, unknown one left as is", len(w) == 1 and "⟦3⟧" in out)
out, w = from_model("Metin⟦1⟧ sürer⟦2⟧.", t, labels, {"type": "para"})
check("dropped repeat (count differs) warns", len(w) == 1)
out, w = from_model("## Bölüm", "Birinci Bölüm", [], {"type": "heading", "level": 2})
check("model echoed heading marker -> removed", out == "Bölüm" and w == [])
out, w = from_model("[^b1-1]: Not.", "Dipnot metni.", [], {"type": "footnote", "label": "b1-1"})
check("model echoed footnote marker -> removed", out == "Not." and w == [])
out, w = from_model("Çeviri.", "Metin.", [], {"type": "para"})
check("no refs, no warning", out == "Çeviri." and w == [])

# ---- strip_for_context ----
check("context: heading marker off", strip_for_context("## Alt başlık") == "Alt başlık")
check("context: refs removed outright",
      strip_for_context("Metin[^b1-1] sürer[^b1-2].") == "Metin sürer.")
check("context: footnote label off", strip_for_context("[^b1-1]: Dipnot.") == "Dipnot.")
check("context: bold/italic kept", strip_for_context("**kalın** *italik*[^a]") == "**kalın** *italik*")
check("strip_block per paragraph",
      strip_block("# Başlık\n\nMetin[^a].\n\n[^a]: Not.") == "Başlık\n\nMetin.\n\nNot.")
check("strip_marker leaves plain text", strip_marker("Düz metin") == "Düz metin")

# ---- reorder_footnotes ----
segs = ["# Bölüm", "A[^b1-1] ve B[^b1-2].", "C[^b1-1] tekrar.", "D.",
        "[^b1-2]: iki", "[^b1-1]: bir"]
out, w = reorder_footnotes(segs)
check("order: footnote behind first citing paragraph",
      out == ["# Bölüm", "A[^b1-1] ve B[^b1-2].", "[^b1-1]: bir", "[^b1-2]: iki",
              "C[^b1-1] tekrar.", "D."] and w == [])
check("segment text untouched", sorted(out) == sorted(segs))
out, w = reorder_footnotes(["A.", "B.", "[^zz-9]: yetim"])
check("orphan stays at end + warning",
      out == ["A.", "B.", "[^zz-9]: yetim"] and [x["kind"] for x in w] == ["orphan"])
out, w = reorder_footnotes(["A[^q-1].", "B."])
check("undefined ref warns, order unchanged",
      out == ["A[^q-1].", "B."] and [x["kind"] for x in w] == ["undefined"])
out, w = reorder_footnotes(["A[^d-1].", "[^d-1]: bir", "B.", "[^d-1]: iki"])
check("duplicate label warns, both definitions kept together",
      out == ["A[^d-1].", "[^d-1]: bir", "[^d-1]: iki", "B."]
      and [x["kind"] for x in w] == ["duplicate"])
out, w = reorder_footnotes(["A.", "B.", "# H"])
check("no footnotes: identical order, no warnings", out == ["A.", "B.", "# H"] and w == [])
out, w = reorder_footnotes(["A[^x-1] b[^x-1]", "[^x-1]: n"])
check("repeat ref in one paragraph: one placement", out == ["A[^x-1] b[^x-1]", "[^x-1]: n"])

# ---- captions and tables (import round 2) ----
from segtypes import table_shape
cap = '::: {custom-style="Caption"}\n*Fig. 5:* A cat\n:::'
check("caption: the model sees the text without its fences", to_prompt(cap) == ("*Fig. 5:* A cat", []))
out, w = from_model('::: {custom-style="Caption"}\n*Şek. 5:* Bir kedi\n:::', "*Fig. 5:* A cat", [], {"type": "caption"})
check("caption: an echoed fence comes off the answer", out == "*Şek. 5:* Bir kedi" and w == [])
tab = "| a | b |\n|---|---|\n| 1 | 2 |"
out, w = from_model("| x | y |\n|---|---|\n| 1 | 2 |", tab, [], {"type": "table"})
check("table: same rows and columns, no warning", w == [])
out, w = from_model("| x | y |\n|---|---|", tab, [], {"type": "table"})
check("table: a lost row is a warning", len(w) == 1 and "3 satır" in w[0] and "2 satır" in w[0])
out, w = from_model("| x | y | z |\n|---|---|---|\n| 1 | 2 | 3 |", tab, [], {"type": "table"})
check("table: an extra column is a warning", len(w) == 1 and "sütun" in w[0])
check("table shape ignores an escaped pipe inside a cell", table_shape("| a \\| b | c |\n|---|---|") == (2, [2, 2]))
check("context text of a caption has no fences", strip_for_context(cap) == "*Fig. 5:* A cat")

# named blocks: epigraph, verse, attribution, box (fenced) and block quote
import segtypes as ST
verse = '::: {custom-style="Verse"}\nOne line\\\n*Two* line\n:::'
check("verse: the model sees lines, no fence, no hard-break backslashes", ST.to_prompt(verse)[0] == "One line\n*Two* line")
check("verse: a translation keeps its lines and wraps back to the same form", ST.wrap("Bir dize\nİki dize", "verse")
      == '::: {custom-style="Verse"}\nBir dize\\\nİki dize\n:::')
out, w = ST.from_model('::: {custom-style="Verse"}\nBir dize\\\nİki dize\n:::', "One line\nTwo line", [], {"type": "verse"})
check("verse: an echoed fence and hard breaks come off", out == "Bir dize\nİki dize" and not w)
out, w = ST.from_model("Bir dize iki dize", "One line\nTwo line", [], {"type": "verse"})
check("verse run into one line: a warning", any("tek satıra" in x for x in w))
out, w = ST.from_model("Bir\nüç\nbeş", "One line\nTwo line", [], {"type": "verse"})
check("verse: a different line count is allowed", not w)
check("block quote: '> ' off for the model, back on export", ST.to_prompt("> A quote.")[0] == "A quote." and ST.wrap("Alıntı.", "blockquote") == "> Alıntı.")
check("block quote: an echoed '> ' comes off", ST.from_model("> Alıntı.", "A quote.", [], {"type": "blockquote"})[0] == "Alıntı.")
for t, style in (("epigraph", "Epigraph"), ("attribution", "Attribution"), ("box", "Box")):
    seg = '::: {custom-style="%s"}\nText[^n1] here.\n:::' % style
    text, labels = ST.to_prompt(seg)
    check(t + ": fence off, refs as placeholders", text == "Text⟦1⟧ here." and labels == ["n1"])
    check(t + ": context text without fence", ST.strip_for_context(seg) == "Text here.")

# ---- a space the model sets before a marker goes; a line break does not
t, labels = to_prompt("Resting[^b10-33] here.")
out, w = from_model("dinlenmek ⟦1⟧ burada.", t, labels, {"type": "para"})
check("space before a marker: removed, the ref sits on the word", out == "dinlenmek[^b10-33] burada." and not w)
out, w = from_model("dinlenmek\u00A0\u202F⟦1⟧ burada.", t, labels, {"type": "para"})
check("no-break spaces before a marker: removed too", out == "dinlenmek[^b10-33] burada.")
t, labels = to_prompt("::: {custom-style=\"Verse\"}\nline\\\n[^n1]rest\n:::")
out, w = from_model("satır\\\n⟦1⟧devam", t, labels, {"type": "para"})
check("a line break (verse hard break) before a marker stays", out == "satır\\\n[^n1]devam")

print("FAILED %d" % failed if failed else "ALL PASS")
sys.exit(1 if failed else 0)
