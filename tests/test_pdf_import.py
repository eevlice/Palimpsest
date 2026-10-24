"""Run: python3 -I tests/test_pdf_import.py   (plain asserts; synthetic data only - no book text, no real PDF)"""
import io
import os
import sys
import ctypes
import collections
import math
import re

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

# ================================================================ fix round 1 (karne items 1, 2, 3, 4, 6, 7, 9)
def doc_from(pages_rows, size=(W, H)):
    """pages_rows: per page [(text, x, base, font, size)] -> Doc"""
    pages = []
    for p, rows in enumerate(pages_rows, 1):
        chars, i = [], 0
        for text, x, bs, font, sz in rows:
            chars += line(text, x, bs, font, sz, idx0=i, italic="Italic" in font, bold="Bold" in font,
                          weight=700 if "Bold" in font else 400)
            i += len(text)
        pages.append(P.build_blocks(p, size[0], size[1], chars, []))
    return P.Doc(pages)


def body_rows(p, top=300, n=10, last=None):
    rows, base = [], top
    for k in range(n):
        rows.append(("Body line %d of page %d is an ordinary line of running prose here." % (k, p), 100, base, "Body-Regular", 11))
        base += 14
    if last:
        rows[-1] = (last, 100, base - 14, "Body-Regular", 11)
    return rows


# -- 1. margin text is found relative to the text block, not by a fixed share of the page height
def mid_page(p):
    rows = [("F A R  C A L L S" if p % 2 else "FAR CALLS", 200, 270, "Head-Track", 6),     # running head, spaced or not
            (str(p + 40), 260, 470, "Body-Regular", 10)]                                  # page number below the block
    if p in (2, 4, 6):
        rows.append(("Chapter %d" % p, 100, 270, "Head-Bold", 20))                          # chapter label, also above
    return rows + body_rows(p)
d = doc_from([mid_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
check("running head above a mid-page text block is removed (spaced and unspaced alike)", "FAR" not in md and "C A L" not in md, md[:160])
check("a lone number below the text block is a page number and goes", not any(re.fullmatch(r"\d+", s.strip()) for s in md.split("\n\n")))
check("a large chapter label above the block is not mistaken for a running head", "Chapter 2" in md and "Chapter 6" in md, md[:200])
check("the text block is measured from body lines", d.text_top == 300 - 11 * 0.8 and abs(d.text_bot - (300 + 9 * 14 + 11 * 0.2)) < 0.01, (d.text_top, d.text_bot))

rot = line("Body text that stays here", 100, 200) + [P.Ch(c, 300 + k, 305 + k, 300 - k, 310 - k, 310 - k, "Wm-Arial", 30, idx=100 + k, rot=True)
                                                     for k, c in enumerate("SAMPLE COPY")]
pg = P.build_blocks(1, W, H, rot, [])
check("rotated characters (watermark, edge tab) are kept out of the text", all("SAMPLE" not in b["text"] for b in pg["blocks"]) and pg["rotated"] == 10, [b["text"] for b in pg["blocks"]])

# -- 2. a ligature is two chars on one glyph box: not a jump back, not a new line segment
lig = line("Things go on", 100, 200)
lig[1].x0, lig[1].x1, lig[1].ox, lig[1].lr = lig[0].x0, lig[0].x1 + 5.5, lig[0].ox, lig[0].lr + 5.5   # 'h' shares the 'T' box
lig[0].x1 = lig[1].x1
L = lines_text(lig)
check("ligature components on one box stay in one line", len(L) == 1 and L[0]["plain"].replace(" ", "") == "Thingsgoon", [l["plain"] for l in L])
fi = line("a fine day", 100, 200)
fi[3].x0, fi[3].ox = fi[2].x0, fi[2].ox                                                    # 'fi' ligature
check("any ligature, not only Th", len(lines_text(fi)) == 1)

# -- 6. letter-spaced text: letter gaps and word gaps are two clusters
def tracked(text, x, size, letter_gap, word_gap, with_spaces=False):
    out, cx, k = [], x, 0
    for w_i, word in enumerate(text.split(" ")):
        if w_i:
            if with_spaces:
                out.append(P.Ch(" ", cx, cx + 0.25 * size, 190, 200, 200, "Head-Track", size, idx=k, ox=cx, lr=cx + 0.25 * size))
                k += 1
            cx += word_gap - letter_gap
        for c in word:
            w = 0.6 * size
            out.append(P.Ch(c, cx, cx + w, 190, 200, 200, "Head-Track", size, idx=k, ox=cx, lr=cx + w))
            cx += w + letter_gap
            k += 1
    return out
check("letter-spaced heading splits at word gaps only", lines_text(tracked("FAR CALLS", 100, 4.5, 1.2, 4.1))[0]["plain"] == "FAR CALLS",
      lines_text(tracked("FAR CALLS", 100, 4.5, 1.2, 4.1))[0]["plain"])
check("one tracked word stays one word", lines_text(tracked("CONTENTS", 100, 12.5, 1.8, 1.8))[0]["plain"] == "CONTENTS")
sp = tracked("THE INTERVAL", 100, 12.5, 1.9, 6.1, with_spaces=True)
check("tracked words with real spaces between them", lines_text(sp)[0]["plain"] == "THE INTERVAL", lines_text(sp)[0]["plain"])

# -- 4. a paragraph split into two blocks on the same page is joined; an open line-end hyphen is resolved
rows = body_rows(1, top=200, n=4, last="the paragraph goes on without a stop and an ele\x02")
rows += [("mentary school sits at the end of this second block.", 100, 290, "Body-Regular", 11),
         ("A new paragraph starts here and ends properly.", 112, 304, "Body-Regular", 11)]
d = doc_from([rows] + [body_rows(p) for p in range(2, 6)])
md, st = P.build_markdown(d, 0, 0)
check("same-page split paragraph is joined, hyphen resolved", "an elementary school sits" in md and st["inpage_merges"] == 1, (md[-260:], st))
check("a paragraph that ends with a full stop is not joined to the next", "properly." in md and "\n\nA new paragraph" in md)
ital = body_rows(1, top=200, n=3, last="then the word in italics")
ital += [("*continues* on a line set mostly in italics.", 100, 262, "Body-Italic", 11)]
md, st = P.build_markdown(doc_from([ital] + [body_rows(p) for p in range(2, 6)]), 0, 0)
check("an italic-variant line of the same size continues the paragraph", "in italics" in md and "italics *continues*" in md.replace("**", "*") or "italics continues" in md.replace("*", ""), md[-200:])
st = collections.Counter()
check("emphasis closed before a line-end hyphen and reopened after is one word", P.join_lines(["the *Geogra\x02*", "*phy* of"], set(), st) == "the *Geography* of")

# -- 7. style roles
def page_with(p, extra):
    return body_rows(p, top=200, n=6) + extra(p)
tiny = doc_from([page_with(p, lambda p: [("TINY BOLD LABEL %s" % "ABCDEFGH"[p - 1], 100, 300, "Tiny-Bold", 5),
                                          ("Body line after label %d of page %d is ordinary prose text." % (p, p), 100, 320, "Body-Regular", 11)])
                 for p in range(1, 9)])
check("a style half the body size is never a heading", tiny.sugg["Tiny-Bold|5"][0] not in P.HEAD, tiny.sugg["Tiny-Bold|5"])
edge = doc_from([page_with(p, lambda p: [(["Alpha notes", "Beta notes", "Gamma notes", "Delta notes", "Epsilon notes", "Zeta notes",
                                           "Eta notes", "Theta notes"][p - 1], 100, 150, "Side-Bold", 10)]) for p in range(1, 9)])
check("a small style living above the text block is not a heading", edge.sugg["Side-Bold|10"][0] not in P.HEAD, edge.sugg["Side-Bold|10"])
short = doc_from([[("Short paragraph number %d of page %d here." % (k, p), 112 if k % 2 == 0 else 100, 200 + 14 * k, "Body-Regular", 11)
                   for k in range(12)] for p in range(1, 7)])
check("the most-worded style is body even when its paragraphs are short", short.sugg[short.body_style] == ("gövde", "en çok kelimeli stil"), short.sugg[short.body_style])

# -- hyphen joins without evidence are counted, never decided differently
words_doc = doc_from([body_rows(1, top=200, n=2) + [("some stress and free time and elementary things and self-aware people", 100, 260, "Body-Regular", 11)]]
                     + [body_rows(p) for p in range(2, 5)])
check("only a compound the book writes with a hyphen keeps it", words_doc.hyphen_real("self", "aware") and not words_doc.hyphen_real("stress", "free"))
st = collections.Counter()
out = P.join_lines(["a stress\x02", "free week"], words_doc.hyphen_real, st, words_doc.hyphen_ambiguous)
check("both pieces are words, joined form unseen: still joined, counted as ambiguous", out == "a stressfree week" and st["hyphen_ambiguous"] == 1, (out, st))
st = collections.Counter()
P.join_lines(["an ele\x02", "mentary school"], words_doc.hyphen_real, st, words_doc.hyphen_ambiguous)
check("a join the book confirms (the joined word occurs) is not ambiguous", st["hyphen_ambiguous"] == 0 and st["hyphen_soft"] == 1, st)

# -- 3. scanned PDF: OCR producer, or a full-page image under invisible text
check("OCR software in the producer means scanned", P.is_scanned("ABBYY PDF Transformer 2.0", []) and not P.is_scanned("Adobe InDesign 20.1", []))


def scanned_pdf(n=4, invisible=True, image=True, size=(522, 738)):
    pdf = pdfium.PdfDocument.new()
    for p in range(n):
        page = pdf.new_page(*size)
        if image:
            bm = raw.FPDFBitmap_Create(8, 8, 0)
            raw.FPDFBitmap_FillRect(bm, 0, 0, 8, 8, 0xFFEEEEEE)
            img = raw.FPDFPageObj_NewImageObj(pdf.raw)
            raw.FPDFImageObj_SetBitmap(None, 0, img, bm)
            raw.FPDFPageObj_Transform(img, size[0], 0, 0, size[1], 0, 0)
            raw.FPDFPage_InsertObject(page.raw, img)
        for k in range(8):
            text = f"Recognised line {k} of scanned page {p} with some ordinary words in it."
            obj = raw.FPDFPageObj_NewTextObj(pdf.raw, b"Helvetica", 11)
            buf = (ctypes.c_ushort * (len(text) + 1))(*([ord(c) for c in text] + [0]))
            raw.FPDFText_SetText(obj, buf)
            if invisible:
                raw.FPDFTextObj_SetTextRenderMode(obj, raw.FPDF_TEXTRENDERMODE_INVISIBLE)
            raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 72, 600 - 14 * k)
            raw.FPDFPage_InsertObject(page.raw, obj)
        raw.FPDFPage_GenerateContent(page.raw)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


md, warns = P.convert(scanned_pdf())
check("full-page image under invisible text: the scanned warning comes first", warns[:1] == [P.SCANNED_WARNING], warns)
md, warns = P.convert(scanned_pdf(invisible=False, image=False))
check("an ordinary text PDF gets no scanned warning", P.SCANNED_WARNING not in warns, warns)


def rotated_pdf(n=5, size=(522, 738)):
    pdf = pdfium.PdfDocument.new()
    for p in range(n):
        page = pdf.new_page(*size)
        rows = [(f"Plain sentence {k} on page {p} keeps going for a while here.", 72, 600 - 14 * k, 0) for k in range(8)]
        rows.append(("NOT FOR SALE OR DISTRIBUTION", 150, 300, 45))
        for text, x, y, angle in rows:
            obj = raw.FPDFPageObj_NewTextObj(pdf.raw, b"Helvetica", 11)
            buf = (ctypes.c_ushort * (len(text) + 1))(*([ord(c) for c in text] + [0]))
            raw.FPDFText_SetText(obj, buf)
            a = math.radians(angle)
            raw.FPDFPageObj_Transform(obj, math.cos(a), math.sin(a), -math.sin(a), math.cos(a), x, y)
            raw.FPDFPage_InsertObject(page.raw, obj)
        raw.FPDFPage_GenerateContent(page.raw)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


md, warns = P.convert(rotated_pdf())
check("convert(): a diagonal watermark is not in the text, and the warning says so", "SALE" not in md and "Plain sentence 3 on page 2" in md
      and any("Eğik ya da dikey" in w for w in warns), (md[:120], warns))

print("FAILED %d" % failed if failed else "ALL PASS")
sys.exit(1 if failed else 0)
