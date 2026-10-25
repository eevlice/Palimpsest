"""Run: python3 -I tests/test_front.py   (a synthetic PDF only; no model calls)

Front matter: a title page and a colophon (found by its ©, not by its short lines) stay out of the text; any other
front text is kept and asked once per page on the review screen, "Atılsın" takes it out: a dedication set as a short
poem keeps its lines, an epigraph keeps its lines and its attribution."""
import io
import os
import re
import sys
import ctypes
import pickle

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


POEM = ["For the keepers", "of every lantern", "on a synthetic shore", "who counted the boats", "and the bells"]
EPIGRAPH = ["Salt on the window,", "nets on the stone,", "a ferry that never came."]
SOURCE = "— A Synthetic Poet"


def front_pdf(n=140):
    """page 1 a title page, 2 a colophon, 3 a dedication set as a five-line poem, 4 an epigraph and its attribution
    (front matter: the first 4 pages of 140); then body pages with a heading every tenth page"""
    pdf = pdfium.PdfDocument.new()
    page = None

    def put(text, x, y, font=b"Times-Roman", size=11):
        obj = raw.FPDFPageObj_NewTextObj(pdf.raw, font, size)
        buf = (ctypes.c_ushort * (len(text) + 1))(*([ord(c) for c in text] + [0]))
        raw.FPDFText_SetText(obj, buf)
        raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y)
        raw.FPDFPage_InsertObject(page.raw, obj)
    for p in range(1, n + 1):
        page = pdf.new_page(500, 700)
        if p == 1:
            put("A Synthetic Harbour", 90, 500, b"Helvetica-Bold", 30)
            put("Ann Writer", 180, 440, b"Helvetica", 16)
        elif p == 2:
            put("Copyright © 2031 Ann Writer", 60, 300, b"Times-Roman", 8)
            put("ISBN 000-0-00-000000-0", 60, 288, b"Times-Roman", 8)
        elif p == 3:
            for k, t in enumerate(POEM):
                put(t, 150, 520 - 16 * k, b"Times-Italic", 12)
        elif p == 4:
            for k, t in enumerate(EPIGRAPH):
                put(t, 200, 520 - 15 * k, b"Times-Italic", 11)
            put(SOURCE, 240, 460, b"Times-Roman", 10)
        else:
            y = 640
            if p % 10 == 4:
                put("Chapter %d of the Book" % (p // 10 + 1), 60, y, b"Helvetica-Bold", 18)
                y -= 40
            for j in range(30):
                put(f"Line {j} of page {p} has plain words of a synthetic sentence" + ("." if j % 5 == 4 else ""), 60, y)
                y -= 14
        raw.FPDFPage_GenerateContent(page.raw)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


ST = P.analyse(front_pdf())
BLOB = pickle.dumps(ST)
r = P.render(ST)
S = [x for x in re.split(r"\n\s*\n", r["md"]) if x.strip()]
plain = [x.replace("*", "") for x in S]
poem = next((i for i, x in enumerate(plain) if "For the keepers" in x), None)
check("(a) a dedication set as a short poem is kept, its lines kept, flagged",
      poem is not None and "\\\n".join(POEM) in plain[poem] and "on-sayfa" in r["meta"][poem]["flags"], plain[:3])
check("(b) the colophon (©) and the title page stay out", not any("Synthetic Harbour" in x or "ISBN" in x or "Copyright" in x for x in S[:6]), S[:6])
epi = next((i for i, x in enumerate(plain) if "Salt on the window" in x), None)
check("(c) an epigraph with short lines on a front page is kept, its attribution apart",
      epi is not None and all(t in plain[epi] for t in EPIGRAPH) and S[epi + 1].startswith('::: {custom-style="Attribution"}')
      and SOURCE in plain[epi + 1], plain[epi:epi + 2] if epi is not None else plain[:4])
qs = [q for q in R.questions(ST, r) if q["type"] == "onsayfa"]
check("one question per kept front page: translate it (now) or drop it",
      [q["page"] for q in qs] == [3] and [c["label"] for c in qs[0]["choices"]] == ["Çevrilsin (şimdiki)", "Atılsın"], qs)
r2 = P.render(pickle.loads(BLOB), R.decisions_for(qs[0], "role:sil"))
check("Atılsın: the dedication goes, the epigraph stays", "For the keepers" not in r2["md"] and "Salt on the window" in r2["md"])

print("FAILED %d" % failed if failed else "ALL PASS")
sys.exit(1 if failed else 0)
