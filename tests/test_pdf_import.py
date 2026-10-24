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
check("the counter never changes the decision", not d.hyphen_real("stress", "free") and d.hyphen_ambiguous("stress", "free"))


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
check("a normal paragraph has no reasons", plain and meta[plain[0]]["flags"] == [], meta[plain[0]] if plain else None)
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

print("FAILED %d" % failed if failed else "ALL PASS")
sys.exit(1 if failed else 0)
