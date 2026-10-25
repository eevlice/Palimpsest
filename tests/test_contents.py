"""Run: python3 -I tests/test_contents.py   (synthetic PDFs only; a temporary HOME; no model calls)

The table of contents: found by its rows (dot leaders, two indented levels, with or without a "Contents" title),
kept out of the text and restorable, its printed pages mapped to PDF pages by the folios, its entries matched to the
body's headings; heading levels set by it (parts over chapters written apart: {.kisim} / {.bolum}); on the review
screen an entry the body sets as no heading is asked, one the body lacks is listed."""
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
import segtypes
import pypdfium2 as pdfium
import pypdfium2.raw as raw

failed = 0


def check(name, cond, extra=""):
    global failed
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        failed += 1


FILL = "plain words of a synthetic sentence that goes on"
CHAPTERS = ["The Lantern Keeper", "A Harbour at Dawn", "Seven Grey Boats", "The Bell Rings Twice", "Salt on the Window",
            "Nets Mended by Noon", "The Last Ferry"]


def book_pdf(titled=True, leader=True, parts=False, plain_heading=None, missing=None, labels=False):
    """pages: 1 title, 2 contents, then per chapter two pages (heading + body, body), folios printed 2 less than the
    PDF page from page 3 on. parts: two parts over the chapters (a part's page before its chapters), the contents
    indented two levels. plain_heading: a chapter whose title is set as body text; missing: one with no title at all;
    labels: "CHAPTER ONE" ... set well above each title"""
    pdf = pdfium.PdfDocument.new()
    page = None

    def put(text, x, y, font=b"Courier", size=11):
        obj = raw.FPDFPageObj_NewTextObj(pdf.raw, font, size)
        buf = (ctypes.c_ushort * (len(text) + 1))(*([ord(c) for c in text] + [0]))
        raw.FPDFText_SetText(obj, buf)
        raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y)
        raw.FPDFPage_InsertObject(page.raw, obj)

    def body(y, n, p):
        for j in range(n):
            put(f"Line {j} of page {p} has {FILL}" + ("." if j % 4 == 3 else ""), 60, y)
            y -= 14

    plan = []                                   # (kind, title) per content page from page 3
    for k, t in enumerate(CHAPTERS):
        if parts and k in (0, 4):
            plan.append(("part", "Part One" if k == 0 else "Part Two"))
        plan += [("chapter", t), ("body", None)]
    pages = 2 + len(plan)
    printed = {kind_t: 2 + i + 1 - 2 for i, kind_t in enumerate(plan)}
    rows = []
    for i, (kind, t) in enumerate(plan):
        if kind in ("part", "chapter"):
            rows.append((kind, t, 2 + i + 1 - 2))
    for p in range(1, pages + 1):
        page = pdf.new_page(500, 700)
        if p == 1:
            put("A Synthetic Book", 120, 500, b"Helvetica-Bold", 26)
        elif p == 2:
            y = 640
            if titled:
                put("Contents", 60, y, b"Helvetica-Bold", 18)
                y -= 40
            for kind, t, n in rows:
                x = 60 if (kind == "part" or not parts) else 84
                if leader:
                    put(t + " " + "." * (40 - len(t) - (x - 60) // 7) + " " + str(n), x, y)
                else:
                    put(t, x, y)
                    put(str(n), 400, y)
                y -= 20
        else:
            kind, t = plan[p - 3]
            put(str(p - 2), 245, 40, b"Courier", 9)                         # folio
            if kind == "part":
                put(t, 60, 640, b"Helvetica-Bold", 24)
                body(580, 8, p)
            elif kind == "chapter":
                if t == missing:
                    body(640, 30, p)
                elif t == plain_heading:
                    put(t, 60, 640)
                    body(600, 26, p)
                else:
                    if labels:
                        put("CHAPTER " + "ONE TWO THREE FOUR FIVE SIX SEVEN".split()[CHAPTERS.index(t)], 60, 660, b"Helvetica-Bold", 14)
                    put(t, 60, 600 if labels else 640, b"Helvetica-Bold", 18)
                    body(560 if labels else 600, 26, p)
            else:
                body(640, 30, p)
        raw.FPDFPage_GenerateContent(page.raw)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


segs_of = lambda r: [x for x in re.split(r"\n\s*\n", r["md"]) if x.strip()]


def run(**kw):
    """the analysis (rendered, as the review screen has it) and its render; fresh() copies come from BLOB"""
    global BLOB
    st = P.analyse(book_pdf(**kw))
    BLOB = pickle.dumps(st)
    return st, P.render(st)


fresh = lambda: pickle.loads(BLOB)


# -- dot leaders, a "Contents" title
st, r = run()
toc = st["doc"].toc
es = toc["entries"] if toc else []
S = segs_of(r)
check("dot leaders: the contents page found, every entry with its title and printed page",
      toc and toc["pages"] == [2] and toc["titled"] and [(e["title"], e["num"]) for e in es]
      == [(t, str(1 + 2 * k)) for k, t in enumerate(CHAPTERS)], toc and [(e["title"], e["num"]) for e in es])
check("printed pages map to PDF pages by the folios", [e["pdf"] for e in es] == [3 + 2 * k for k in range(len(CHAPTERS))], [e["pdf"] for e in es])
check("every entry matched to its heading", all(e["match"] for e in es), [e["match"] for e in es])
check("the contents are not in the text", not any("Contents" in x or "...." in x for x in S) and r["stats"]["toc_blocks"] >= 1,
      [x for x in S if "Contents" in x or "...." in x])
r2 = P.render(fresh(), [{"contents": "keep"}])
S2 = segs_of(r2)
check("brought back by the user: the title a heading, the rows as text", "# Contents" in S2
      and any("The Lantern Keeper" in x and "...." in x for x in S2) and len(S2) > len(S), [x[:40] for x in S2[:4]])
I = R.integrity(st)
check("review top line: where the contents were, not kept", I["contents"] == dict(pages="2", kept=False), I["contents"])

# -- no title, page numbers in a column of their own
st, r = run(titled=False, leader=False)
toc = st["doc"].toc
check("no title, numbers in their own column: found all the same",
      toc and toc["pages"] == [2] and not toc["titled"] and [e["num"] for e in toc["entries"]] == [str(1 + 2 * k) for k in range(len(CHAPTERS))]
      and all(e["match"] for e in toc["entries"]), toc and [(e["title"], e["num"]) for e in toc["entries"]])

# -- two indented levels: parts over chapters, written apart
st, r = run(parts=True)
toc = st["doc"].toc
S = segs_of(r)
check("two indented levels: parts level 1, chapters level 2, parts over chapters",
      toc and [e["level"] for e in toc["entries"]] == [1, 2, 2, 2, 2, 1, 2, 2, 2] and toc["parts"],
      toc and [(e["title"], e["level"]) for e in toc["entries"]])
check("parts and chapters kept apart in the markdown: {.kisim} / {.bolum}",
      "# Part One {.kisim}" in S and "# Part Two {.kisim}" in S and "# The Lantern Keeper {.bolum}" in S, [x for x in S if x.startswith("#")])
check("the class is no text: stripped for the model, back on export",
      segtypes.strip_marker("# Part One {.kisim}") == "Part One" and segtypes.to_prompt("# The Lantern Keeper {.bolum}")[0] == "The Lantern Keeper"
      and segtypes.from_model("Fener Bekçisi {.bolum}", "The Lantern Keeper", [], {"type": "heading", "level": 1})[0] == "Fener Bekçisi")
import server
check("Word: parts Heading 1, chapters Heading 2, the rest one level down",
      server.docx_markdown("# Part One {.kisim}\n\n# The Lantern Keeper {.bolum}\n\n## A Section\n\nText.")
      == "# Part One {.kisim}\n\n## The Lantern Keeper {.bolum}\n\n### A Section\n\nText."
      and server.docx_markdown("# A Chapter\n\n## A Section") == "# A Chapter\n\n## A Section")

# -- a chapter's label set apart above its title: one contents entry, one heading
st, r = run(labels=True)
S = segs_of(r)
check("a label far above its title: one heading", "# CHAPTER ONE The Lantern Keeper" in S and not any(x.startswith("#") and x.endswith("CHAPTER TWO") for x in S),
      [x for x in S if x.startswith("#")])

# -- the review screen: an entry set as body text is asked, one with no title in the body is listed
st, r = run(plain_heading="Seven Grey Boats", missing="Salt on the Window")
qs = R.questions(st, r)
qi = [q for q in qs if q["type"] == "icindekiler"]
qm = [q for q in qs if q["type"] == "icindekiler-yok"]
check("an entry the body sets as no heading: asked, the heading role offered",
      len(qi) == 1 and qi[0]["choices"][0]["key"].startswith("role:") and qi[0]["page"] == 7, [(q["type"], q["page"]) for q in qs])
check("an entry the body lacks: listed once, its row boxed on the contents page",
      len(qm) == 1 and "Salt on the Window" in qm[0]["detail"] and qm[0]["boxes"][0][0] == 2 and qm[0]["count"] == 1, qm)
r3 = P.render(fresh(), R.decisions_for(qi[0], qi[0]["choices"][0]["key"]))
check("...answered: a heading", any(x.startswith("# Seven Grey Boats") for x in segs_of(r3)), [x[:30] for x in segs_of(r3) if "Seven" in x])

import shutil
shutil.rmtree(HOME, ignore_errors=True)
print("FAILED %d" % failed if failed else "ALL PASS")
sys.exit(1 if failed else 0)
