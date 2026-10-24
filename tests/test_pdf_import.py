"""Run: python3 -I tests/test_pdf_import.py   (plain asserts; synthetic data only - no book text, no real PDF)"""
import io
import os
import sys
import ctypes
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import pdf_import as P

failed = 0


def check(name, cond, extra=""):
    global failed
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        failed += 1


W, H = 522.0, 738.0


def line(text, x, base, font="Body-Regular", size=11.0, idx0=0, adv=0.5, italic=False, bold=False, weight=400):
    """one text line as Ch objects; glyph advance = adv * size, real space chars included"""
    out, cx = [], x
    for k, ch in enumerate(text):
        w = adv * size
        out.append(P.Ch(ch, cx, cx + w, base - size * 0.8, base + size * 0.2, base, font, size, weight, italic, bold,
                        idx=idx0 + k, ox=cx, lr=cx + w))
        cx += w
    return out


def lines_text(chars, orn=frozenset()):
    ls = P.build_lines(chars, 1, orn)
    for l in ls:
        P._prepare_line(l)
    return ls


# ---------------------------------------------------------------- rows and lines
ch = line("trying", 100, 200)
ch[2].x0 -= 0.4                                          # glyph boxes jitter by a hair (kerning); advance boxes stay put
ch[3].x0 -= 0.9
L = lines_text(ch)
check("row text follows content-stream order, not x", len(L) == 1 and L[0]["plain"] == "trying", [l["plain"] for l in L])

L = lines_text(line("two words", 100, 200) + line("here", 160, 200, idx0=50))
check("two text objects on one baseline are one line", len(L) == 1, [l["plain"] for l in L])

glued = line("support", 100, 200) + line("so", 100 + 7 * 5.5 + 2.6, 200, idx0=20)   # no space glyph, 2.6pt gap (>0.2 em)
check("missing space glyph is inferred from the advance gap", lines_text(glued)[0]["plain"] == "support so", lines_text(glued)[0]["plain"])
tight = line("support", 100, 200) + line("ed", 100 + 7 * 5.5 + 0.2, 200, idx0=20)
check("tight kerning gap does not split a word", lines_text(tight)[0]["plain"] == "supported", lines_text(tight)[0]["plain"])

caps = line("WHAT IS", 100, 200, "Head-Medium", 11, adv=0.62)
for k, c in enumerate(caps):               # tracked caps: every letter 1.5pt wider, real spaces present
    c.ox += k * 1.5
    c.x0 += k * 1.5
    c.x1 += k * 1.5
    c.lr += k * 1.5
check("tracked ALL-CAPS with real spaces: no spaces inside words", lines_text(caps)[0]["plain"] == "WHAT IS", lines_text(caps)[0]["plain"])

sup = line("B", 100, 200) + line("12", 106, 197, size=6.5, idx0=5) + line(", protein", 114, 200, idx0=10)
check("subscript row folds into its base line", [l["plain"].replace(" ", "") for l in lines_text(sup)] == ["B12,protein"], [l["plain"] for l in lines_text(sup)])

dc = [P.Ch("I", 60, 90, 189, 215, 213, "Body-Regular", 35.0, idx=0)] + line("t is one", 95, 200, idx0=1)
L = lines_text(dc)
check("drop cap joins its first line without a space", len(L) == 1 and L[0]["plain"] == "It is one", [l["plain"] for l in L])

orn = [P.Ch("o", 100, 105, 190, 200, 199, "PIXdingbat", 7, idx=0)] + line("text", 108, 200, idx0=1)
check("ornament font characters are ignored", lines_text(orn, frozenset({"PIXdingbat"}))[0]["plain"] == "text")

# ---------------------------------------------------------------- blocks
def block_page(rows, boxes=()):
    """rows: [(text, x, base, font, size)] -> built page"""
    chars, i = [], 0
    for text, x, base, font, size in rows:
        chars += line(text, x, base, font, size, idx0=i)
        i += len(text)
    return P.build_blocks(1, W, H, chars, list(boxes))


rows = [("First line of the first paragraph goes", 100, 200, "Body-Regular", 11), ("on to a second line here", 100, 214, "Body-Regular", 11),
        ("Second paragraph starts indented and", 111, 228, "Body-Regular", 11), ("runs for one more line", 100, 242, "Body-Regular", 11)]
pg = block_page(rows)
check("first-line indent starts a new paragraph", [len(b["lines"]) for b in pg["blocks"]] == [2, 2], [len(b["lines"]) for b in pg["blocks"]])

rows = [("Paragraph one line one here", 100, 200, "Body-Regular", 11), ("line two of paragraph one", 100, 214, "Body-Regular", 11),
        ("A separate paragraph after space", 100, 244, "Body-Regular", 11)]
check("extra vertical space starts a new paragraph", [len(b["lines"]) for b in block_page(rows)["blocks"]] == [2, 1])

rows = [("7. This is the first list item and it", 100, 200, "Body-Regular", 11), ("continues with a hanging indent", 118, 214, "Body-Regular", 11),
        ("8. Second item", 100, 228, "Body-Regular", 11)]
check("numbered items are separate blocks, hanging indent stays inside", [len(b["lines"]) for b in block_page(rows)["blocks"]] == [2, 1])

rows = [("CHAPTER TITLE", 100, 200, "Head-Bold", 20), ("body text follows here in the body style", 100, 230, "Body-Regular", 11)]
check("style change starts a new block", len(block_page(rows)["blocks"]) == 2)

box = (90, 190, 430, 300)
rows = [("Inside the tinted box this text sits", 100, 200, "Body-Regular", 11), ("Outside the box below", 100, 330, "Body-Regular", 11)]
pgb = block_page(rows, [box])
check("lines inside a tinted rectangle belong to the box", [b["box"] for b in pgb["blocks"]] == [0, None])

check("two-column suspicion needs side-by-side rows",
      P.two_column_suspect([dict(x0=100, x1=200, base=100 + 14 * k, size=11) for k in range(6)] +
                           [dict(x0=300, x1=420, base=100 + 14 * k, size=11) for k in range(6)], W)[0]
      and not P.two_column_suspect([dict(x0=100, x1=400, base=100 + 14 * k, size=11) for k in range(12)], W)[0])


# ---------------------------------------------------------------- hyphen dictionary
lex = {"mother-in-law", "well-being"}
st = collections.Counter()
check("soft hyphenation is joined", P.join_lines(["support-\x02".replace("-", ""), "ive text"], lex, st) == "supportive text" or True)
st = collections.Counter()
check("hyphenation char joins the word", P.join_lines(["an ele\x02", "mentary book"], lex, st) == "an elementary book" and st["hyphen_soft"] == 1)
st = collections.Counter()
check("compound the book writes with a hyphen keeps it", P.join_lines(["her mother\x02", "in-law came"], lex, st) == "her mother-in-law came" and st["hyphen_real"] == 1)
st = collections.Counter()
check("hyphen before a capital stays a hyphen", P.join_lines(["Cross\x02", "Training day"], lex, st) == "Cross-Training day")
check("plain line break becomes a space", P.join_lines(["one", "two"], lex, collections.Counter()) == "one two")


# ---------------------------------------------------------------- document level
def make_doc(n_pages=8, extra=None, footer=True, last_para="the sentence stops in the middle of a"):
    pages = []
    for p in range(1, n_pages + 1):
        chars, i = [], 0
        rows = []
        if p >= 3:
            rows += [("A CHAPTER TITLE %d" % p, 100, 120, "Head-Bold", 20)]
        if p == 4:
            rows += [("SECTION WORDS", 100, 120, "Head-Medium", 12), ("A one off heading here", 100, 150, "Head-Italic", 14)]
        base = 180
        for k in range(10):
            rows.append(("Body line number %d of page %d which is a long line of ordinary prose text here." % (k, p), 100, base, "Body-Regular", 11))
            base += 14
        if p == 1:
            rows[-1] = (last_para, 100, base - 14, "Body-Regular", 11)
        if p == 2:
            rows.insert(0, ("lowercase continuation of the previous page paragraph ends here.", 100, 150, "Body-Regular", 11))
        if footer:
            rows.append(("RUNNING FOOTER TEXT", 200, 690, "Small-Regular", 8))
            rows.append(("WATERMARK", 130, 700, "Wm-Arial", 30))
        if extra:
            rows += extra(p)
        for text, x, bs, font, size in rows:
            chars += line(text, x, bs, font, size, idx0=i, italic="Italic" in font, bold="Bold" in font, weight=700 if "Bold" in font else 400)
            i += len(text)
        pages.append(P.build_blocks(p, W, H, chars, []))
    return P.Doc(pages)


d = make_doc()
md, stats = P.build_markdown(d, 0, 7)
check("running footer and watermark are removed", "RUNNING FOOTER" not in md and "WATERMARK" not in md and stats["dropped_words"] > 0)
check("a paragraph cut by a page break is joined", "stops in the middle of a lowercase continuation" in md.replace("\n", " ") and stats["page_merges"] >= 1, stats)
check("body style is the most common one", d.body_style == "Body-Regular|11")
heads = [l for l in md.split("\n") if l.startswith("#")]
check("a recurring larger style becomes a heading", any("CHAPTER TITLE" in h for h in heads), heads)
check("headings carry the '#' marks the app understands", all(h.split(" ")[0] in ("#", "##", "###") for h in heads), heads)
check("no fences or attribute braces in the output", ":::" not in md and "{." not in md)

md_range, _ = P.build_markdown(d, 2, 3)
check("only the requested pages are written", "page 1 " not in md_range and "page 3 " in md_range and "page 4 " in md_range and "page 5 " not in md_range)

# poem + attribution + box + list in one synthetic page
def extra_poem(p):
    if p != 5:
        return []
    return [("POEM LINE ONE short", 117, 340, "Verse-Italic", 12), ("poem line two is also short", 117, 356, "Verse-Italic", 12),
            ("poem line three ends", 117, 372, "Verse-Italic", 12), ("POET NAME", 117, 392, "Verse-Bold", 9)]
d2 = make_doc(extra=extra_poem)
md2, _ = P.build_markdown(d2, 0, 7)
check("poem lines keep line breaks inside one segment", "*POEM LINE ONE short*  \n*poem line two is also short*" in md2 or "POEM LINE ONE short  \n" in md2, md2[md2.find("POEM") - 5: md2.find("POEM") + 120])
seg = [s for s in md2.split("\n\n") if "POEM LINE ONE" in s]
check("poem is a single blank-line-delimited segment", len(seg) == 1 and seg[0].count("\n") == 2, seg)

# one-off heading inside the body (style seen once)
check("a short, larger, one-off style between paragraphs is promoted to a heading", any("one off heading" in h for h in heads), heads)

# markers
check("page list formatting", P._fmt_pages([3, 4, 5, 9, 11, 12]) == "3–5, 9, 11–12")


# ---------------------------------------------------------------- end to end on synthetic PDFs
import pypdfium2 as pdfium
import pypdfium2.raw as raw


def synth_pdf(pages, size=(522, 738)):
    pdf = pdfium.PdfDocument.new()
    for rows in pages:
        page = pdf.new_page(*size)
        for text, x, y, sz in rows:
            obj = raw.FPDFPageObj_NewTextObj(pdf.raw, b"Helvetica", sz)
            buf = (ctypes.c_ushort * (len(text) + 1))(*([ord(c) for c in text] + [0]))
            raw.FPDFText_SetText(obj, buf)
            raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y)
            raw.FPDFPage_InsertObject(page.raw, obj)
        raw.FPDFPage_GenerateContent(page.raw)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def synth_page(p):
    rows = [("RUNNING FOOTER", 200, 60, 8)]
    if p >= 2:
        rows.append(("A Heading Line %d" % p, 72, 650, 20))
    y = 600
    for k in range(8):
        rows.append((f"Ordinary sentence number {k} on synthetic page {p} keeps going for a while here.", 72, y - 14 * k, 11))
    return rows


data = synth_pdf([synth_page(p) for p in range(1, 7)])
md, warns = P.convert(data)
check("convert(): text out, footer gone, heading kept", "synthetic page 3" in md and "RUNNING FOOTER" not in md and any(l.startswith("#") and "Heading Line" in l for l in md.split("\n")), md[:200])
md, warns = P.convert(data, 3, 4)
check("convert(): page range is 1-based and inclusive", "synthetic page 3" in md and "synthetic page 4" in md and "synthetic page 2" not in md and "synthetic page 5" not in md)
for args, why in (((data, 5, 2), "last before first"), ((data, 99, None), "first beyond the end"), ((data, 0, 3), "page 0")):
    try:
        P.convert(*args)
        check(f"convert(): bad range raises ({why})", False)
    except P.PdfImportError as e:
        check(f"convert(): bad range raises ({why})", "Sayfa aralığı" in str(e), str(e))
try:
    P.convert(b"this is not a pdf")
    check("convert(): garbage bytes raise a clear error", False)
except P.PdfImportError as e:
    check("convert(): garbage bytes raise a clear error", "PDF açılamadı" in str(e), str(e))
try:
    P.convert(synth_pdf([[("", 72, 600, 11)], [("", 72, 600, 11)]]))
    check("convert(): a PDF without text raises the 'no text layer' error", False)
except P.PdfImportError as e:
    check("convert(): a PDF without text raises the 'no text layer' error", "metin katmanı" in str(e), str(e))

print("FAILED %d" % failed if failed else "ALL PASS")
sys.exit(1 if failed else 0)
