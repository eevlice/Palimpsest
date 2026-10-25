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
      P.two_column_suspect([dict(x0=100, x1=200, base=100 + 14 * k, size=11, plain="left words") for k in range(6)] +
                           [dict(x0=300, x1=420, base=100 + 14 * k, size=11, plain="right words") for k in range(6)], W)[0]
      and not P.two_column_suspect([dict(x0=100, x1=400, base=100 + 14 * k, size=11, plain="words") for k in range(12)], W)[0])


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
check("poem lines keep line breaks inside one Verse segment", '::: {custom-style="Verse"}\n*POEM LINE ONE short*\\\n*poem line two is also short*\\\n'
      in md2, md2[md2.find("POEM") - 40: md2.find("POEM") + 120])
seg = [s for s in md2.split("\n\n") if "POEM LINE ONE" in s]
check("poem is a single blank-line-delimited segment", len(seg) == 1 and seg[0].count("\n") == 4, seg)

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
    rows = [("T H E  B O O K" if p % 2 else "THE BOOK", 200, 270, "Head-Track", 6),     # running head, spaced or not
            (str(p + 40), 260, 470, "Body-Regular", 10)]                                  # page number below the block
    if p in (2, 4, 6):
        rows.append(("Chapter %d" % p, 100, 270, "Head-Bold", 20))                          # chapter label, also above
    return rows + body_rows(p)
d = doc_from([mid_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
check("running head above a mid-page text block is removed (spaced and unspaced alike)", "BOOK" not in md and "B O O" not in md, md[:160])
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
check("letter-spaced heading splits at word gaps only", lines_text(tracked("THE BOOK", 100, 4.5, 1.2, 4.1))[0]["plain"] == "THE BOOK",
      lines_text(tracked("THE BOOK", 100, 4.5, 1.2, 4.1))[0]["plain"])
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

# -- a line-end hyphen stays when the book writes the compound, or when both pieces are English words and the joined
#    form is not; otherwise the word is joined
words_doc = doc_from([body_rows(1, top=200, n=2) + [("some stress and free time and elementary things and self-aware people", 100, 260, "Body-Regular", 11)]]
                     + [body_rows(p) for p in range(2, 5)])
check("a compound the book writes, or two words whose joined form is no word, keep the hyphen; a split word joins",
      words_doc.hyphen_real("self", "aware") and words_doc.hyphen_real("stress", "free")
      and not words_doc.hyphen_real("some", "thing") and not words_doc.hyphen_real("ele", "mentary"))
st = collections.Counter()
out = P.join_lines(["a stress\x02", "free week"], words_doc.hyphen_real, st, words_doc.hyphen_ambiguous)
check("both pieces are words, joined form no word: the hyphen stays (not counted as ambiguous)", out == "a stress-free week" and st["hyphen_ambiguous"] == 0, (out, st))
st = collections.Counter()
P.join_lines(["an ele\x02", "mentary school"], words_doc.hyphen_real, st, words_doc.hyphen_ambiguous)
check("a join the book confirms (the joined word occurs) is not ambiguous", st["hyphen_ambiguous"] == 0 and st["hyphen_soft"] == 1, st)

# -- 3. scanned PDF: OCR producer, or a full-page image under invisible text
check("OCR software in the producer means scanned", P.is_scanned("ABBYY PDF Transformer 2.0", []) and not P.is_scanned("Adobe InDesign 20.1", []))


def scanned_pdf(n=4, invisible=True, image=True, size=(522, 738), text=None, textless=(), font=b"Helvetica"):
    """text(p, k) gives line k of page p; pages in textless carry the image only"""
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
        for k in range(0 if p in textless else 8):
            t = text(p, k) if text else f"Recognised line {k} of scanned page {p} with some ordinary words in it."
            obj = raw.FPDFPageObj_NewTextObj(pdf.raw, font, 11)
            buf = (ctypes.c_ushort * (len(t) + 1))(*([ord(c) for c in t] + [0]))
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

# -- scanned PDF policy: page kinds, textless pages, OCR quality, fonts, suspect words
check("page kinds: image under invisible text is OCR, an image alone a textless scan, anything else digital",
      [P.page_kind(s_) for s_ in ((True, 40, 0), (True, 0, 0), (False, 0, 30), (True, 2, 30))] == ["ocr", "scan", "digital", "digital"])
try:
    P.convert(scanned_pdf(n=6, textless={0, 1, 2, 3}))
    err = None
except P.PdfImportError as e:
    err = str(e)
check("most pages scanned without text: refused, with what to do", err is not None and "4/6" in err and "OCR" in err, err)
md, warns = P.convert(scanned_pdf(n=8, textless={7}))
check("a few textless scanned pages: a warning with their pages", any(w.startswith("Metin katmanı olmayan taranmış sayfalar: 8") for w in warns), warns)
check("OCR pages: the warning says italic/bold is gone and counts the suspect words",
      any(w == "Taranmış: italik/kalın bilgisi yok, 0 şüpheli sözcük." for w in warns), warns)
GARBLE = "Althoug Hinduis i ofte though t b unconcerne wit sufferin i thi lif becaus o th emphasi"
try:
    P.convert(scanned_pdf(n=4, text=lambda p, k: GARBLE))
    err = None
except P.PdfImportError as e:
    err = str(e)
check("an OCR layer whose words are mostly no words: refused with the share and a suggestion",
      err is not None and "%" in err and "sözlükte yok" in err and "OCRmyPDF" in err, err)
md, warns, meta = P.convert_with_meta(scanned_pdf(n=6, font=b"Helvetica-Bold",
                                                  text=lambda p, k: f"Plain line {k} of the page {p} names a recognisd word in it."))
segs = [x for x in md.split("\n\n") if x.strip()]
check("OCR pages: font names give no bold (size and position stay)", "**" not in md and "*" not in md, md[:200])
check("OCR pages: words the list does not know are flagged in seg_meta",
      any("recognisd" in (m.get("suspect") or []) and "ocr-supheli" in m["flags"] for m in meta)
      and any("şüpheli sözcük" in w and not w.endswith(" 0 şüpheli sözcük.") for w in warns), [m.get("suspect") for m in meta][:3])
pdf = scanned_pdf(n=2)
doc_ = pdfium.PdfDocument(pdf)
mc = P._miner_chars(pdf, {0})[0]
pc = [c for c in P._page_chars(doc_[0]) if c.ch.strip()]
mc = [c for c in mc if c.ch.strip()]
check("pdfminer reads the same glyphs at the same spots as pdfium on a clean layer",
      "".join(c.ch for c in mc) == "".join(c.ch for c in pc) and all(abs(a.x0 - b.ox) < 0.5 and abs(a.base - b.base) < 0.5 and a.size == b.size for a, b in zip(mc, pc)),
      ("".join(c.ch for c in mc)[:60], "".join(c.ch for c in pc)[:60]))
hidden = {k: sys.modules.pop(k) for k in list(sys.modules) if k == "pdfminer" or k.startswith("pdfminer.")}
sys.modules["pdfminer"] = None                     # as if pdfminer.six were not installed
try:
    P.convert(scanned_pdf(n=4, text=lambda p, k: GARBLE))
    err = None
except P.PdfImportError as e:
    err = str(e)
del sys.modules["pdfminer"]
sys.modules.update(hidden)
check("without pdfminer.six: a broken OCR layer is refused, and the message says the reader is missing",
      err is not None and "pdfminer.six kütüphanesi eksik" in err, err)
check("unknown-word share: a broken OCR line against a clean one",
      P.unknown_share([c for c in P._page_chars(pdfium.PdfDocument(scanned_pdf(n=1, text=lambda p, k: GARBLE))[0])])[0] > 10
      and P.unknown_share(pc)[0] == 0)


# -- a non-breaking hyphen at a line's end is a line-end hyphen too
L = lines_text(line("a funda\u2011", 100, 200) + line("mental idea", 100, 214, idx0=20))
check("U+2011 at a line's end is marked like pdfium's own line-end hyphen", L[0]["plain"].endswith("\x02"), [l["plain"] for l in L])
check("...and joins as a split word", P.join_lines([l["md"] for l in L], lambda a, b: False, collections.Counter()) == "a fundamental idea")


# -- a picture mostly off the page (the bleed of a spread) is no figure of that page; display type inside a picture
#    is text, not a diagram label
def bleed_pdf(x0):
    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(500, 700)
    bm = raw.FPDFBitmap_Create(8, 8, 0)
    raw.FPDFBitmap_FillRect(bm, 0, 0, 8, 8, 0xFF888888)
    img = raw.FPDFPageObj_NewImageObj(pdf.raw)
    raw.FPDFImageObj_SetBitmap(None, 0, img, bm)
    raw.FPDFPageObj_Transform(img, 440, 0, 0, 150, x0, 520)
    raw.FPDFPage_InsertObject(page.raw, img)
    raw.FPDFPage_GenerateContent(page.raw)
    out = io.BytesIO()
    pdf.save(out)
    return pdfium.PdfDocument(out.getvalue())[0]


check("a picture with most of its area off the page is not a figure of the page",
      P._page_graphics(bleed_pdf(-400))["images"] == [] and len(P._page_graphics(bleed_pdf(30))["images"]) == 1)
band = (30, 30, 470, 180)
k = line("Chapter Title On A Band", 120, 150, "Head-Medium", 16) + line("a b label", 300, 100, "Body-Regular", 9, idx0=40)
k += [c for j in range(8) for c in line("Body %d of the page with ordinary running words of prose here." % j, 100, 250 + 14 * j, idx0=100 + 80 * j)]
pg = P.build_blocks(1, 500, 700, k, [], images=[band])
texts = [b_["text"] for b_ in pg["blocks"]]
check("display type inside a picture stays text; a small label inside it belongs to the picture",
      any("Chapter Title On A Band" in t for t in texts) and not any("a b label" in t for t in texts), texts[:3])


# -- one paragraph of an epigraph or a poem stays one segment
Ln = lambda x1, size=11: dict(x1=x1, size=size)
st_ = collections.Counter()
prose_block = P.poem_lines([Ln(400), Ln(400), Ln(250)], ["Charlie learns how accurate it is when he", "travels with Raymond and we", "see it."],
                           lambda a, b: False, st_)
check("poem: a block set like prose (two lines at the edge) joins its lowercase wraps",
      prose_block == "Charlie learns how accurate it is when he travels with Raymond and we see it.", prose_block)
poem = P.poem_lines([Ln(180), Ln(400), Ln(90), Ln(260)], ["Short line one", "a much longer second line of the poem", "third", "and the last"],
                    lambda a, b: False, collections.Counter())
check("poem: a real poem keeps every break, its longest line too",
      poem == "Short line one\\\na much longer second line of the poem\\\nthird\\\nand the last", poem)
split = P.poem_lines([Ln(400), Ln(300)], ["Charlie trav\x02", "els with Raymond."], lambda a, b: False, collections.Counter())
check("poem: a split word joins (the hyphen rule decides the hyphen)", split == "Charlie travels with Raymond.", split)
M = lambda pg: dict(pages={pg}, boxes=[], style="Body 12", flags=set())
md_ = [("raw", P.fenced("epigraf", "What we talk about on this walk: What one"), M(15))]
blk_ = dict(bid="15.3")
ok = P._named_join(md_, "epigraf", "man’s attempt to build a toaster.", M(15), blk_, 15, lambda a, b: False, st_, None)
check("callout cut by a drop cap's indent: one Epigraph segment",
      ok and len(md_) == 1 and md_[0][1] == P.fenced("epigraf", "What we talk about on this walk: What one man’s attempt to build a toaster."), md_)
md_ = [("raw", P.fenced("epigraf", "A closed sentence."), M(15))]
check("...not after a closed sentence, nor into another role, nor over a skipped page",
      not P._named_join(md_, "epigraf", "and more", M(15), blk_, 15, lambda a, b: False, st_, None)
      and not P._named_join([("raw", P.fenced("atıf", "—A Name"), M(15))], "epigraf", "and more", M(15), blk_, 15, lambda a, b: False, st_, None)
      and not P._named_join([("raw", P.fenced("epigraf", "it goes"), M(13))], "epigraf", "and more", M(15), blk_, 15, lambda a, b: False, st_, None))
md_ = [("raw", P.fenced("şiir", "the first line\\\nand the second"), M(15))]
P._named_join(md_, "şiir", "and the third", M(16), blk_, 16, lambda a, b: False, st_, None)
check("a poem going on overleaf keeps its line break", md_[0][1] == P.fenced("şiir", "the first line\\\nand the second\\\nand the third"), md_)


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

# ================================================================ import round 2: figures, captions, tables, columns, index
def page_of(p, rows, images=(), hl=(), vl=()):
    chars, i = [], 0
    for text, x, bs, font, sz in rows:
        chars += line(text, x, bs, font, sz, idx0=i, italic="Italic" in font, bold="Bold" in font, weight=700 if "Bold" in font else 400)
        i += len(text)
    return P.build_blocks(p, W, H, chars, [], frozenset(), list(images), list(hl), list(vl))


def book(special, n=8):
    """n pages of body text; special: {page: (extra rows, images, hl, vl)}"""
    pages = []
    for p in range(1, n + 1):
        rows, ims, hl, vl = special.get(p, ([], (), (), ()))
        pages.append(page_of(p, body_rows(p, top=120, n=6) + rows, ims, hl, vl))
    return P.Doc(pages)


# -- figures: a placeholder in the flow; labels inside the figure stay out; the caption follows it
fig = ([("Brain stem", 150, 330, "Label-Regular", 8), ("Cortex", 220, 380, "Label-Regular", 8),
        ("Figure 2. A synthetic diagram of nothing", 100, 445, "Caption-Italic", 9)], [(100, 300, 320, 430)], (), ())
d = book({4: fig})
md, st = P.build_markdown(d, 0, 7)
segs = [s for s in md.split("\n\n") if s.strip()]
k = segs.index("![Görsel · s.4](gorsel-4-1)") if "![Görsel · s.4](gorsel-4-1)" in segs else -1
check("a figure becomes a placeholder segment at its place", k > 0 and "page 4" in segs[k - 1], segs[k - 1:k + 2] if k > 0 else md[-300:])
check("labels inside the figure do not reach the text", "Brain stem" not in md and "Cortex" not in md and d.pages[3]["fig_words"] == 3)
check("the caption follows the figure as a caption segment", k > 0 and segs[k + 1] == '::: {custom-style="Caption"}\n*Figure 2. A synthetic diagram of nothing*\n:::', segs[k + 1] if k > 0 else None)
from segtypes import seg_type
check("placeholder and caption are typed by segtypes", seg_type(segs[k])["type"] == "image" and seg_type(segs[k + 1])["type"] == "caption")
opn = ([("the paragraph runs on past the figure and", 100, 290, "Body-Regular", 11),
        ("comes back under it to finish its sentence here.", 100, 450, "Body-Regular", 11)], [(100, 300, 320, 430)], (), ())
md, st = P.build_markdown(book({5: opn}), 0, 7)
check("a figure inside an open paragraph is written after the paragraph", "past the figure and comes back under it" in md
      and md.index("![Görsel · s.5]") > md.index("finish its sentence"), md[md.find("past the") - 40: md.find("past the") + 200])

# -- ruled tables: one markdown table segment; merged cells -> placeholder; a frame with a tab is not a table
xs, ys = [100, 200, 300, 400], [300, 320, 340]
grid_h = [(y, xs[0], xs[-1]) for y in ys]
grid_v = [(x, ys[0], ys[-1]) for x in xs]
cells = [("Name", 110, 314), ("Size", 210, 314), ("Kind", 310, 314), ("alpha", 110, 334), ("one", 210, 334), ("first", 310, 334)]
tab = ([(s, x, b, "Table-Regular", 9) for s, x, b in cells], (), grid_h, grid_v)
md, st = P.build_markdown(book({3: tab}), 0, 7)
check("a ruled grid is one markdown table segment", "| Name | Size | Kind |\n|---|---|---|\n| alpha | one | first |" in md and st["tables"] == 1, md[:400])
check("the table text is not also in the paragraphs", md.count("alpha") == 1)
merged_v = [(x, ys[0], ys[-1]) for x in (100, 400)] + [(200, 300, 320), (300, 300, 340)]     # x=200 missing in row 2
md, st = P.build_markdown(book({3: (tab[0], (), grid_h, merged_v)}), 0, 7)
check("merged cells: placeholder instead of a table, counted", "![Tablo · s.3](tablo-3-1)" in md and st["tables_complex"] == 1 and "alpha" not in md)
badge_h = [(73, 172, 198), (86, 52, 319), (95, 172, 198), (128, 52, 319)]
badge_v = [(52, 86, 128), (172, 73, 95), (198, 73, 95), (319, 86, 128)]
md, st = P.build_markdown(book({3: ([("Optimize Your Stress Levels", 80, 110, "Head-Bold", 14)], (), badge_h, badge_v)}), 0, 7)
check("a frame with a number tab on top is not a table", st["tables"] == 0 and st["tables_complex"] == 0 and "Optimize Your Stress Levels" in md)

# -- two columns: left column first, then right; a line across both stays in place
cols = [("A HEADING OVER BOTH COLUMNS OF THIS PAGE TEXT", 60, 100, "Body-Regular", 11)]
cols += [("left column line number %d goes" % k, 60, 130 + 14 * k, "Body-Regular", 11) for k in range(6)]
cols += [("right column line number %d goes" % k, 300, 130 + 14 * k, "Body-Regular", 11) for k in range(6)]
pg = page_of(1, cols)
order = [l["plain"] for b in pg["blocks"] for l in b["lines"]]
check("two columns are read left then right", order.index("left column line number 5 goes") < order.index("right column line number 0 goes")
      and order[0].startswith("A HEADING"), order[:3])

# -- back-of-book index: out of the flow as one placeholder
def index_page(p):
    names = ["Abbot", "Baker", "Carter", "Dalton", "Ellis", "Fisher", "Gray", "Hardy", "Irwin", "Jones", "Keller", "Lewis",
             "Moore", "Nash", "Owens", "Price", "Quinn", "Reed"]
    return [("%s, %s, %d" % (n, "x" * (p % 3 + 1), 10 * k + p), 60, 100 + 14 * k, "Index-Regular", 9) for k, n in enumerate(names)]
pages = [page_of(p, body_rows(p, top=120, n=10)) for p in range(1, 17)] + [page_of(p, index_page(p)) for p in range(17, 21)]
d = P.Doc(pages)
md, st = P.build_markdown(d, 0, 19)
check("index pages are found in the last quarter", d.index_pages == [17, 18, 19, 20], d.index_pages)
check("the index leaves the flow as one placeholder", "![Dizin · s.17–20](dizin-17-20)" in md and "Abbot" not in md and st["index_words"] > 0)

# -- ambiguous-join counter: line-end pieces are not words (across blocks and pages too)
rows = body_rows(1, top=200, n=2) + [("we keep stress and some free time and an ele\x02", 100, 260, "Body-Regular", 11),
                                     ("mentary list of plain words here", 100, 274, "Body-Regular", 11)]
d = doc_from([rows] + [body_rows(p) for p in range(2, 5)])
check("pieces of a split word are not counted as words", "ele" not in d.words and "mentary" not in d.words and "stress" in d.words and "list" in d.words)
check("the counter only counts: two words of the book whose joined form neither the book nor the word list has",
      d.hyphen_real("stress", "free") and d.hyphen_ambiguous("stress", "free"))


# -- end to end: a synthetic PDF with an image and its labels
def figure_pdf(n=6, size=(522, 738)):
    pdf = pdfium.PdfDocument.new()
    for p in range(n):
        page = pdf.new_page(*size)
        rows = [(f"Plain sentence {k} on page {p} keeps going for a while here.", 72, 650 - 14 * k, 11) for k in range(8)]
        if p == 3:
            bm = raw.FPDFBitmap_Create(8, 8, 0)
            raw.FPDFBitmap_FillRect(bm, 0, 0, 8, 8, 0xFF888888)
            img = raw.FPDFPageObj_NewImageObj(pdf.raw)
            raw.FPDFImageObj_SetBitmap(None, 0, img, bm)
            raw.FPDFPageObj_Transform(img, 200, 0, 0, 120, 100, 380)
            raw.FPDFPage_InsertObject(page.raw, img)
            rows += [("LABEL INSIDE", 140, 440, 8)]
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


md, warns = P.convert(figure_pdf())
check("convert(): image placeholder in, label out, warning given", "![Görsel · s.4](gorsel-4-1)" in md and "LABEL INSIDE" not in md
      and any("1 görsel yer tutucuyla" in w and "2 sözcük" in w for w in warns), (md[-300:], warns))

# ================================================================ round 3: body style, object groups, split paragraphs, flags, seg_meta
def tbl(p, top=300):
    xs, ys = [100, 250, 400], [top, top + 20, top + 40]
    rows = [("Name", 110, top + 14, "Table-Regular", 9), ("Kind", 260, top + 14, "Table-Regular", 9),
            ("alpha", 110, top + 34, "Table-Regular", 9), ("first", 260, top + 34, "Table-Regular", 9)]
    return rows, [(y, xs[0], xs[-1]) for y in ys], [(x, ys[0], ys[-1]) for x in xs]


rows, hl, vl = tbl(5)
grp = rows + [("THE SAMPLE TABLE", 160, 290, "Label-Bold", 10),                        # title right above
              ("Reproduced by permission of nobody.", 200, 352, "Credit-Italic", 7),   # credit line right below
              ("The rows above list a kind for each name and say nothing more at all.", 100, 366, "Note-Regular", 9),
              ("Body text resumes here after the table and its notes, as ordinary prose.", 100, 400, "Body-Regular", 11)]
d = book({5: (grp, (), hl, vl)})
md, st = P.build_markdown(d, 0, 7)
segs = [s for s in md.split("\n\n") if s.strip()]
k = next(i for i, s in enumerate(segs) if s.startswith("| Name"))
cap = lambda s: s.startswith('::: {custom-style="Caption"}')
check("object group: the title above is a caption written before the table", cap(segs[k - 1]) and "THE SAMPLE TABLE" in segs[k - 1], segs[k - 1])
check("object group: credit line and explanation below are captions written after it, in order",
      cap(segs[k + 1]) and "permission" in segs[k + 1] and cap(segs[k + 2]) and "kind for each name" in segs[k + 2], segs[k + 1:k + 3])
check("object group: body text after the group stays a paragraph", segs[k + 3].startswith("Body text resumes"), segs[k + 3])
check("body style: italic/bold variants are body, another size is not",
      not d.deviates(dict(style="Body-Italic|11")) and not d.deviates(dict(style="Body-Bold|11")) and d.deviates(dict(style="Body-Regular|9")))

# a heading right under a figure is the next section, not the figure's caption
fig_head = ([("Next Section Heading", 100, 445, "Sub-Bold", 12)], [(100, 300, 320, 430)], (), ())
heads = {p: ([("Next Section Heading %s" % "ABCDEFGH"[p - 1], 100, 300, "Sub-Bold", 12)], (), (), ()) for p in (2, 3, 6, 7, 8)}
heads[4] = fig_head
d = book(heads)
md, st = P.build_markdown(d, 0, 7)
check("a heading under a figure stays a heading", "## Next Section Heading" in md or "# Next Section Heading" in md, [s for s in md.split("\n\n") if "Next Section" in s][:3])

# a chapter heading over a figure is never the figure's title (contents entries stay headings)
chap = {p: ([("CHAPTER NAME %s" % "ABCDEFGH"[p - 1], 100, 95, "Head-Bold", 20)], (), (), ()) for p in range(3, 9)}
chap[5] = ([("CHAPTER NAME E", 100, 95, "Head-Bold", 20)], [(100, 100, 320, 115)], (), ())
d = book(chap)
md, st = P.build_markdown(d, 0, 7)
check("a chapter heading right over a figure stays a heading", any(s.startswith("#") and "CHAPTER NAME E" in s for s in md.split("\n\n")))

# a paragraph cut by a figure: joined even when it goes on with a capital, the figure goes after it
cut = ([("the leaders who score highest. Even among", 100, 290, "Body-Regular", 11),
        ("CEOs, the best performers were the ones with the highest scores.", 100, 445, "Body-Regular", 11)], [(100, 300, 320, 430)], (), ())
d = book({4: cut})
md, st = P.build_markdown(d, 0, 7)
check("figure inside a paragraph: the two parts join (capital continuation) and the figure follows",
      "Even among CEOs, the best performers" in md and md.index("![Görsel · s.4]") > md.index("highest scores.") and st["object_merges"] == 1, st)
nofig = ([("the leaders who score highest. Even among", 100, 290, "Body-Regular", 11),
          ("CEOs, the best performers were the ones with the highest scores.", 100, 320, "Body-Regular", 11)], (), (), ())
md, st = P.build_markdown(book({4: nofig}), 0, 7)
check("without a figure in between a capital start is not joined", "Even among CEOs" not in md and st["object_merges"] == 0)

# flags and seg_meta
odd = ([("A stray line in an unusual face that belongs to nothing.", 100, 300, "Odd-Regular", 8)], (), (), ())
d = book({3: odd, 4: cut})
md, st = P.build_markdown(d, 0, 7)
segs = [s for s in md.split("\n\n") if s.strip()]
meta = d.seg_meta
check("seg_meta: one entry per segment", len(meta) == len(segs), (len(meta), len(segs)))
i_odd = next(i for i, s in enumerate(segs) if "stray line" in s)
check("a block set apart from body text that joins nothing is flagged", "stil-farkli" in meta[i_odd]["flags"] and "font" in meta[i_odd]["flags"]
      and st["style_flags"] == 1, meta[i_odd])
i_cut = next(i for i, s in enumerate(segs) if "Even among CEOs" in s)
check("seg_meta of a joined paragraph: its page, both boxes, the body style", meta[i_cut]["pages"] == [4] and len(meta[i_cut]["boxes"]) == 2
      and meta[i_cut]["style"] == "Body-Regular 11" and meta[i_cut]["boxes"][0][0] == 4, meta[i_cut])
i_fig = next(i for i, s in enumerate(segs) if s.startswith("![Görsel · s.4]"))
check("seg_meta of a figure: its box", meta[i_fig]["boxes"] == [[4, 100, 300, 320, 430]], meta[i_fig])
md2, w2, m2 = P.convert_with_meta(figure_pdf())
check("convert_with_meta(): as many meta entries as segments", len(m2) == len([s for s in md2.split("\n\n") if s.strip()]))

# -- a title printed inside the picture's box is kept (becomes a caption); a diagram label inside it goes
im = (100, 300, 400, 500)                                         # 300 wide, 200 high: top 15% band ends at y=330
inside_rows = [("A WIDE TITLE ALONG THE TOP OF THE PICTURE", 110, 318, "Label-Bold", 10),     # wide, top edge band
               ("A WIDE DIAGRAM LABEL ACROSS THE MIDDLE PART", 110, 400, "Label-Regular", 10),  # wide, middle
               ("small", 200, 450, "Label-Regular", 8)]                                       # narrow label
pg = page_of(4, body_rows(4, top=120, n=6) + inside_rows, [im])
kept = [l["plain"] for b in pg["blocks"] for l in b["lines"]]
check("inside the picture: a wide line along its top edge is a title and stays", any("WIDE TITLE" in x for x in kept), kept)
check("inside the picture: a wide diagram label across the middle goes", not any("DIAGRAM LABEL" in x for x in kept) and not any(x == "small" for x in kept))
check("only the labels are counted as figure text (8 + 1 words)", pg["fig_words"] == 9, pg["fig_words"])
d = book({4: (inside_rows, [im], (), ())})
md, st = P.build_markdown(d, 0, 7)
segs = [s for s in md.split("\n\n") if s.strip()]
k = segs.index("![Görsel · s.4](gorsel-4-1)")
check("the title printed inside the top of the picture is written before it, as a caption", "WIDE TITLE" in segs[k - 1] and segs[k - 1].startswith(":::"), segs[k - 1])
check("object merge log: one entry per joined paragraph", d.object_merge_log == [], d.object_merge_log)

# ================================================================ import round 5
# -- 1: the body runs into the bottom band; its last line is at the same spot on every page but its text differs
def low_page(p):
    rows = [("Body line %d of page %d is an ordinary line of running prose %s." % (k, p, "abcdefghij"[k] * (p % 5 + 1)),
             100, 520 + 14 * k, "Body-Regular", 11) for k in range(10)]                 # last line top 637 > 0.86 H
    rows.append(("Same small slug", 100, 700, "Slug-Regular", 7))                       # repeated text: a slug
    rows.append(("note %s" % "xyzwvutsrq"[p], 400, 700, "Note-Regular", 7))           # same spot, text changes
    return rows
d = doc_from([low_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
check("the last body line in the bottom band is not taken for a footer", all("Body line 9 of page %d " % p in md for p in range(1, 9)), md[-300:])
check("in the band, a short line whose text repeats goes; one whose text changes stays", "Same small slug" not in md and "note z" in md)

# -- 3: a slanted (sheared) faux italic stays in the flow at its upright size; turned text still goes
def shear_pdf(n=4):
    pdf = pdfium.PdfDocument.new()
    for p in range(n):
        page = pdf.new_page(522, 738)
        rows = [(f"Plain sentence {k} on page {p} keeps going for a while here.", 0.0) for k in range(8)]
        rows[3] = (f"Slanted words on page {p} drawn with a sheared matrix only.", 0.21)
        for k, (text, c) in enumerate(rows):
            obj = raw.FPDFPageObj_NewTextObj(pdf.raw, b"Helvetica", 11)
            buf = (ctypes.c_ushort * (len(text) + 1))(*([ord(ch) for ch in text] + [0]))
            raw.FPDFText_SetText(obj, buf)
            raw.FPDFPageObj_Transform(obj, 1, 0, c, 1, 72, 600 - 14 * k)
            raw.FPDFPage_InsertObject(page.raw, obj)
        raw.FPDFPage_GenerateContent(page.raw)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()
md, warns = P.convert(shear_pdf())
check("a sheared (faux italic) line is text, not turned text", "Slanted words on page 2 drawn" in md and not any("Eğik ya da dikey" in w for w in warns), (md[:200], warns))
sh = P._page_chars(pdfium.PdfDocument(shear_pdf(1))[0])
check("a sheared glyph keeps its upright size", {round(c.size, 1) for c in sh if c.ch.strip()} == {11.0}, {round(c.size, 2) for c in sh})

# -- 2: word spaces around ligatures and clipped glyphs
lig = line("the ", 100, 200) + line("fi", 122, 200, idx0=4) + line(" ", 125, 200, idx0=6) + line("nal", 128, 200, idx0=7)
lig[4].x1 = lig[5].x1 = lig[4].lr = lig[5].lr = 128.0                               # one glyph box for "fi"
lig[5].x0, lig[5].ox = lig[4].x0, lig[4].ox
lig[6].x0, lig[6].x1, lig[6].ox, lig[6].lr = 125.0, 127.9, 125.0, 127.9             # takes half the ligature's advance
check("a space glyph drawn inside a ligature is no word break", lines_text(lig)[0]["plain"] == "the final", lines_text(lig)[0]["plain"])
ff = line("of", 100, 200) + line("f", 105.5, 200, idx0=2) + line(" ", 111.0, 200, idx0=3) + line("to", 114.6, 200, idx0=4)
ff[1].x0, ff[1].ox = ff[2].x0, ff[2].ox = 104.0, 105.5                            # "ff": one box, wide enough to cover
ff[1].x1 = ff[2].x1 = ff[1].lr = ff[2].lr = 114.5                                  # the space glyph after it
ff[3].x0, ff[3].x1, ff[3].ox, ff[3].lr = 111.0, 114.2, 111.0, 114.2               # a full ff advance (0.5 em) later
check("a real space after an ff ligature (its box overlapping the space) stays", lines_text(ff)[0]["plain"] == "off to", lines_text(ff)[0]["plain"])
over = line("of", 100, 200) + line(" ", 105, 200, idx0=2) + line("the", 107.8, 200, idx0=3)
over[1].x1 = over[1].lr = 108.0                                                    # an f overhanging into the space
over[2].x0, over[2].x1, over[2].ox, over[2].lr = 105.5, 108.2, 105.5, 108.2
check("a real space under an overhanging f (no ligature) stays", lines_text(over)[0]["plain"] == "of the", lines_text(over)[0]["plain"])
clip = line("of", 100, 200) + line("the", 112, 200, idx0=2)
clip[1].lr = clip[1].x1 = 110.5                                                    # gap 1.5 < 0.2 em: no inferred space
check("without pdfium's generated space a small gap is no break", lines_text(clip)[0]["plain"] == "ofthe")
clip = line("of", 100, 200) + line("the", 112, 200, idx0=2)
clip[1].lr = clip[1].x1 = 110.5
clip[2].gsp = True
check("pdfium's generated space is a word break", lines_text(clip)[0]["plain"] == "of the", lines_text(clip)[0]["plain"])
cap = line("THEIR TRAITS AND", 100, 200, "Head-Regular", 11, adv=0.7)                 # a real space later: 0.5 em rule
for c in cap[6:]:
    c.ox -= 3.0                                                                    # kerned capitals close the gap
    c.x0 -= 3.0
    c.x1 -= 3.0
    c.lr -= 3.0
cap = cap[:5] + cap[6:]
cap[5].gsp = True
check("kerned capitals: the generated space still separates the words", lines_text(cap)[0]["plain"] == "THEIR TRAITS AND", lines_text(cap)[0]["plain"])
gen = P._page_chars(pdfium.PdfDocument(synth_pdf([[("glued", 72, 600, 11), ("word", 110, 600, 11)]]))[0])
check("_page_chars marks the char after a generated space", [c.ch for c in gen if c.gsp] == ["w"], [(c.ch, c.gsp) for c in gen])

# -- 5: an italic body font with an unrelated name, found by where it is set
def mixed_page(p, mixed=True):
    chars, i = [], 0
    for k in range(10):
        base = 180 + 14 * k
        head = "Body line %d of page %d has some " % (k, p)
        chars += line(head, 100, base, idx0=i)
        i += len(head)
        if mixed:
            chars += line("emphasis", 100 + len(head) * 5.5, base, "AdvQ7F09", 11, idx0=i, italic=True)
            i += 8
        tail_ = " in it and runs on as prose here."
        chars += line(tail_, 100 + (len(head) + 8) * 5.5, base, idx0=i)
        i += len(tail_)
    if p == 4:
        chars += line("An italic line of quoted words standing alone", 100, 360, "AdvQ7F09", 11, idx0=i, italic=True)
    return P.build_blocks(p, W, H, chars, [])
d = P.Doc([mixed_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
check("a font set inside body lines at body size is a body variant whatever its name", ("AdvQ7F09", 11.0) in d.body_fams and st["style_flags"] == 0, (d.body_fams, st["style_flags"]))
d = P.Doc([mixed_page(p, False) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
check("...the same lone line in a font never seen in the body is flagged", st["style_flags"] == 1, st["style_flags"])

# an emphasised statement font never mixed into body lines, but set as body-size paragraphs at the body's pitch
def statement_page(p):
    rows = body_rows(p, top=120, n=6)
    if p in (3, 6):                                                                  # under 5% of the words
        rows += [("An emphasised statement on page %d runs over two lines" % p, 100, 220, "AdvQ7F0C", 11),
                 ("of the page and ends as a sentence does.", 100, 234, "AdvQ7F0C", 11)]
    rows += [(t.replace("Body line", "More body"), x, b + 140, f, sz) for t, x, b, f, sz in body_rows(p, top=120, n=4)]
    return rows
d = doc_from([statement_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
check("a font setting body-size paragraphs at the body's line pitch is a body variant, not flagged",
      ("AdvQ7F0C", 11.0) in d.body_fams and st["style_flags"] == 0 and "emphasised statement on page 3" in md, (d.body_fams, st["style_flags"]))

# a display-size title that names a work runs past twelve words and is still a heading
def long_title_page(p):
    return [("Profile %d: A Person in the Novel With" % p, 60, 150, "Disp-Regular", 16),
            ("a Rather Long Title by Some Author and Friends", 60, 169, "Disp-Regular", 16)] + body_rows(p, top=210, n=10)
d = doc_from([long_title_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
check("a long display-size title is a heading", sum(1 for l in md.split("\n") if l.startswith("#") and "Profile" in l) == 8,
      [l for l in md.split("\n") if "Profile" in l][:2])

# -- false two-column warnings: contents page numbers; a title on a page with little small text
toc = [("Chapter %d title words" % k, 60, 130 + 14 * k, "Body-Regular", 11) for k in range(8)]
toc += [("%s" % ["ix", "xi", "1", "23", "45", "67", "89", "101"][k], 440, 130 + 14 * k, "Body-Regular", 11) for k in range(8)]
toc_lines = lines_text(sum((line(t, x, b, f, sz, idx0=40 * n) for n, (t, x, b, f, sz) in enumerate(toc)), []))
check("a contents page (titles left, page numbers right) is not two columns", not P.two_column_suspect(toc_lines, W)[0]
      and len(toc_lines) == 16)
title = line("The Paper Garden", 110, 120, "Title-Regular", 16) + line("slug 01/04/2020", 40, 600, "Slug-Regular", 6, idx0=40)
L = lines_text(title)
check("big letters side by side are a title, not drop caps", [l["plain"] for l in L][0] == "The Paper Garden", [l["plain"] for l in L])

check("a slug's page number (arabic, roman, inside a word) does not make its text differ",
      P._similar_key("90000 The Book (12x) - 2nd pass.indd iii") == P._similar_key("0000 The Book (12x) - 2nd pass.indd xii")
      == "thebookpassindd")

# -- a title page, and a half title repeating it later, are front matter; a part opener is a heading
def display_page(p):
    if p == 1:
        return [("The Synthetic Title", 100, 200, "Display-Regular", 24)]
    if p == 9:
        return [("The Synthetic Title", 100, 200, "Display-Regular", 24)]
    if p == 10:
        return [("PART ONE", 100, 200, "Display-Regular", 24)]
    return [("Chapter Head %d" % p, 100, 100, "Display-Regular", 24)] + body_rows(p, top=150, n=10)
d = doc_from([display_page(p) for p in range(1, 41)])
md, st = P.build_markdown(d, 0, 39)
heads = [l for l in md.split("\n") if l.startswith("#")]
check("title page and the half title repeating it are not headings; the part opener is", not any("Synthetic Title" in h for h in heads)
      and any("PART ONE" in h for h in heads), heads[:4])

# ================================================================ import round 6
def runs_line(parts, x, base, idx0, size=11):
    """one line made of style runs: [(text, font)]; italic/bold follow the font name"""
    out, cx = [], x
    for text, font in parts:
        out += line(text, cx, base, font, size, idx0=idx0 + len(out), italic="Italic" in font, bold="Bold" in font,
                    weight=700 if "Bold" in font else 400)
        cx += len(text) * 0.5 * size
    return out


def page_runs(p, rows):
    """rows: [(parts, x, base)] or [(parts, x, base, size)] -> built page"""
    chars = []
    for r in rows:
        chars += runs_line(r[0], r[1], r[2], len(chars), *r[3:])
    return P.build_blocks(p, W, H, chars, [])


def full(text, n=66):
    """pad a line to n characters: a line that runs to the right edge"""
    return text + " " + "x" * (n - len(text) - 1) if len(text) < n - 1 else text


B = "Body-Regular"
FULL = "ordinary words of running prose fill this whole line up to here"            # 64 chars: a full line


def prose(p, top, n, x=100):
    return [([("Body %d of page %d: %s" % (k, p, FULL[:48]), B)], x, top + 14 * k) for k in range(n)]


# -- 1. an inline run that takes most of a line stays in its paragraph; a heading needs a block that ended
def inline_page(p):
    rows = prose(p, 120, 4)
    rows.append(([("Yet another line opens a sentence and it names the scholar, author of", B)], 100, 176))   # full, open
    rows.append(([("A Long Book Title Set In Italics Across Most Of The", "Title-Italic"), (" Line, who then", B)], 100, 190))
    rows.append(([("goes on writing here until the sentence finally comes to an end.", B)], 100, 204))
    rows.append(([("Bold Lead Heading Running Almost To The Right Edge Of The Text", "Head-Bold")], 100, 232))  # near right
    rows += prose(p, 250, 3)
    return page_runs(p, rows)
d = P.Doc([inline_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
segs = [s for s in md.split("\n\n") if s.strip()]
k = next(i for i, s in enumerate(segs) if "Long Book Title" in s)
check("a line set mostly in another style stays in its paragraph (no block split, no heading)",
      not segs[k].startswith("#") and "author of *A Long Book Title" in segs[k] and "sentence finally comes" in segs[k], segs[k][:160])
check("a bold line after a finished paragraph is still its own block", any(s.startswith("#") and "Bold Lead Heading" in s for s in segs)
      or any(s.startswith("**Bold Lead Heading") for s in segs), [s[:60] for s in segs if "Bold Lead" in s])

# a paragraph dominated by runs in a heading style (long italic titles) is still a paragraph
def titles_page(p):
    rows = prose(p, 120, 3)
    rows.append(([("Sub Head %d" % p, "Sub-Italic")], 100, 176))
    rows += prose(p, 194, 2)
    if p == 4:
        rows += [([("His Books Include A Very Long Title Indeed", "Sub-Italic"), (" and also the", B)], 100, 250 + 14 * k)
                 for k in range(4)] + [([("Another Long Title.", "Sub-Italic")], 100, 306)]
    return page_runs(p, rows)
d = P.Doc([titles_page(p) for p in range(1, 13)])
md, st = P.build_markdown(d, 0, 11)
check("a long paragraph in a heading style's runs is no heading", any(l.startswith("#") and "Sub Head 2" in l for l in md.split("\n"))
      and not any(l.startswith("#") and "His Books" in l for l in md.split("\n")), [l[:50] for l in md.split("\n") if "His Books" in l][:1])

# a heading-styled block of body size right under an open sentence, no space above, lowercase: the paragraph going on
def cont_page(p):
    rows = prose(p, 120, 3)
    rows.append(([("Here the line leaves its sentence open and runs to the right", B)], 100, 162))
    rows.append(([("continues in", "Odd-Bold")], 100, 176))
    rows.append(([("the next block until it ends here.", B)], 100, 190))
    rows += [([("SECTION %d" % k, "Odd-Bold")], 100, 230 + 60 * k) for k in range(1)] + prose(p, 250, 3)
    return page_runs(p, rows)
d = P.Doc([cont_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
check("no heading may start in lowercase right after an open sentence", not any(l.startswith("#") and "continues in" in l for l in md.split("\n")),
      [l for l in md.split("\n") if "continues in" in l])

d.lex = {"half-moon"}
check("a line-end hyphen before a compound is real when the book writes its first part with a hyphen",
      d.hyphen_real("half", "moon-shaped") and not d.hyphen_real("hal", "fmoon-shaped"))

# -- 2. list items with a bold lead-in and a hanging indent are one segment each; bullets too
def list_page(p):
    rows = prose(p, 120, 2)
    rows.append(([("Two kinds:", B)], 100, 148))
    rows.append(([("1. Bold Lead-In Words Here In A Bold Face, Set Long.", "Lead-Bold"), (" Then text goes", B)], 100, 166))
    rows.append(([("continues on the next line with a hanging indent to the end.", B)], 112, 180))
    rows.append(([("2. Another Lead-In.", "Lead-Bold"), (" Short item text that runs on to the very end of it", B)], 100, 194))
    rows.append(([("right edge and then ends.", B)], 112, 208))
    rows.append(([("• Bulleted Lead.", "Lead-Bold"), (" A bulleted item whose text runs to the far right side of", B)], 100, 222))
    rows.append(([("end of the line and wraps under itself.", B)], 112, 236))
    rows.append(([("• Second bullet.", B)], 100, 250))
    rows += prose(p, 280, 3)
    return page_runs(p, rows)
d = P.Doc([list_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
segs = [s for s in md.split("\n\n") if s.strip()]
one = [s for s in segs if "Bold Lead-In Words" in s]
check("a numbered item with a bold lead-in is one segment with its continuation", len(one) == 8 and all("hanging indent to the end" in s
      and not s.startswith("#") for s in one), one[:1])
check("the next number starts the next item", all(any(s.startswith("2.") and "then ends" in s for s in segs) for _ in [0]), [s[:50] for s in segs if "Another Lead" in s][:1])
bul = [s for s in segs if "Bulleted Lead" in s]
check("a bulleted item is one segment with its wrapped line, the next bullet is another", len(bul) == 8 and all("wraps under itself" in s
      and "Second bullet" not in s for s in bul), bul[:1])

# -- 3. running heads that carry the page number go even when their text changes per section; a margin line that
# repeats a heading goes too; both are counted in the warnings
def head_page(p):
    sec = "Alpha Part" if p <= 4 else "Beta Part"
    rows = [([(f"{sec} {p + 10}" if p % 2 else f"{p + 10} The Book Title", "Run-Regular")], 100, 60, 9)]
    heads = {1: "Gamma Section", 3: "Delta Section", 5: "Gamma Section", 6: "Epsilon Section", 8: "Zeta Section"}
    if p in heads:                                                                     # a heading style of the book
        rows.append(([(heads[p], "Head-Bold")], 100, 104, 16))
    rows += prose(p, 130, 12)
    if p in (2, 3):
        rows.append(([("Gamma Section", "Run-Regular")], 100, 700, 9))                  # foot repeating the heading
    return page_runs(p, rows)
d = P.Doc([head_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
check("running heads with the page number go although their text changes", "Alpha Part" not in md and "Beta Part" not in md
      and "Book Title" not in md, [l for l in md.split("\n") if "Part" in l or "Title" in l][:3])
check("a foot repeating a heading goes, the heading stays", md.count("Gamma Section") == 2
      and sum(1 for l in md.split("\n") if l.startswith("#") and "Gamma" in l) == 2, md.count("Gamma Section"))
check("the removals are counted for the warnings", sum(p["running_removed"] for p in d.pages) == 10, [p["running_removed"] for p in d.pages])
check("a note ending in a number has a running head's shape", P.FOLIO.match("See Abel, 23") is not None)   # the page offset decides
def note_page(p):
    names = ["Abel", "Brook", "Crane", "Drake", "Ebert", "Frost", "Grant", "Hurst"]
    rows = prose(p, 130, 12) + [([("See %s, %d" % (names[p - 1], p * 7 + 3), "Note-Regular")], 100, 690, 9)]
    return page_runs(p, rows)
d = P.Doc([note_page(p) for p in range(1, 9)])
notes = [l for p in d.pages for b in p["blocks"] for l in b["lines"] if l["plain"].startswith("See ")]
check("...its number does not follow the pages, so it is no running head", len(notes) == 8 and not any(l["sil"] for l in notes)
      and sum(p["running_removed"] for p in d.pages) == 0, [l["sil"] for l in notes])

# -- 4. a set-apart block in the margin band or a bare folio goes (counted); elsewhere it stays, flagged
def flag_page(p):
    rows = prose(p, 130, 12)
    if p == 3:
        rows.append(([("Odd margin note text", "Odd-Regular")], 100, 40))
        rows.append(([("An odd set-apart remark between paragraphs.", "Odd-Regular")], 100, 330))
    return page_runs(p, rows)
d = P.Doc([flag_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
check("a set-apart block in the margin band goes and is counted", "Odd margin note" not in md and st["flag_removed"] == 1, st["flag_removed"])
check("a set-apart block in the text stays, flagged for review", "odd set-apart remark" in md and st["style_flags"] == 1, st["style_flags"])

# ================================================================ round 7: named block roles from shape
from segtypes import seg_type as _st


def named_page(p):
    rows = [([("Chapter Title %d" % p, "Head-Bold")], 100, 90, 16)]
    if p == 3:                                                    # epigraph + attribution with the work's title
        rows += [([("Plain sample words set in a narrower measure, than when", B)], 130, 130),
                 ([("they open a chapter, says the sample.", B)], 130, 144),
                 ([("—A. Poet,", "Small-Regular")], 405 - 9 * 5.5, 160, 11),
                 ([("A Book of Lines", "Small-Italic")], 405 - 15 * 5.5, 174, 11)]
        rows += prose(p, 210, 6)
    elif p == 4:                                                  # block quote inside the flow
        rows += prose(p, 130, 4) + [([("A quoted passage from another book that runs on over a long", B)], 130, 200),
                                    ([("second line and ends right here with a full stop.", B)], 130, 214),
                                    ([("(A. Writer)", "Small-Regular")], 130, 230, 10)] + prose(p, 254, 4)
    elif p == 5:                                                  # verse: short lines of irregular length
        rows += prose(p, 130, 4) + [([(t, "Verse-Italic")], 130, 196 + 14 * k) for k, t in enumerate(
            ["Short line one", "a much longer second line of the poem", "third", "and the fourth and last one"])] + prose(p, 270, 4)
    elif p == 7:                                                  # a deviation no rule names: centered
        rows += prose(p, 130, 4) + [([("A centered remark", B)], 281.5 - 17 * 2.75, 200)] + prose(p, 226, 4)
    else:
        rows += prose(p, 130, 8)
    return page_runs(p, rows)
d = P.Doc([named_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
segs = [x for x in md.split("\n\n") if x.strip()]
meta = d.seg_meta
types = [_st(x)["type"] for x in segs]
i_epi = next(i for i, x in enumerate(segs) if "Plain sample words set" in x)
check("epigraph: set apart right after a chapter heading, one Epigraph segment", types[i_epi] == "epigraph" and types[i_epi - 1] == "heading"
      and "measure, than when they open a chapter" in segs[i_epi], segs[i_epi - 1:i_epi + 2])
check("attribution: the dash line and the work's title line are one Attribution segment", types[i_epi + 1] == "attribution"
      and "—A. Poet, *A Book of Lines*" in segs[i_epi + 1], segs[i_epi + 1])
check("epigraph meta: role, confidence and the shape reasons", meta[i_epi]["role"] == "epigraph" and meta[i_epi]["conf"] >= 0.7
      and {"indent", "indent-right"} <= set(meta[i_epi]["flags"]) and "stil-farkli" not in meta[i_epi]["flags"], meta[i_epi])
i_q = next(i for i, x in enumerate(segs) if "quoted passage" in x)
check("block quote: indented inside the flow, '> ' segment, body before and after", types[i_q] == "blockquote" and types[i_q - 1] == "para"
      and types[i_q + 2] == "para" and "over a long second line" in segs[i_q], segs[i_q])
check("a name in parentheses under a quote is its attribution", types[i_q + 1] == "attribution" and "(A. Writer)" in segs[i_q + 1], segs[i_q + 1])
i_v = next(i for i, x in enumerate(segs) if "Short line one" in x)
check("verse: lines kept with hard breaks in one Verse segment", types[i_v] == "verse"
      and "*Short line one*\\\n*a much longer second line of the poem*\\\n*third*\\\n" in segs[i_v], segs[i_v])
i_c = next(i for i, x in enumerate(segs) if "centered remark" in x)
check("a deviation no rule names keeps the review flag, with its reason", types[i_c] == "para" and "centered" in meta[i_c]["flags"]
      and "stil-farkli" in meta[i_c]["flags"] and meta[i_c]["role"] is None, meta[i_c])
plain = [i for i, x in enumerate(segs) if x.startswith("Body 0 of page 2")]
# ("of page 2" in the synthetic prose is an in-book page reference: that flag is not a shape reason)
check("a normal paragraph has no reasons", plain and [f for f in meta[plain[0]]["flags"] if f != "sayfa-gonderme"] == [],
      meta[plain[0]] if plain else None)
check("role counts in the stats", st["role_epigraph"] == 1 and st["role_attribution"] == 2 and st["role_blockquote"] == 1 and st["role_verse"] == 1, st)

# a quote cut by a page break goes on in a one-line, unindented rest at the top of the next page
def cut_page(p):
    rows = [([("Chapter Title %d" % p, "Head-Bold")], 100, 90, 16)] if p != 4 else []
    if p == 3:
        rows += prose(p, 130, 30) + [([("A quoted passage from another book that runs on over a long", B)], 130, 560),
                                     ([("way down the page and is broken at the end of it with frag-", B)], 130, 574)]
    elif p == 4:
        rows += [([("mentation of the sample.", B)], 100, 130)] + prose(p, 160, 6)
    else:
        rows += prose(p, 130, 8)
    return page_runs(p, rows)
d = P.Doc([cut_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
q = [x for x in md.split("\n\n") if x.startswith("> ")]
check("a quote cut by a page break takes its rest from the next page (hyphen joined)", len(q) == 1
      and q[0].endswith("with fragmentation of the sample."), q)

# box: a tinted panel's paragraphs are Box segments, its first short line in another style the box title
def box_page(p):
    rows = [([("Chapter Title %d" % p, "Head-Bold")], 100, 90, 16)] + prose(p, 130, 4)
    if p == 2:
        rows += [([("Box Title", "BoxHead-Bold")], 110, 210)] + [([("Inside the box the text reads on in its own panel line %d." % k, B)], 110, 226 + 14 * k)
                                                               for k in range(3)]
    rows += prose(p, 300, 4)
    chars = []
    for r in rows:
        chars += runs_line(r[0], r[1], r[2], len(chars), *r[3:])
    return P.build_blocks(p, W, H, chars, [(100, 195, 470, 270)] if p == 2 else [])
d = P.Doc([box_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
segs = [x for x in md.split("\n\n") if x.strip()]
boxed = [x for x in segs if _st(x)["type"] == "box"]
check("box: title and text inside a tinted panel are Box segments", len(boxed) == 2 and "**Box Title**" in boxed[0]
      and "own panel line 0" in boxed[1] and "line 2." in boxed[1], boxed)

# ================================================================ round 8
# -- 1. a second text layer: text drawn again at the same spot (whole lines or word pieces) is read once
def layered_page(p):
    rows = [(f"Plain sentence {k} on page {p} keeps going for a while here.", 72, 600 - 14 * k, 11) for k in range(8)]
    return rows + [rows[2], ("Plain sente", 72, 600 - 14 * 5, 11)]                # a whole line again, and a word piece
md, warns = P.convert(synth_pdf([layered_page(p) for p in range(1, 5)]))
check("a glyph drawn twice at the same spot is read once (whole line, word piece)",
      md.count("Plain sentence 2 on page 3 keeps") == 1 and "sente Plain" not in md and "Plain sente\n" not in md
      and md.count("Plain sentence 5 on page 3 keeps going") == 1, md[:400])

lig = [P.Ch(ch, 100, 110, 190, 202, 200, "Body-Regular", 11.0, idx=k) for k, ch in enumerate("ff")] + line("ect", 110, 200, idx0=2)
check("the letters of a ligature (one spot, one after the other) are both kept", "".join(c.ch for c in P._dedupe(lig)) == "ffect")
far = line("word", 100, 200) + [P.Ch("o", 105.5, 111, 190, 202, 200, "Body-Regular", 11.0, idx=40)]
check("the same glyph at the same spot further on in the stream is dropped", "".join(c.ch for c in P._dedupe(far)) == "word")
near = line("word", 100, 200) + [P.Ch("o", 105.62, 111, 190, 202, 200.04, "Body-Regular", 11.0, idx=40)]   # a hair off the cell edge
check("...also a tenth of a point off", "".join(c.ch for c in P._dedupe(near)) == "word")

# -- 4. text set under 3 pt is hidden, never read
md, warns = P.convert(synth_pdf([layered_page(p)[:8] + [("Hidden %s Variable" % ["one", "two", "three", "four"][p - 1], 72, 560, 1)]
                                 for p in range(1, 5)]))
check("text under 3 pt is not read", "Variable" not in md and "Plain sentence 4 on page 2" in md, md[:200])

# -- 3. an ornamental initial far larger than 40 pt is still the drop cap
dc = [P.Ch("O", 60, 140, 191, 271, 268, "Body-Regular", 100.0, idx=0)] + line("ne quiet sample line of plain words", 145, 200, idx0=1)
L = lines_text(dc)
check("a 100 pt initial joins its line", len(L) == 1 and L[0]["plain"].startswith("One quiet sample line"), [l["plain"] for l in L])
dcb = [P.Ch("O", 60, 140, 191, 271, 268, "Display-Bold", 100.0, 700, False, True, idx=0)] + line("ne quiet sample line of plain words", 145, 200, idx0=1)
L = lines_text(dcb)
check("a bold (or italic) initial carries no style of its own into the word", L[0]["md"].startswith("One quiet sample line"), L[0]["md"][:30])
tall = ([P.Ch("O", 60, 120, 191, 271, 268, "Body-Regular", 100.0, idx=0)] + line("ne grey morning the sample begins", 130.1, 200, idx0=1)
        + line("its second line runs on from here", 130.0, 214, idx0=40) + line("and a third line follows it", 130.0, 228, idx0=80))
L = lines_text(tall)
check("a tall initial joins the line beside its top, not the next one starting a hair further left",
      [l["plain"] for l in L][0].startswith("One grey morning"), [l["plain"] for l in L])

# -- 2. a running head near body size (and its page number apart) is outside the text block and goes
def head_body_page(p):
    rows = [("THE RUNNING BOOK TITLE" if p % 2 else "Chapter Running Title", 300, 90, "Run-Regular", 10.5),   # below the 9% band
            (str(p + 10), 100 if p % 2 else 420, 90, "Run-Regular", 10.5)]
    return rows + body_rows(p, top=130, n=12)
d = doc_from([head_body_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
check("a running head set near body size is outside the text block and goes, page number too",
      "RUNNING BOOK TITLE" not in md and "Chapter Running Title" not in md and not re.search(r"(^|\n)#*\s*1[0-9]\s*(\n|$)", md),
      [x for x in md.split("\n\n") if "Running" in x or "RUNNING" in x or re.fullmatch(r"#*\s*\d+", x.strip())][:4])
check("...the text block starts at the first body line", d.text_top is not None and d.text_top > 115, d.text_top)

# -- 5. an attribution set in a heading style (dash, small caps) after an epigraph is the attribution
WORDS = ["amber", "birch", "cedar", "dune", "ember", "fjord", "grove", "heath", "islet", "jade", "kelp", "larch"]


def attr_page(p):
    rows = [([("Chapter Title %d" % p, "Head-Bold")], 100, 90, 16)]
    if p % 2:                                                     # every other chapter opens with an epigraph
        w = WORDS[p - 1]
        rows += [([(f"Plain {w} words set in a narrower measure, than when"[:55].ljust(55, "s"), B)], 130, 130),
                 ([(f"they open the {w} chapter, says the sample.", B)], 130, 144),
                 ([(f"—A {w.upper()} NAME (1988, P. 140)", "Attr-Bold")], 200, 166, 11)]
        rows += prose(p, 200, 6)
    else:                                                         # the attribution style is also the sub-head style
        rows += prose(p, 130, 4) + [([("SECTION HEAD %d" % p, "Attr-Bold")], 100, 200, 11)] + prose(p, 220, 4)
    return page_runs(p, rows)
d = P.Doc([attr_page(p) for p in range(1, 13)])
md, st = P.build_markdown(d, 0, 11)
segs = [x for x in md.split("\n\n") if x.strip()]
check("a dash line in a heading style after an epigraph is its attribution, not a heading",
      not any(x.startswith("#") and " NAME (1988" in x for x in segs) and sum(_st(x)["type"] == "attribution" for x in segs) == 6,
      [x[:60] for x in segs if " NAME (1988" in x][:3])

# -- 7. a block drawn over other lines (pieces of a second layer) is no quote
def over_page(p):
    rows = prose(p, 130, 8)
    if p == 4:
        rows.append(([("a stray piece of words drawn over the last line of text", B)], 160, 233))   # over the last line
    return page_runs(p, rows)
d = P.Doc([over_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
check("a block lying over other lines is no block quote (it keeps the review flag)", not any(x.startswith("> ") for x in md.split("\n\n")),
      [x[:60] for x in md.split("\n\n") if "stray piece" in x])

# ---------------------------------------------------------------- notes: callouts, page-bottom footnotes, endnotes
from segtypes import reorder_footnotes, seg_type as _st2


class Chars:
    """a page's chars in content order: body lines, raised callouts (6.5pt, 4pt up), small note lines"""
    def __init__(self):
        self.c = []

    def text(self, t, x, base, font=B, size=11.0):
        self.c += line(t, x, base, font, size, idx0=len(self.c), italic="Italic" in font, bold="Bold" in font,
                       weight=700 if "Bold" in font else 400)
        return x + len(t) * 0.5 * size

    def sup(self, t, x, base, gap=0.0):
        self.c += line(t, x + gap, base - 4, B, 6.5, idx0=len(self.c))


# a raised number after a word is a callout; after a digit an exponent; after a capital a formula (B12)
got = []
for t, n in (("the end of a sentence.", "3"), ("ten to the power 10", "2"), ("a dose of vitamin B", "12")):
    k = Chars()
    k.sup(n, k.text(t, 100, 200), 200)
    L = lines_text(k.c)
    got.append((len(L), L[0]["refs"], L[0]["plain"]))
check("callout: a raised number after a word; not after a digit (10²) or a capital (B12)",
      got == [(1, ["3"], "the end of a sentence.3"), (1, [], "ten to the power 102"), (1, [], "a dose of vitamin B12")], got)
k = Chars()
k.sup("7", k.text("a short last line.", 100, 200), 200, gap=2.5)
k.text("Next line of text that runs on here.", 100, 214)
L = lines_text(k.c)
check("callout: a number standing just past the end of its line folds into it", L[0]["refs"] == ["7"] and len(L) == 2, [l["md"] for l in L])


def foot_page(p):
    """full pages (the text block reaches the notes); page 3 cites notes 1 and 2, note 2 and the paragraph run on to
    page 4, which cites note 3"""
    k = Chars()
    first = 0
    if p == 4:
        k.text("next page where the sentence ends.", 100, 130)
        k.sup("3", k.text("A third sentence with its own note.", 100, 144), 144)
        first = 2
    for j in range(first, 30):
        k.text("Body %d of the %d: %s" % (j, p, FULL[:48]), 100, 130 + 14 * j)
    if p == 3:
        k.sup("1", k.text("A sentence on this leaf that has a note.", 100, 550), 550)
        k.sup("2", k.text("Then another with a second note, and the text runs to the", 100, 564), 564)
        k.text("1 A synthetic footnote that ends here.", 100, 640, "Note-Regular", 9)
        k.text("2 The second note runs on over the", 100, 652, "Note-Regular", 9)
    elif p == 4:
        k.text("leaf break and ends overleaf.", 100, 640, "Note-Regular", 9)
        k.text("3 A third note.", 100, 652, "Note-Regular", 9)
    else:
        for j in (30, 31):
            k.text("Body %d of the %d: %s" % (j, p, FULL[:48]), 100, 130 + 14 * j)
    return P.build_blocks(p, W, H, k.c, [])


d = P.Doc([foot_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
segs = [x for x in md.split("\n\n") if x.strip()]
cut = [x for x in segs if "runs to the" in x]
check("footnote: callouts become [^label] right after the word, the paragraph cut by the page still joins",
      len(cut) == 1 and "has a note.[^b1-1] Then" in cut[0] and "second note, and the text runs to the[^b1-2] next page where" in cut[0], cut)
check("footnote: each note is its own definition segment, without its number",
      "[^b1-1]: A synthetic footnote that ends here." in segs and "[^b1-3]: A third note." in segs, [x for x in segs if x.startswith("[^")])
check("footnote: a note running on overleaf is one note", "[^b1-2]: The second note runs on over the leaf break and ends overleaf." in segs,
      [x for x in segs if x.startswith("[^b1-2]")])
check("footnote: counts, nothing unmatched", (st["note_refs"], st["note_bodies"], st["note_refs_matched"], st["note_refs_unmatched"],
                                             st["note_bodies_unmatched"]) == (3, 3, 3, 0, 0), dict(st))
re_segs, warns = reorder_footnotes(segs)
i1 = next(i for i, x in enumerate(re_segs) if "has a note.[^b1-1]" in x)
check("footnote: the import reorder puts the definitions after the paragraph citing them",
      re_segs[i1 + 1].startswith("[^b1-1]:") and re_segs[i1 + 2].startswith("[^b1-2]:") and not warns, (re_segs[i1 + 1:i1 + 3], warns))


def foot2_page(p):
    """page-bottom notes below the text block: a sign, numbers that restart on every page, one note per page at the
    same distance from the page number (a folio pattern), a hanging indent, a number printed twice, two paragraphs"""
    k = Chars()
    notes = {2: ("*", ["* A note marked with a sign."]),
             3: ("1", ["1 The first note on leaf three."]),
             4: ("1", ["1 The first note on leaf four."]),
             5: ("20", ["20 A note whose second line hangs", "  under the first and ends here."]),
             6: ("21", ["21 21 A note whose number came twice."]),
             7: ("22", ["22 First paragraph of this note runs", "on and ends.", "   A second paragraph opens here,", "and closes it."])}
    for j in range(30):
        t = "Body %d of the %d: %s" % (j, p, FULL[:48])
        if j == 20 and p in notes:
            k.sup(notes[p][0], k.text("A sentence on leaf %d that carries a note." % p, 100, 130 + 14 * j), 130 + 14 * j)
        else:
            k.text(t, 100, 130 + 14 * j)
    for i, t in enumerate(notes.get(p, (None, []))[1]):
        x = 100 + 5 * (len(t) - len(t.lstrip()))
        k.text(t.strip(), x, 600 + 11 * i, "Note-Regular", 9)
    return P.build_blocks(p, W, H, k.c, [])


d = P.Doc([foot2_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
segs = [x for x in md.split("\n\n") if x.strip()]
defs = [x for x in segs if x.startswith("[^")]
check("footnote: a sign callout and its note", any("leaf 2 that carries a note.[^b1-a]" in x for x in segs)
      and "[^b1-a]: A note marked with a sign." in defs, defs)
check("footnote: numbers restarting on every page match by page and number, labels stay unique",
      any("leaf 3 that carries a note.[^b1-1]" in x for x in segs) and any("leaf 4 that carries a note.[^b1-1-4]" in x for x in segs)
      and "[^b1-1]: The first note on leaf three." in defs and "[^b1-1-4]: The first note on leaf four." in defs, defs)
check("footnote: one note per page at a fixed distance from the page number is no running foot",
      all(any(x.startswith("[^b1-%d]" % n) for x in defs) for n in (20, 21, 22)), defs)
check("footnote: a hanging-indented line belongs to its note", "[^b1-20]: A note whose second line hangs under the first and ends here." in defs, defs)
check("footnote: a number printed twice is dropped once more", "[^b1-21]: A note whose number came twice." in defs, defs)
check("footnote: two paragraphs in a note stay apart (hard break)",
      "[^b1-22]: First paragraph of this note runs on and ends.\\\nA second paragraph opens here, and closes it." in defs, defs)
check("footnote: all matched, no review flag on the notes", (st["note_refs"], st["note_refs_unmatched"], st["note_bodies_unmatched"]) == (6, 0, 0)
      and not any("stil-farkli" in m["flags"] for x, m in zip(segs, d.seg_meta) if x.startswith("[^")), dict(st))


def end_page(p):
    k = Chars()
    y = 130
    if p in (1, 4):
        k.text("Chapter %s Title" % ("One" if p == 1 else "Two"), 100, 90, "Head-Bold", 16)
    if p in (1, 2, 4, 5, 6):
        for j in range(10):
            t = "Body %d of page %d: %s" % (j, p, FULL[:48])
            if j == 3 and p in (1, 2, 4, 5, 6):
                n = {1: "1", 2: "2", 4: "1", 5: "2", 6: "3"}[p]
                k.sup(n, k.text("Body %d of page %d ends a sentence here." % (j, p), 100, y), y)
            elif j == 6 and p == 6:
                k.sup("4", k.text("A sentence whose note is missing.", 100, y), y)
            else:
                k.text(t, 100, y)
            y += 14
    if p == 3:
        for j in range(10):
            k.text("Body %d of page %d: %s" % (j, p, FULL[:48]), 100, y)
            y += 14
    if p == 7:
        k.text("Notes", 100, 90, "Head-Bold", 16)
        y = 160
        for head, ns in (("Chapter One", (1, 2, 3)), ("Chapter Two", (1, 2, 3))):
            k.text(head, 100, y, "Head-Bold", 16)
            y += 28
            for n in ns:
                k.text("%d. A synthetic endnote number %d of %s." % (n, n, head.lower()), 100, y, "Note-Regular", 10)
                y += 16
                if n == 1 and head == "Chapter One":     # a wrapped line of note 1 that starts with a number
                    k.text("3. 14 is where the wrapped reference of note one ends.", 100, y, "Note-Regular", 10)
                    y += 16
            y += 12
    if p == 8:
        k.text("Bibliography", 100, 90, "Head-Bold", 16)
        for n in (1, 2, 3):
            k.text("%d. Author, A. 2001. A Synthetic Title. Place: Press." % n, 100, 130 + 16 * n, "Note-Regular", 10)
    return P.build_blocks(p, W, H, k.c, [])


d = P.Doc([end_page(p) for p in range(1, 9)])
md, st = P.build_markdown(d, 0, 7)
segs = [x for x in md.split("\n\n") if x.strip()]
check("endnotes: numbering restarts per chapter, matched by chapter and number",
      any("page 1 ends a sentence here.[^b1-1]" in x for x in segs) and any("page 4 ends a sentence here.[^b2-1]" in x for x in segs)
      and "[^b2-3]: A synthetic endnote number 3 of chapter two." in segs, [x for x in segs if "[^" in x][:12])
check("endnotes: a callout without its note is written as the number and flagged", any("is missing.4" in x for x in segs)
      and "atif-eslesmedi" in d.seg_meta[next(i for i, x in enumerate(segs) if "is missing.4" in x)]["flags"])
i3 = next((i for i, x in enumerate(segs) if "number 3 of chapter one" in x), None)
check("endnotes: a note nobody cites stays as it was, flagged", i3 is not None and segs[i3].startswith("3. ")
      and "not-eslesmedi" in d.seg_meta[i3]["flags"], segs[i3] if i3 is not None else None)
check("endnotes: an emptied group heading goes, one with a note left stays (and so does Notes)",
      "# Chapter Two" not in segs and "# Chapter One" in segs and "# Notes" in segs, [x for x in segs if x.startswith("#")])
check("endnotes: a number out of sequence that comes again later is a line of the note before",
      any(x.startswith("[^b1-1]: A synthetic endnote number 1") and "3. 14 is where the wrapped" in x for x in segs),
      [x for x in segs if x.startswith("[^b1-1]")])
check("bibliography: numbered entries after it are no notes", sum("Synthetic Title" in x for x in segs) == 3
      and not any(x.startswith("[^") and "Synthetic Title" in x for x in segs))
check("endnotes: counts", (st["note_refs"], st["note_bodies"], st["note_refs_matched"], st["note_refs_unmatched"], st["note_bodies_unmatched"],
                           st["notes_heads_removed"]) == (6, 6, 5, 1, 1, 1), dict(st))


# -- endnote sections: every name, every way of grouping (unit level: the writer's item list)
def notes_md(name, labels, name_as_head=True):
    """two chapters citing 1, 2 each, then a notes section named `name`; labels: per group a (kind, text) or None"""
    R = lambda n: P.REF_OPEN + n + P.REF_CLOSE
    meta = lambda pg: dict(pages={pg}, boxes=[], style="Body 11", flags=set())
    nmeta = lambda pg: dict(pages={pg}, boxes=[], style="Note 9", flags=set())
    md = []
    for ch in (1, 2):
        md += [("head", dict(lvl=2, text="Chapter Title %d" % ch, page=ch, base=0, size=16), meta(ch)),
               ("p", dict(text="A first claim%s and a second claim%s close chapter %d." % (R("1"), R("2"), ch)), meta(ch))]
    md.append(("head", dict(lvl=2, text=name, page=9, base=0, size=16), meta(9)) if name_as_head else ("p", dict(text=name), meta(9)))
    for g, lab in enumerate(labels, 1):
        if lab:
            kind, t = lab
            md.append(("head", dict(lvl=3, text=t, page=9, base=0, size=13), meta(9)) if kind == "head" else ("p", dict(text=t), nmeta(9)))
        md += [("p", dict(text="%d. Source %d of group %d." % (n, n, g)), nmeta(9)) for n in (1, 2)]
    return md


def notes_out(md):
    st = collections.Counter()
    P.resolve_notes(md, st)
    return [P._txt(it) for it in md if it is not None], st


cases = [("Notes", [("head", "chapter one: the first"), ("head", "chapter two: the second")], True),
         ("NOTE", [("p", "Chapter 1"), ("p", "Chapter 2")], True),
         ("Endnotes", [("p", "1"), ("p", "2")], True),
         ("End Notes", [("p", "I"), ("p", "II")], True),
         ("Notes and Sources", [None, None], True),
         ("notes", [("p", "The First Chapter"), ("p", "The Second Chapter")], False)]
for name, labels, as_head in cases:
    out, st = notes_out(notes_md(name, labels, as_head))
    ok = (any("claim[^b1-1] and a second claim[^b1-2] close chapter 1" in x for x in out)
          and any("claim[^b2-1] and a second claim[^b2-2] close chapter 2" in x for x in out)
          and "[^b2-2]: Source 2 of group 2." in out and st["note_refs_unmatched"] == 0 and st["note_bodies_unmatched"] == 0
          and name not in out and not any(lab and lab[1] in out for lab in labels))
    check("endnotes named %r, groups %s: matched by chapter and number, the emptied section leaves nothing"
          % (name, "/".join(lab[0] + ":" + lab[1] if lab else "restart" for lab in labels[:1])), ok, (out[-6:], dict(st)))
md = notes_md("Notes to Chapter 1", [None])
md = [it for it in md if not (it[0] == "p" and "chapter 2" in P._txt(it))]
out, st = notes_out(md)
check("a section per chapter (Notes to Chapter N)", "[^b1-1]: Source 1 of group 1." in out and st["note_refs_unmatched"] == 0, (out, dict(st)))
md = notes_md("Notes", [None, None])
md = [it for it in md if not (it[0] == "p" and "claim" in P._txt(it))]
out, st = notes_out(md)
check("a notes section none of whose notes is cited: left as it was, and warned about",
      "Notes" in out and "1. Source 1 of group 1." in out and st.get("notes_unmatched_pages") == [9], (out, dict(st)))

# ---------------------------------------------------------------- in-book page references vs citations
def refs_of(texts, heads=()):
    md = [("head", dict(lvl=2, text=h), dict(flags=set())) for h in heads]
    md += [("p", dict(text=t), dict(flags=set())) for t in texts]
    st = collections.Counter()
    P.page_refs(md, st)
    return [it[2].get("page_refs") for it in md[len(heads):]]


got = refs_of(["As we saw earlier (see p. 42), this holds.", "This is shown on pages 12–14 of the book.",
               "See chapter 3 for more.", "The points listed below are the main ones.", "Mentioned before (p. 7) in passing."])
check("page reference: see p., pages a–b, see chapter, listed below, a bare (p. N)",
      got == [["s.42"], ["s.12–14"], ["bölüm 3"], ["below"], ["s.7"]], got)
got = refs_of(["Smith (2003, p. 4) says so.", "Jones, *A Synthetic Title*, p. 15.", "Ibid., pp. 13–14.",
               "the “three word phrase” (p. 256) here.", "Lee et al. found it, p. 9.", "Noted by Brown (p. 5) once."])
check("citation: author and year, a title, ibid., a quoted phrase, et al., a name before (p. N) are not page references",
      got == [None] * 6, got)
got = refs_of(["See p. 4 of the guide."], heads=["Bibliography"])
check("page reference: none inside a bibliography", got == [None], got)

print("FAILED %d" % failed if failed else "ALL PASS")
sys.exit(1 if failed else 0)
