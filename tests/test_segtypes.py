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

print("FAILED %d" % failed if failed else "ALL PASS")
sys.exit(1 if failed else 0)
