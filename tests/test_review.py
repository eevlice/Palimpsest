"""Run: python3 -I tests/test_review.py   (synthetic data only; a temporary HOME; no model calls)

The review screen's back half: questions from what the importer knows, answers as decisions, render() with them
(byte-identical without them; markdown, seg_meta and block ids stay aligned after join/split/drop), and the
/import -> answers -> /setup flow."""
import io
import os
import sys
import json
import time
import ctypes
import pickle
import tempfile
import collections

HOME = tempfile.mkdtemp(prefix="palimpsest-test-")
os.environ["HOME"] = HOME
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import pdf_import as P
import review as R
from segtypes import seg_type
import pypdfium2 as pdfium
import pypdfium2.raw as raw

failed = 0


def check(name, cond, extra=""):
    global failed
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        failed += 1


# ---------------------------------------------------------------- A. questions, on a hand-made Doc
class FakeDoc:
    pass


def blk(bid, role, style="Body|11", text="Some words here.", conf=None, flags=(), zone=None, top=100, cut=(), **kw):
    b = dict(bid=bid, role=role, style=style, text=text, conf=conf, flags=list(flags), x0=50, x1=400, top=top, bot=top + 12,
             lines=[dict(zone=zone, plain=text, style=style)], cut_lines=[dict(zone=z, plain=t, style=s) for z, t, s in cut])
    b.update(kw)
    return b


HEAD_STYLE = "Head|8"
pages = []
for p in range(1, 7):
    bl = []
    if p <= 4:                                   # the running head dropped on pages 1-4 ...
        bl.append(blk(f"{p}.0", "sil", HEAD_STYLE, "A Synthetic Running Head", zone="top", top=20))
    else:                                        # ... and kept (as a heading) on pages 5-6
        bl.append(blk(f"{p}.0", "bölüm", HEAD_STYLE, "A Synthetic Running Head", zone="top", top=20))
    bl.append(blk(f"{p}.1", "gövde", text=f"Body text of page {p}."))
    pages.append(dict(p=p, W=500, H=700, blocks=bl, two_col=False, items=[]))
pages[0]["blocks"].append(blk("1.2", "epigraf", "Epi|10", "An opening line.", conf=0.7, flags=["centered", "narrow"]))
pages[0]["blocks"].append(blk("1.3", "alıntı", "Body|11", "A quoted passage.", conf=0.9, flags=["indent"]))
pages[1]["blocks"].append(blk("2.2", "gövde", "Odd|10", "An odd block.", flags=["stil-farkli", "smaller"]))
pages[2]["blocks"].append(blk("3.2", "gövde", "Odd|10", "Another odd block.", flags=["stil-farkli", "smaller"]))
pages[2]["blocks"].append(blk("3.3", "alt başlık 1", "Sub|13", "A Section Nobody Lists"))
pages[3]["two_col"] = True
pages[4]["blocks"].append(blk("5.2", "epigraf", "Call|10", "What we talk about on this walk:", conf=0.7, flags=["centered"]))
pages[4]["blocks"].append(blk("5.3", "epigraf", "Call|10", "a toaster and the market.", conf=0.9, flags=["centered"]))
D = FakeDoc()
D.pages, D.body_styles, D.index_pages = pages, {"Body|11"}, []
D.seg_bids = [[b["bid"]] for p in pages for b in p["blocks"] if b["role"] != "sil"]
meta = [dict(pages=[int(bids[0].split(".")[0])], flags=[]) for bids in D.seg_bids]
meta[D.seg_bids.index(["2.1"])]["flags"] = ["atif-eslesmedi"]
meta[D.seg_bids.index(["4.1"])]["flags"] = ["not-eslesmedi"]
state = dict(doc=D, first=1, last=6, ctx=dict(outline=[dict(title=t, level=0, page=1) for t in
                                                         ("A Synthetic Running Head", "Chapter One", "Chapter Two")]))
qs = R.questions(state, dict(meta=meta))
by = collections.defaultdict(list)
for q in qs:
    by[q["type"]].append(q)
check("questions: one of every kind (role, review flag, unlisted heading, unmatched callout and note, two columns, repeat)",
      set(by) == {"rol", "stil", "baslik", "atif", "not", "sutun", "tekrar"}, dict((k, len(v)) for k, v in by.items()))
flat = lambda q: [b for t in q["targets"] for b in (t if isinstance(t, list) else [t])]
check("a role set with confidence 0.8+ is not asked on its own", not any("1.3" in flat(q) for q in by["rol"]), by["rol"])
callout = next((q for q in by["rol"] if "5.2" in flat(q)), {})
check("unit: two adjacent blocks of one role and style are one question (the sure part goes with the shaky one)",
      callout.get("targets") == [["5.2", "5.3"]] and callout.get("count") == 1 and callout.get("bids") == ["5.2", "5.3"]
      and len(callout.get("boxes", [])) == 2, callout)
check("unit: the answer goes to both blocks", R.decisions_for(callout, "role:gövde") == [{"bid": "5.2", "role": "gövde"}, {"bid": "5.3", "role": "gövde"}])
check("the same style signature is one question (two odd blocks, count 2)", len(by["stil"]) == 1 and by["stil"][0]["count"] == 2,
      by["stil"])
check("a heading the bookmarks list is not asked; one they do not list is", [q["targets"] for q in by["baslik"]] == [["3.3"]],
      by["baslik"])
rep = by["tekrar"][0] if by["tekrar"] else {}
check("inconsistent repeat: dropped on 4 pages, kept on 2, one question for all six",
      rep.get("count") == 6 and rep.get("detail") == "4 sayfada atıldı, 2 sayfada metinde kaldı.", rep)
check("every question has a title, choices, a page and an id", all(q["title"] and q["choices"] and q["page"] and q["id"] for q in qs))
dec = R.decisions_for(by["stil"][0], "role:alıntı", True)
check("an answer applies to all look-alikes by default", dec == [{"bid": "2.2", "role": "alıntı"}, {"bid": "3.2", "role": "alıntı"}], dec)
check("...or to the asked one only", R.decisions_for(by["stil"][0], "role:alıntı", False) == [{"bid": "2.2", "role": "alıntı"}])
check("repeat: drop all removes the kept ones (whole blocks)", R.decisions_for(rep, "drop") == [{"bid": "5.0", "role": "sil"}, {"bid": "6.0", "role": "sil"}])
check("repeat: keep all brings the dropped ones back in the kept ones' role", R.decisions_for(rep, "keep")
      == [{"bid": f"{p}.0", "role": "bölüm"} for p in range(1, 5)])
check("skip and the current answer decide nothing", R.decisions_for(by["rol"][0], "skip") == [] and R.decisions_for(by["atif"][0], "none") == [])
check("two columns: read top to bottom", R.decisions_for(by["sutun"][0], "order:rows") == [{"page": 4, "order": "rows"}])


# ---------------------------------------------------------------- B. render with decisions, on a synthetic PDF
def text_pdf(n=8, two_col_page=None):
    """n pages: a running head, three paragraphs of four lines each (a wider gap between paragraphs)"""
    pdf = pdfium.PdfDocument.new()

    def put(page, text, x, y, font=b"Times-Roman", size=11):
        obj = raw.FPDFPageObj_NewTextObj(pdf.raw, font, size)
        buf = (ctypes.c_ushort * (len(text) + 1))(*([ord(c) for c in text] + [0]))
        raw.FPDFText_SetText(obj, buf)
        raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y)
        raw.FPDFPage_InsertObject(page.raw, obj)
    for p in range(n):
        page = pdf.new_page(500, 700)
        put(page, "THE SYNTHETIC BOOK", 200, 670, b"Helvetica", 8)
        y = 620
        if p == 5:
            put(page, "xx", 240, y)                  # two stray letters on a line of their own
            y -= 40
        for k in range(3):
            for j in range(4):
                put(page, f"Paragraph {k} line {j} of page {p} carries plain words along" + ("." if j == 3 else ""), 60, y)
                y -= 14
            y -= 16
        raw.FPDFPage_GenerateContent(page.raw)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


PDF = text_pdf()
ST = P.analyse(PDF)
fresh = lambda: pickle.loads(pickle.dumps(ST))
base = P.render(fresh())
segs = lambda r: [x for x in r["md"].split("\n\n") if x.strip()]
aligned = lambda r: len(segs(r)) == len(r["meta"]) == len(r["doc"].seg_bids)
md0, w0, m0 = P.convert_with_meta(PDF)
check("no decisions: byte-identical to convert (markdown, warnings, seg_meta)",
      P.render(fresh(), [])["md"] == base["md"] == md0 and base["warnings"] == w0 and base["meta"] == m0)
check("seg_meta never carries the private block ids", not any(k.startswith("_") for m in base["meta"] for k in m))
S0 = segs(base)
bids = base["doc"].seg_bids
i_join = next(i for i, s in enumerate(S0) if s.startswith("Paragraph 1 line 0 of page 2"))
r = P.render(fresh(), [{"bid": bids[i_join][0], "join": True}])
S = segs(r)
check("join: one segment fewer, the text goes on in the one before, aligned, noted",
      len(S) == len(S0) - 1 and "of page 2 carries plain words along. Paragraph 1 line 0 of page 2" in S[i_join - 1]
      and aligned(r) and "kullanici:birlestir" in r["meta"][i_join - 1]["flags"], (len(S), len(S0), S[i_join - 1][-80:]))
r = P.render(fresh(), [{"bid": bids[i_join][0], "split": 2}])
S = segs(r)
check("split: one segment more, at line 2, both halves noted, aligned",
      len(S) == len(S0) + 1 and S[i_join].endswith("line 1 of page 2 carries plain words along") and S[i_join + 1].startswith("Paragraph 1 line 2")
      and aligned(r) and "kullanici:bol" in r["meta"][i_join]["flags"] and "kullanici:bol" in r["meta"][i_join + 1]["flags"], S[i_join:i_join + 2])
r = P.render(fresh(), [{"bid": bids[i_join][0], "role": "sil"}])
check("drop: the segment is gone, the rest aligned", len(segs(r)) == len(S0) - 1 and not any(s.startswith("Paragraph 1 line 0 of page 2") for s in segs(r))
      and aligned(r))
r = P.render(fresh(), [{"bid": bids[i_join][0], "role": "epigraf"}])
S = segs(r)
check("role: epigraph fence, the segment type follows, seg_meta says who decided",
      S[i_join].startswith('::: {custom-style="Epigraph"}') and seg_type(S[i_join])["type"] == "epigraph"
      and r["meta"][i_join]["role"] == "epigraph" and "kullanici:epigraf" in r["meta"][i_join]["flags"] and aligned(r), S[i_join][:60])
r = P.render(fresh(), [{"bid": bids[i_join][0], "role": "bölüm"}, {"bid": bids[i_join + 1][0], "role": "altyazı"}])
S = segs(r)
check("role: heading and a caption standing on its own", S[i_join].startswith("# Paragraph 1") and seg_type(S[i_join + 1])["type"] == "caption"
      and aligned(r), (S[i_join][:30], S[i_join + 1][:40]))
xx = next(b for p in ST["doc"].pages for b in p["blocks"] if b["text"].strip() == "xx")
check("stray letters: dropped by the importer's own rule", "xx" not in S0)
r = P.render(fresh(), [{"bid": xx["bid"], "role": "gövde"}])
check("stray letters: a user's Paragraf decision wins over the rule (the block stays), aligned", "xx" in segs(r) and aligned(r)
      and "kullanici:gövde" in r["meta"][segs(r).index("xx")]["flags"], [x for x in segs(r) if len(x) < 6])
heads = [b for p in ST["doc"].pages for b in p["blocks"] if b["role"] == "sil" and "SYNTHETIC BOOK" in b["text"]]
r = P.render(fresh(), [{"bid": heads[0]["bid"], "role": "gövde"}])
check("a dropped running head can be kept (role given back), aligned", any(s == "THE SYNTHETIC BOOK" for s in segs(r)) and aligned(r),
      [s for s in segs(r) if "SYNTHETIC" in s])

# ---------------------------------------------------------------- C. /import -> answers -> /setup
import server
TOK = {"input": 10, "output": 5, "cache_read": 0, "cache_write": 0, "stop_reason": "end_turn", "truncated": False}
server.build_context = lambda provider, model, src, tgt, text: ("=== SYNOPSIS ===\nKısa özet.\n\n=== TERM SHEET ===\n", 0.0, dict(TOK))
server.condense_brief = lambda provider, model, synopsis: (synopsis, None, None)
server.keys.get_key = lambda provider: "test-key"
c = server.app.test_client()
r = c.post("/import", data={"file": (io.BytesIO(PDF), "kitap.pdf"), "project_id": "p_rev", "name": "deneme"},
           content_type="multipart/form-data")
d = r.get_json()
check("/import: 200, segments with seg_meta and block ids aligned, the questions, the loss check",
      r.status_code == 200 and len(d["segments"]) == len(d["segMeta"]) == len(d["segBids"]) and isinstance(d["questions"], list)
      and d["integrity"]["sentence"] == "Metin kaybı yok", (r.status_code, d.get("error"), d.get("integrity")))
check("/import: the PDF, the analysis and the review wait in _pending (no project yet)",
      all(os.path.exists(server.pending_file("p_rev", e)) for e in ("state.pkl.gz", "review.json"))
      and os.path.exists(server.pdf_path("p_rev", True)) and not os.path.exists(server.project_path("p_rev")))
check("/import: no model call, nothing spent", not os.path.exists(os.path.join(server.CONFIG_DIR, "spending.jsonl")))
check("/page_image reads the pending PDF while the review is open", c.get("/page_image?project=p_rev&page=1").status_code == 200)
edits = [{"bid": d["segBids"][i_join][0], "role": "epigraf"}]
r = c.post("/import/p_rev/answers", json={"answers": {}, "edits": edits})
d2 = r.get_json()
check("answers/edits: saved and rendered again", r.status_code == 200 and d2["segments"][i_join].startswith('::: {custom-style="Epigraph"}')
      and d2["edits"] == edits, d2.get("error"))
d3 = c.get("/import/p_rev").get_json()
check("a page reload gets the review as it was left", d3["edits"] == edits and d3["segments"] == d2["segments"])
r = c.post("/setup", data={"import_id": "p_rev", "project_id": "p_rev", "model": "anthropic::claude-opus-5-5", "name": "deneme"},
           content_type="multipart/form-data")
d4 = r.get_json()
check("/setup from the review: its decisions are in the project, seg_meta aligned and noted",
      r.status_code == 200 and d4["segments"] == d2["segments"] and len(d4["segMeta"]) == len(d4["segments"])
      and "kullanici:epigraf" in d4["segMeta"][i_join]["flags"] and d4["pdfKept"], (r.status_code, d4.get("error")))
c.post("/import/p_rev/answers", json={"answers": {}, "edits": []})
d5 = c.post("/setup", data={"import_id": "p_rev", "project_id": "p_rev", "model": "anthropic::claude-opus-5-5", "name": "deneme"},
            content_type="multipart/form-data").get_json()
check("unanswered: the importer's own reading goes on (same as without a review)", d5["segments"] == server.segment(md0)[0])
c.post("/save", json={"id": "p_rev", "name": "deneme", "segments": d5["segments"]})
check("first save: the PDF moves to the project, the review files go",
      os.path.exists(server.pdf_path("p_rev")) and not any(os.path.exists(server.pending_file("p_rev", e)) for e in ("state.pkl.gz", "review.json")))
c.post("/import", data={"file": (io.BytesIO(PDF), "kitap.pdf"), "project_id": "p_old"}, content_type="multipart/form-data")
old = time.time() - 25 * 3600
for e in ("state.pkl.gz", "review.json"):
    os.utime(server.pending_file("p_old", e), (old, old))
os.utime(server.pdf_path("p_old", True), (old, old))
check("an abandoned review is cleaned after a day, with its PDF", server.clean_pending() == 3
      and c.get("/import/p_old").status_code == 404)

import shutil
shutil.rmtree(HOME, ignore_errors=True)
print("FAILED %d" % failed if failed else "ALL PASS")
sys.exit(1 if failed else 0)
