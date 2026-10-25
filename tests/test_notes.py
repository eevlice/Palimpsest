"""Run: python3 -I tests/test_notes.py   (synthetic PDFs only; a temporary HOME; no model calls)

Notes end to end: an endnote section over several pages (group labels as long as the chapter titles, entries run
together in one block), footnotes marked by letter and by sign, endnote numbers and footnote letters on one page; on
the review screen a callout without its note and a note-like block near it asked as one question, matched notes
never asked about their role, a block the user makes a note tied to a callout."""
import io
import os
import re
import sys
import ctypes
import pickle
import tempfile

HOME = tempfile.mkdtemp(prefix="palimpsest-test-")
os.environ["HOME"] = HOME
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import pdf_import as P
import review as R
import pypdfium2 as pdfium
import pypdfium2.raw as raw

failed = 0


def check(name, cond, extra=""):
    global failed
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        failed += 1


TITLE1 = "The First Synthetic Chapter Whose Title Runs On Across The Whole Page Width"
TITLE2 = "The Second Synthetic Chapter With A Title Longer Than Any Plain Label Could Be"
FILL = "plain words of a synthetic sentence that keeps going along the line"


def notes_pdf():
    """ten pages: two chapters citing endnotes (1-3, then 1-2), a lettered footnote on a page with endnote callouts,
    a dagger footnote, a sign callout whose note was set overleaf; the notes over two pages, each group under a bold
    line repeating its chapter's title, entries 2 and 3 of the first group in one line; acknowledgements last"""
    pdf = pdfium.PdfDocument.new()
    page = None

    def put(text, x, y, font=b"Courier", size=11):
        obj = raw.FPDFPageObj_NewTextObj(pdf.raw, font, size)
        buf = (ctypes.c_ushort * (len(text) + 1))(*([ord(c) for c in text] + [0]))
        raw.FPDFText_SetText(obj, buf)
        raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y)
        raw.FPDFPage_InsertObject(page.raw, obj)
        return x + len(text) * 0.6 * size              # Courier: every glyph 0.6 em wide

    def cite(text, mark, y):
        put(mark, put(text, 60, y) + 0.5, y + 4, size=6.5)

    def title(t, y, font=b"Helvetica-Bold", size=18, x=40):
        w = t.split()
        put(" ".join(w[:7]), x, y, font, size)
        put(" ".join(w[7:]), x, y - size * 1.3, font, size)

    def body(y, n, calls=None):
        for j in range(n):
            if calls and j in calls:
                cite("Line %d says a thing worth a source here." % j, calls[j], y)
            else:
                put("Line %d has %s." % (j, FILL) if j % 4 == 3 else "Line %d has %s" % (j, FILL), 60, y)
            y -= 14
        return y

    for p in range(1, 11):
        page = pdf.new_page(500, 700)
        put(str(p), 245, 30, b"Courier", 9)                                # folio
        if p == 1:
            title(TITLE1, 640)
            body(580, 24, {2: "1", 6: "a", 10: "2"})
            put("a A lettered footnote set at the foot.", 60, 80, b"Courier", 8)
        elif p == 2:
            body(640, 30, {4: "3", 12: "†"})
            put("† A dagger footnote set at the foot.", 60, 80, b"Courier", 8)
        elif p == 3:
            title(TITLE2, 640)
            body(580, 30, {3: "1", 9: "2"})
        elif p == 4:
            body(640, 30, {14: "*"})
        elif p == 5:
            body(640, 30)
            put("* A note for the starred line, set overleaf.", 60, 80, b"Courier", 8)
        elif p == 6:
            put("A Third Chapter Without Notes", 40, 640, b"Helvetica-Bold", 18)
            body(600, 26)
        elif p == 7:
            put("Notes", 40, 640, b"Helvetica-Bold", 18)
            y = 600
            title(TITLE1, y, b"Courier-Bold", 10, 60)
            y -= 34
            for t in ("1 Author, A. 2001. A Synthetic Source One. Press.",
                      "2 Author, B. 2002. Another Source. Press. 3 https://", "example.org/a-synthetic-page"):
                put(t, 60, y, b"Courier", 10)
                y -= 13
        elif p == 8:                                                       # the section goes on, no heading
            y = 640
            title(TITLE2, y, b"Courier-Bold", 10, 60)
            y -= 34
            for t in ("1 Writer, C. 2003. A Third Source. Press.", "2 Writer, D. 2004. A Fourth Source. Press."):
                put(t, 60, y, b"Courier", 10)
                y -= 13
        elif p == 9:
            put("Acknowledgements", 40, 640, b"Helvetica-Bold", 18)
            body(600, 6)
        else:
            put("An Afterword To Close The Book", 40, 640, b"Helvetica-Bold", 18)
            body(600, 26)
        raw.FPDFPage_GenerateContent(page.raw)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


PDF = notes_pdf()
ST = P.analyse(PDF)
fresh = lambda: pickle.loads(pickle.dumps(ST))
segs_of = lambda r: [x for x in re.split(r"\n\s*\n", r["md"]) if x.strip()]
r0 = P.render(fresh())
S = segs_of(r0)
st = r0["stats"]
defs = {x.split("]:")[0] + "]": x for x in S if x.startswith("[^")}
cites = " ".join(x for x in S if not x.startswith("[^"))

# -- 1. an endnote section over two pages
check("endnotes over two pages: every group found, labels as long as the chapter titles",
      all(k in defs for k in ("[^b1-1]", "[^b1-2]", "[^b1-3]", "[^b2-1]", "[^b2-2]")), sorted(defs))
check("endnotes: two entries in one line are two notes, a URL broken over lines stays one",
      defs.get("[^b1-2]") == "[^b1-2]: Author, B. 2002. Another Source. Press."
      and (defs.get("[^b1-3]") or "").startswith("[^b1-3]: https://"), (defs.get("[^b1-2]"), defs.get("[^b1-3]")))
check("endnotes: the callouts carry the labels", all(("[^b%s]" % x) in cites for x in ("1-1", "1-2", "1-3", "2-1", "2-2")))
check("endnotes: the group labels and the emptied Notes heading go, the next section stays",
      not any(TITLE1[:30] in x and not x.startswith("#") for x in S) and "# Notes" not in S
      and any(x.endswith("Acknowledgements") for x in S), [x[:60] for x in S if x.startswith("#")])

# -- 2. footnotes by letter and by sign, beside endnote numbers
check("a lettered footnote: [^b1-a], on a page with endnote callouts", "[^b1-a]" in cites
      and defs.get("[^b1-a]") == "[^b1-a]: A lettered footnote set at the foot.", defs.get("[^b1-a]"))
check("a sign footnote: its own series, [^b1-s2] for a dagger", "[^b1-s2]" in cites
      and defs.get("[^b1-s2]") == "[^b1-s2]: A dagger footnote set at the foot.", defs.get("[^b1-s2]"))
check("counts: the starred callout is the only one left", (st["note_refs"], st["note_refs_matched"], st["note_refs_unmatched"])
      == (8, 7, 1), dict((k, v) for k, v in st.items() if k.startswith("note")))
check("labels: series kept apart", P.note_series("12") == "12" and P.note_series("b") == "b" and P.note_series("aa") == "aa"
      and P.note_series("*") == "s1" and P.note_series("‡") == "s3" and P.note_series("**") == "s7")

# -- 3. the review screen
qs = R.questions(ST and r0["doc"] and dict(doc=r0["doc"], first=1, last=10, ctx=ST["ctx"]), r0)
pair = [q for q in qs if q["type"] == "eslestir"]
check("a callout without its note and a note-like block overleaf: one question",
      len(pair) == 1 and pair[0]["title"] == "Bu yazı, s. 4'teki * numaralı atıfın notu mu?"
      and [c["label"] for c in pair[0]["choices"]] == ["Evet", "Hayır (şimdiki)"] and pair[0]["other"]
      and not any(q["type"] in ("atif", "not") for q in qs), [(q["type"], q["title"]) for q in qs])
note_bids = {b for x, bids in zip(S, r0["doc"].seg_bids) if x.startswith("[^") for b in bids}
check("a matched note is never asked about its role",
      not any(set(q.get("bids", [])) & note_bids for q in qs if q["type"] in ("rol", "stil")))
r1 = P.render(fresh(), R.decisions_for(pair[0], "link"))
S1 = segs_of(r1)
check("Evet: the block is the callout's footnote", any("[^b2-s1]" in x and not x.startswith("[^") for x in S1)
      and "[^b2-s1]: A note for the starred line, set overleaf." in S1 and r1["stats"]["note_refs_unmatched"] == 0,
      [x for x in S1 if "starred" in x])
I0 = R.integrity(dict(doc=r0["doc"], first=1, last=10))
check("top line: notes matched out of callouts, not low", I0["notes"] == dict(matched=7, refs=8, low=False), I0["notes"])

# a block the user makes a note: tied to the only free callout with its mark, or asked "which callout?"
star = pair[0]["bid"]
r2 = P.render(fresh(), [{"bid": star, "role": "dipnot"}])
check("role Dipnot: a block starting with a free callout's mark nearby is tied to it",
      "[^b2-s1]: A note for the starred line, set overleaf." in segs_of(r2), [x for x in segs_of(r2) if "starred" in x])
plain = next(bids[0] for x, bids, m in zip(S, r0["doc"].seg_bids, r0["meta"]) if m["pages"] == [5])
st3 = fresh()
r3 = P.render(st3, [{"bid": plain, "role": "son not"}])
q3 = [q for q in R.questions(st3, r3) if q["type"] == "bagla"]
check("role Son not, no mark: asked which callout, the free ones nearby offered",
      len(q3) == 1 and q3[0]["id"] == "n:" + plain and q3[0]["choices"][0]["label"] == "s. 4 · *"
      and q3[0]["choices"][-1]["label"] == "Atıfsız kalsın (şimdiki)", q3)
r4 = P.render(fresh(), [{"bid": plain, "role": "son not"}] + R.decisions_for(q3[0], "ref:0"))
check("...answered: tied to that callout", any(x.startswith("[^b2-s1]: Line 3 has") for x in segs_of(r4))
      and r4["stats"]["note_refs_unmatched"] == 0)

# callouts without notes as a book-level problem: counted on the top line, not asked one by one
st5 = fresh()
r5 = P.render(st5)
r5["doc"].notes.update(refs=40, matched=20)
q5 = R.questions(st5, r5)
check("many callouts without notes: low on the top line, no single questions",
      R.integrity(st5)["notes"]["low"] and not any(q["type"] in ("atif", "eslestir") for q in q5))
r5["doc"].notes.update(refs=3, matched=2)
check("...a few are asked, even when they are a large share", not R.integrity(st5)["notes"]["low"]
      and any(q["type"] == "eslestir" for q in R.questions(st5, r5)))

# -- the server: the "which callout?" question comes right after the answer that made the note
import server
c = server.app.test_client()
d = c.post("/import", data={"file": (io.BytesIO(PDF), "kitap.pdf"), "project_id": "p_notes"},
           content_type="multipart/form-data").get_json()
check("/import: notes on the top line", d["integrity"]["notes"] == dict(matched=7, refs=8, low=False), d.get("integrity"))
d2 = c.post("/import/p_notes/answers", json={"answers": {}, "edits": [{"bid": plain, "role": "son not"}]}).get_json()
bq = [q for q in d2["questions"] if q["type"] == "bagla"]
check("answers: a note with no callout gets its question", len(bq) == 1 and bq[0]["id"] == "n:" + plain, [q["type"] for q in d2["questions"]])
d3 = c.post("/import/p_notes/answers", json={"answers": {bq[0]["id"]: {"key": "ref:0", "all": False}},
                                             "edits": [{"bid": plain, "role": "son not"}]}).get_json()
check("answers: the answered question stays and ties the note", any(q["id"] == bq[0]["id"] for q in d3["questions"])
      and any(x.startswith("[^b2-s1]: Line 3 has") for x in d3["segments"]) and d3["integrity"]["notes"]["matched"] == 8)
d4 = c.post("/import/p_notes/answers", json={"answers": {}, "edits": []}).get_json()
check("answers: the block no note again, its question goes", not any(q["type"] == "bagla" for q in d4["questions"]))

import shutil
shutil.rmtree(HOME, ignore_errors=True)
print("FAILED %d" % failed if failed else "ALL PASS")
sys.exit(1 if failed else 0)
