"""
PDF -> markdown for the new-project screen. pypdfium2 only (BSD/Apache; no model, no OCR).

    convert(pdf_bytes, first_page=None, last_page=None) -> (markdown, warnings)

The page range is 1-based, as printed by a PDF viewer. The whole document is always analysed (style
statistics, repeated-text detection and the hyphen lexicon need many pages); only the requested pages
are written out. Text layer required: scanned PDFs raise PdfImportError.

Pipeline: characters (font, size, weight, italic, advance box) -> rows by baseline, content-stream
order -> blocks (style change, indent, vertical gap, numbered item, tinted box) -> styles -> roles by
rules -> markdown in the conventions the app understands today: '#' headings (part/chapter '#',
sub-heading 1 '##', sub-heading 2 '###'), box title as bold paragraph, poem lines kept in one
segment with trailing double-space breaks, attribution as its own paragraph, one segment per list item.

Everything here is derived from the document itself; no per-book constants.
"""
import re
import math
import ctypes
import statistics
import collections

import pypdfium2 as pdfium
import pypdfium2.raw as raw


class PdfImportError(Exception):
    """Message is shown to the user as is (Turkish)."""


ROLES = ["kısım", "bölüm", "alt başlık 1", "alt başlık 2", "gövde", "kutu", "kutu-başlık",
         "şiir", "epigraf", "alıntı", "atıf", "liste", "ön sayfa", "sil"]
HEAD = {"kısım": 1, "bölüm": 2, "alt başlık 1": 3, "alt başlık 2": 4}   # internal depth
MD_LEVEL = {1: 1, 2: 1, 3: 2, 4: 3}                                    # '#' count written to the markdown
NUM = re.compile(r"^\s*\d+\.\s")
ITEM = re.compile(r"^\s*(\d+\.|[•◦▪●■])\s")      # a list item starts: numbered or bulleted
DASH = re.compile(r"^\s*[—–―]")
ORNAMENT_FONT = re.compile(r"dingbat|symbol|ornament|wingding|fleuron|picto", re.I)
FOLIO = re.compile(r"^\s*(\d{1,4}|[ivxlcdm]{1,7})\s+\S.*|.*\S\s+(\d{1,4}|[ivxlcdm]{1,7})\s*$")   # number + short text
PAGE_NO = re.compile(r"^[\s—–\-·•|]*(\d{1,4}|[ivxlcdm]{1,7})[\s—–\-·•|]*$", re.I)
OCR_PRODUCER = re.compile(r"abbyy|finereader|tesseract|omnipage|readiris|paper capture|clearscan|\bocr\b", re.I)
SCANNED_WARNING = "Bu PDF taranmış görünüyor, metin güvenilir olmayabilir."
CAPTION = re.compile(r"^\s*(figure|fig\.|şekil|resim|görsel|plate|illustration|photo)\s*[\dIVX]", re.I)

# ---------------------------------------------------------------------------- characters
class Ch:
    __slots__ = ("idx", "ch", "ox", "lr", "x0", "x1", "top", "bot", "base", "font", "size", "weight",
                 "italic", "bold", "dropcap", "rot", "gsp")

    def __init__(self, ch, x0, x1, top, bot, base, font, size, weight=400, italic=False, bold=False,
                 idx=0, ox=None, lr=None, rot=False):
        self.idx, self.ch, self.x0, self.x1, self.top, self.bot, self.base = idx, ch, x0, x1, top, bot, base
        self.font, self.size, self.weight, self.italic, self.bold = font, size, weight, italic, bold
        self.ox = x0 if ox is None else ox          # glyph origin
        self.lr = x1 if lr is None else lr          # end of the advance box (loose box)
        self.dropcap = False
        self.rot = rot                              # set at an angle (diagonal watermark, vertical edge tab)
        self.gsp = False                            # pdfium generated a space right before this char


def size_key(font, size):
    return f"{font}|{round(size * 2) / 2:g}"


def fam_of(style):
    f, s = style.split("|")
    return re.sub(r"-(BoldItalic|LightItalic|RegularItalic|Italic|Bold|Regular|Light|Medium|Semibold|ExtraBold|Roman|SmallCaps)$", "", f), float(s)


def _page_chars(page):
    """per-character records, top-left origin; effective size = FontSize * the matrix's height across the baseline
    (a slanted faux italic is no taller than its upright text)"""
    H = page.get_height()
    tpg = page.get_textpage()
    tp = tpg.raw
    n = tpg.count_chars()
    l, b, r, t = (ctypes.c_double() for _ in range(4))
    ox, oy = ctypes.c_double(), ctypes.c_double()
    m, loose = raw.FS_MATRIX(), raw.FS_RECTF()
    buf, fl = ctypes.create_string_buffer(96), ctypes.c_int()
    out, gsp = [], False
    for i in range(n):
        if raw.FPDFText_IsGenerated(tp, i):
            gsp = gsp or raw.FPDFText_GetUnicode(tp, i) == 32
            continue
        u = raw.FPDFText_GetUnicode(tp, i)
        if u in (13, 10):
            continue
        raw.FPDFText_GetCharBox(tp, i, l, r, b, t)
        raw.FPDFText_GetCharOrigin(tp, i, ox, oy)
        raw.FPDFText_GetMatrix(tp, i, m)
        raw.FPDFText_GetLooseCharBox(tp, i, loose)
        raw.FPDFText_GetFontInfo(tp, i, buf, 96, ctypes.byref(fl))
        fn = buf.value.decode("latin-1")
        w = raw.FPDFText_GetFontWeight(tp, i)
        # turned text has b != 0; c alone is a shear (faux italic), which stays in the flow
        rot = abs(m.b) > 0.1 * (abs(m.a) + abs(m.d))
        out.append(Ch(chr(u), l.value, r.value, H - t.value, H - b.value, H - oy.value, fn,
                      raw.FPDFText_GetFontSize(tp, i) * (abs(m.a * m.d - m.b * m.c) / math.hypot(m.a, m.b) if m.a or m.b else 1.0), w,
                      bool(fl.value & 0x40) or bool(re.search(r"italic|oblique", fn, re.I)),
                      w >= 600 or bool(re.search(r"bold|black|heavy", fn, re.I)),
                      idx=i, ox=ox.value, lr=loose.right, rot=rot))
        out[-1].gsp, gsp = gsp, False
    return _dedupe(out)


def _dedupe(chars):
    """Some producers draw part of the text a second time on top of a watermark (word pieces at exactly the same spot,
    later in the stream); a glyph repeated at the same place in the same font and size is one glyph. The letters of a
    ligature ("ff") also share one spot, but they come one right after the other: only a repeat further on counts.
    Text set under 3 pt is hidden (running-head variables on chapter openers), never read."""
    seen, keep = collections.defaultdict(list), []           # (char, font, size, x cell, y cell) -> [(x0, base, idx)]
    for c in chars:
        if not c.ch.strip():
            keep.append(c)
            continue
        if c.size < 3:
            continue
        gx, gy = round(c.x0), round(c.base)
        if any(abs(x - c.x0) < 0.3 and abs(y - c.base) < 0.3 and c.idx - i > 3
               for dx in (-1, 0, 1) for dy in (-1, 0, 1)
               for x, y, i in seen[(c.ch, c.font, round(c.size), gx + dx, gy + dy)]):
            continue
        seen[(c.ch, c.font, round(c.size), gx, gy)].append((c.x0, c.base, c.idx))
        keep.append(c)
    return keep


def _tint_boxes(page):
    """filled grey-ish path objects (sidebar boxes): [(x0, top, x1, bottom)]"""
    H, W = page.get_height(), page.get_width()
    res = []
    R, G, B, A = (ctypes.c_uint() for _ in range(4))
    mode, st = ctypes.c_int(), ctypes.c_int()
    for o in page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH]):
        raw.FPDFPath_GetDrawMode(o.raw, mode, st)
        if mode.value == 0:
            continue
        raw.FPDFPageObj_GetFillColor(o.raw, R, G, B, A)
        l, b, r, t = o.get_bounds()
        w, h = r - l, t - b
        lum = (R.value + G.value + B.value) / 3
        if (A.value == 255 and 150 <= lum <= 248 and w * h >= 1500 and w < W * 0.9 and h < H * 0.9
                and max(R.value, G.value, B.value) - min(R.value, G.value, B.value) < 30):
            res.append((l, H - t, r, H - b))
    return res


def _page_graphics(page):
    """images (top-left boxes), horizontal and vertical rule segments, and the scanned-page signals, in one pass.
    Rules are the straight pieces of path objects (cell borders, table rules): (y, x0, x1) and (x, y0, y1)."""
    W, H = page.get_width(), page.get_height()
    images, hl, vl = [], [], []
    big, inv, vis = False, 0, 0
    m, x, y = raw.FS_MATRIX(), ctypes.c_float(), ctypes.c_float()
    for o in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE, raw.FPDF_PAGEOBJ_TEXT, raw.FPDF_PAGEOBJ_PATH]):
        if o.type == raw.FPDF_PAGEOBJ_TEXT:
            if raw.FPDFTextObj_GetTextRenderMode(o.raw) == raw.FPDF_TEXTRENDERMODE_INVISIBLE:
                inv += 1
            else:
                vis += 1
            continue
        if o.type == raw.FPDF_PAGEOBJ_IMAGE:
            l, b, r, t = o.get_bounds()
            if (r - l) * (t - b) >= 0.8 * W * H:
                big = True                  # a scanned page or a full-page background: not a figure
            elif r - l >= 25 and t - b >= 25:
                images.append((l, H - t, r, H - b))
            continue
        raw.FPDFPageObj_GetMatrix(o.raw, m)
        prev = None
        for i in range(raw.FPDFPath_CountSegments(o.raw)):
            s = raw.FPDFPath_GetPathSegment(o.raw, i)
            raw.FPDFPathSegment_GetPoint(s, x, y)
            pt = (m.a * x.value + m.c * y.value + m.e, H - (m.b * x.value + m.d * y.value + m.f))
            kind = raw.FPDFPathSegment_GetType(s)
            if kind == raw.FPDF_SEGMENT_LINETO and prev:
                (x0, y0), (x1, y1) = prev, pt
                if abs(y1 - y0) < 0.6 and abs(x1 - x0) >= 10:
                    hl.append((y0, min(x0, x1), max(x0, x1)))
                elif abs(x1 - x0) < 0.6 and abs(y1 - y0) >= 6:
                    vl.append((x0, min(y0, y1), max(y0, y1)))
            prev = pt if kind != raw.FPDF_SEGMENT_BEZIERTO else None
    return dict(images=images, hl=hl, vl=vl, signals=(big, inv, vis))


def _merge_rules(segs, tol=1.2, gap=2.5):
    """collinear rule pieces -> {position: [(a, b), ...]} with touching pieces joined"""
    out = {}
    for pos, a, b in sorted(segs):
        key = next((k for k in out if abs(k - pos) <= tol), None)
        if key is None:
            out[pos] = [(a, b)]
            continue
        iv = out[key]
        if a <= iv[-1][1] + gap:
            iv[-1] = (iv[-1][0], max(iv[-1][1], b))
        else:
            iv.append((a, b))
        iv.sort()
    return out


def find_grids(hl, vl):
    """Ruled tables: horizontal and vertical rules that cross each other. A grid needs at least 3 horizontal and 3
    vertical rule positions (2 x 2 cells; a framed figure is 1 x 1). A cell border that is missing inside the grid means
    merged cells: the grid is marked complex."""
    H_ = _merge_rules(hl)
    V_ = _merge_rules(vl)
    covers = lambda ivs, a, b: any(s - 2 <= a and b <= e + 2 for s, e in ivs)
    hs = [(y, s, e) for y, ivs in H_.items() for s, e in ivs]
    vs = [(x, s, e) for x, ivs in V_.items() for s, e in ivs]
    parent = list(range(len(hs) + len(vs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, (y, x0, x1) in enumerate(hs):
        for j, (x, y0, y1) in enumerate(vs):
            if x0 - 2 <= x <= x1 + 2 and y0 - 2 <= y <= y1 + 2:
                parent[find(i)] = find(len(hs) + j)
    groups = collections.defaultdict(lambda: ([], []))
    for i in range(len(hs)):
        groups[find(i)][0].append(hs[i])
    for j in range(len(vs)):
        groups[find(len(hs) + j)][1].append(vs[j])
    grids = []
    for gh, gv in groups.values():
        ys = sorted({round(y, 1) for y, _, _ in gh})
        xs = sorted({round(x, 1) for x, _, _ in gv})
        ys = [y for k, y in enumerate(ys) if k == 0 or y - ys[k - 1] > 3]
        xs = [x for k, x in enumerate(xs) if k == 0 or x - xs[k - 1] > 3]
        if len(ys) < 3 or len(xs) < 3:
            continue
        # a table has a full outer frame; a decorative frame with a tab on top (a chapter-number badge) does not
        span = lambda ivs, a, b: sum(max(0, min(e, b) - max(s, a)) for s, e in ivs) >= 0.9 * (b - a)
        edge_h = lambda y: [(s, e) for yy, s, e in gh if abs(yy - y) <= 3]
        edge_v = lambda x: [(s, e) for xx, s, e in gv if abs(xx - x) <= 3]
        if not (span(edge_h(ys[0]), xs[0], xs[-1]) and span(edge_h(ys[-1]), xs[0], xs[-1])
                and span(edge_v(xs[0]), ys[0], ys[-1]) and span(edge_v(xs[-1]), ys[0], ys[-1])):
            continue
        complex_ = False
        for x in xs[1:-1]:
            ivs = [(s, e) for xx, s, e in gv if abs(xx - x) <= 3]
            complex_ |= any(not covers(ivs, ys[k] + 1, ys[k + 1] - 1) for k in range(len(ys) - 1))
        for y in ys[1:-1]:
            ivs = [(s, e) for yy, s, e in gh if abs(yy - y) <= 3]
            complex_ |= any(not covers(ivs, xs[k] + 1, xs[k + 1] - 1) for k in range(len(xs) - 1))
        grids.append(dict(xs=xs, ys=ys, complex=complex_))
    return grids


def _word_count(chars, page_no):
    ls = build_lines(chars, page_no)
    for l in ls:
        _prepare_line(l)
    return sum(len(l["plain"].split()) for l in ls)


def _cell_text(chars, page_no):
    ls = build_lines(chars, page_no)
    for l in ls:
        _prepare_line(l)
    return join_lines([l["md"].strip() for l in ls], set(), collections.Counter()).replace("|", "\\|") if ls else ""


def grid_markdown(grid, chars, page_no):
    """the grid's text as a markdown table (first row = header), or None when it holds no text"""
    xs, ys = grid["xs"], grid["ys"]
    cells = [[[] for _ in range(len(xs) - 1)] for _ in range(len(ys) - 1)]
    for c in chars:
        cx, cy = (c.x0 + c.x1) / 2, (c.top + c.bot) / 2
        r = next((k for k in range(len(ys) - 1) if ys[k] <= cy <= ys[k + 1]), None)
        q = next((k for k in range(len(xs) - 1) if xs[k] <= cx <= xs[k + 1]), None)
        if r is not None and q is not None:
            cells[r][q].append(c)
    rows = [[_cell_text(cell, page_no) for cell in row] for row in cells]
    if not any(any(x.strip() for x in row) for row in rows):
        return None
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


def _two_column_order(lines, W):
    """Two columns on a page: read the left column, then the right, between lines that span the gutter (a heading
    over both columns). The gutter is the x in the middle band of the page crossed by the fewest lines."""
    cands = [W * (0.3 + 0.01 * k) for k in range(41)]
    cross = lambda g: sum(1 for l in lines if l["x0"] < g - 1 and l["x1"] > g + 1)
    g = min(cands, key=lambda g: (cross(g), abs(g - W / 2)))
    out, run = [], []

    def flush():
        out.extend(sorted((l for l in run if l["x1"] <= g + 1), key=lambda l: (round(l["base"]), l["x0"])))
        out.extend(sorted((l for l in run if l["x1"] > g + 1), key=lambda l: (round(l["base"]), l["x0"])))
        run.clear()
    for l in sorted(lines, key=lambda l: (round(l["base"]), l["x0"])):
        if l["x0"] < g - 1 and l["x1"] > g + 1:
            flush()
            out.append(l)
        else:
            run.append(l)
    flush()
    return out


# ---------------------------------------------------------------------------- rows, lines
def build_lines(chars, page_no, orn_fonts=frozenset()):
    """chars -> rows (baseline clustering, content-stream order inside a row) -> segments split at big gaps /
    backward jumps. Small super/subscript rows fold into the row they overlap; drop caps attach to the
    first line they sit next to."""
    chars = [c for c in chars if c.font not in orn_fonts]
    body = statistics.median([c.size for c in chars if c.ch.strip()] or [10.0])
    # (an ornamental initial may be eight times the body size; a letter beyond twelve is art, not text)
    caps = [c for c in chars if c.size >= 2.5 * body and c.ch.isalpha() and c.size < 12 * body]
    # a drop cap stands alone: big letters next to each other are a title on a page with little small text
    caps = [c for c in caps if not any(d is not c and abs(d.base - c.base) < 0.3 * c.size and abs(d.x0 - c.x0) < 2 * c.size
                                       for d in caps)]
    capset = {id(c) for c in caps}
    chars = [c for c in chars if id(c) not in capset]
    rows = []
    for c in sorted(chars, key=lambda c: (c.base, c.idx)):
        if rows and abs(c.base - rows[-1][0].base) <= max(1.2, 0.18 * c.size):
            rows[-1].append(c)
        else:
            rows.append([c])

    def rsize(r):
        s = [c.size for c in r if c.ch.strip()]
        return statistics.median(s) if s else 0
    sizes = [rsize(r) for r in rows]
    merged = set()
    for i, r in enumerate(rows):
        if not sizes[i]:
            continue
        rx0, rx1 = min(c.x0 for c in r), max(c.x1 for c in r)
        tiny = all(c.ch in "¹²³⁴⁵⁶⁷⁸⁹⁰₀₁₂₃₄₅₆₇₈₉⁄/ " for c in r)
        best = None
        for j, n in enumerate(rows):
            if j == i or j in merged or sizes[j] <= 0 or (sizes[i] > 0.8 * sizes[j] and not tiny):
                continue
            nx0, nx1 = min(c.x0 for c in n), max(c.x1 for c in n)
            if nx0 - 1 <= rx0 and rx1 <= nx1 + 1 and abs(r[0].base - n[0].base) <= (1.1 if tiny else 0.9) * sizes[j]:
                d = abs(r[0].base - n[0].base)
                if best is None or d < best[0]:
                    best = (d, j)
        if best:
            rows[best[1]] = rows[best[1]] + r
            merged.add(i)
    rows = [r for i, r in enumerate(rows) if i not in merged]
    segs = []
    for r in rows:
        r.sort(key=lambda c: c.idx)
        seg = [r[0]]
        for a, b in zip(r, r[1:]):
            # a ligature (Th, fi, ffl ...) comes out as several chars on ONE glyph box: the second starts where the
            # first did, which is not a jump back to an earlier part of the line
            same_box = abs(b.x0 - a.x0) < 0.05 * max(b.size, 1.0)
            fwd, back = b.x0 - a.x1, (0.0 if same_box else a.x1 - b.x0)
            if (fwd > max(2.5 * b.size, 18) or back > 0.9 * b.size) and b.ch.strip() and a.ch.strip():
                segs.append(seg)
                seg = [b]
            else:
                seg.append(b)
        segs.append(seg)
    out = []
    for seg in segs:
        if not "".join(c.ch for c in seg).strip():
            continue
        cnt = collections.Counter(size_key(c.font, c.size) for c in seg if c.ch.strip())
        key = cnt.most_common(1)[0][0]
        out.append(dict(chars=seg, x0=min(c.x0 for c in seg), x1=max(c.x1 for c in seg), top=min(c.top for c in seg),
                        bot=max(c.bot for c in seg), base=statistics.median(c.base for c in seg), style=key,
                        size=float(key.split("|")[1]), page=page_no))
    out.sort(key=lambda l: (round(l["base"]), l["x0"]))
    for dc in caps:
        dc.dropcap = True
        cands = [l for l in out if l["x0"] >= dc.x0 - 1 and abs(l["top"] - dc.top) <= 0.2 * dc.size + 3]
        if cands:
            # the line beside the initial's top (a tall initial reaches several lines; their left edges differ by
            # hairs, so the top decides)
            l = min(cands, key=lambda l: (abs(l["top"] - dc.top) > 0.5 * l["size"], abs(l["top"] - dc.top), l["x0"]))
            # the initial is the first letter of the word, not a styled run of its own ("**O**ne" -> "One")
            dc.italic, dc.bold = l["chars"][0].italic, l["chars"][0].bold
            l["chars"].insert(0, dc)
            l["x0"] = min(l["x0"], dc.x0)
            l["top"] = min(l["top"], dc.top)
        else:
            out.append(dict(chars=[dc], x0=dc.x0, x1=dc.x1, top=dc.top, bot=dc.bot, base=dc.base,
                            style=size_key(dc.font, dc.size), size=dc.size, page=page_no))
    out.sort(key=lambda l: (round(l["base"]), l["x0"]))
    return out


def _two_clusters(gaps):
    """Otsu split of a list of gap widths: (threshold, mean of the small gaps, mean of the large gaps)"""
    s = sorted(gaps)
    n, tot, left, best = len(s), sum(s), 0.0, None
    for k in range(1, n):
        left += s[k - 1]
        m1, m2 = left / k, (tot - left) / (n - k)
        v = k * (n - k) * (m1 - m2) ** 2
        if best is None or v > best[0]:
            best = (v, (s[k - 1] + s[k]) / 2, m1, m2)
    return best[1:] if best else (float("inf"), 0.0, 0.0)


def line_tokens(line):
    """(char, italic, bold) tokens with inferred word spaces.
    Letter-spaced (tracked) lines: the gaps between glyphs fall into two clusters, letter gaps and word gaps; a word
    break is a gap in the large cluster, and the line's own space glyphs are ignored (some PDFs put one between every
    letter). One tracked word has a single cluster: no break.
    Other lines: real ' ' chars are honoured (unless hidden inside the previous glyph); extra spaces are inferred
    from the advance gap (0.5 em for ALL-CAPS with real spaces, 0.2 em otherwise) or from pdfium's generated spaces."""
    cs = line["chars"]
    size = line["size"]
    ink = [c for c in cs if c.ch != " "]
    gaps = [b.ox - a.lr for a, b in zip(ink, ink[1:])]
    med = sorted(gaps)[len(gaps) // 2] if gaps else 0.0
    if len(ink) >= 4 and med > 0.08 * size:
        thr, m1, m2 = _two_clusters(gaps)
        if not (m2 - m1 > 0.15 * size and m2 > 1.8 * max(m1, 0.02 * size)):
            thr = float("inf")
        toks, prev = [], None
        for c in ink:
            if prev is not None and not prev.dropcap and c.ox - prev.lr > thr:
                toks.append((" ", prev.italic, prev.bold))
            toks.append((c.ch, c.italic, c.bold))
            prev = c
        return toks
    # some PDFs emit a space glyph after every fi/fl ligature that takes the second half of the ligature's own
    # advance (the space starts ~0.14 em per letter after the ligature's origin, the next letter right after it): no
    # word break. A real space after "ff" starts a full ligature advance later. A ligature is chars on one glyph box
    # (same left and right edge; an overhanging f only shares its neighbour's left edge).
    def fake_space(i):
        c = cs[i]
        if not (c.ch == " " and 1 < i < len(cs) - 1 and cs[i - 1].ch.isalpha()):
            return False
        k = i - 1
        while k > 0 and abs(cs[k - 1].x0 - cs[i - 1].x0) < 0.05 * size and abs(cs[k - 1].x1 - cs[i - 1].x1) < 0.05 * size:
            k -= 1
        n = i - k
        return n >= 2 and c.ox - cs[i - 1].ox < 0.2 * n * size and cs[i + 1].ox - cs[i - 1].lr < 0.08 * size
    cs = [c for i, c in enumerate(cs) if not fake_space(i)]
    has_space = any(c.ch == " " for c in cs)
    letters = [c for c in cs if c.ch.isalpha()]
    caps = sum(c.ch.isupper() for c in letters) / max(1, len(letters))
    if has_space and caps > 0.8:
        thr = 0.5 * size
    else:
        thr = 0.2 * size
    # pdfium's own generated space is a second opinion where a clipped glyph box (an "f" in some fonts, kerned
    # capitals) hides the gap; it also fires inside lightly tracked titles, so the gap must still stand out from
    # the line's usual letter gap
    lgaps = sorted(b.ox - a.lr for a, b in zip(cs, cs[1:]) if not a.ch.isspace() and not b.ch.isspace())
    lmed = lgaps[len(lgaps) // 2] if lgaps else 0.0
    toks, prev = [], None
    for c in cs:
        if prev is not None and c.ch != " " and prev.ch != " " and not prev.dropcap:
            gap = c.ox - prev.lr
            gen = c.gsp and not c.ch.isspace() and not prev.ch.isspace() and gap - lmed >= 0.06 * size
            if gap > thr or gen:
                toks.append((" ", prev.italic, prev.bold))
        toks.append((c.ch, c.italic, c.bold))
        prev = c
    return toks


def inline_md(toks, bold_ok=True):
    out, cur, buf = [], None, ""

    def flush():
        nonlocal buf
        if buf:
            core = buf.strip()
            lead, trail = buf[:len(buf) - len(buf.lstrip())], buf[len(buf.rstrip()):]
            i, b = cur
            if core:
                if b and bold_ok and i:
                    core = f"***{core}***"
                elif b and bold_ok:
                    core = f"**{core}**"
                elif i:
                    core = f"*{core}*"
            out.append(lead + core + trail)
        buf = ""
    for ch, i, b in toks:
        st = (i, b) if ch != " " else (cur or (i, b))
        if cur is None:
            cur = st
        if st != cur:
            flush()
            cur = st
        buf += ch
    flush()
    return "".join(out)


def _prepare_line(l):
    toks = line_tokens(l)
    l["md"], l["md_nobold"] = inline_md(toks, True), inline_md(toks, False)
    l["plain"] = "".join(t[0] for t in toks)
    ne = [c for c in l["chars"] if c.ch.strip()]
    n = max(1, len(ne))
    l["italic_share"] = sum(1 for c in ne if c.italic) / n
    l["bold_share"] = sum(1 for c in ne if c.bold) / n
    l["w0"] = l["chars"][0].weight
    l["mix"] = collections.Counter(size_key(c.font, c.size) for c in ne)
    l["mix_flag"] = collections.Counter(size_key(c.font, c.size) for c in ne if c.italic or c.bold)
    del l["chars"]                      # keep memory flat on long books


def two_column_suspect(lines, W):
    left = [l for l in lines if l["x0"] < 0.4 * W]
    # a right-hand line with no word in it (a contents page's page numbers, roman or arabic) is no column
    right = [l for l in lines if l["x0"] > 0.5 * W and l["x1"] <= W + 1
             and any(len(w) >= 3 and not re.fullmatch(r"[ivxlcdm]+", w, re.I) for w in re.findall(r"[^\W\d_]+", l["plain"]))]
    rows = sum(1 for r in right if any(abs(r["base"] - l["base"]) < 2 and l["x1"] < r["x0"] for l in left))
    return rows >= 4, rows


# ---------------------------------------------------------------------------- blocks
def _in_box(l, boxes):
    cx, cy = (l["x0"] + l["x1"]) / 2, (l["top"] + l["bot"]) / 2
    for i, (a, b, c, d) in enumerate(boxes):
        if a - 1 <= cx <= c + 1 and b - 1 <= cy <= d + 1:
            return i
    return None


def _finish_block(b):
    ls = b["lines"]
    b["top"], b["bot"] = min(l["top"] for l in ls), max(l["bot"] for l in ls)
    b["x0"], b["x1"] = min(l["x0"] for l in ls), max(l["x1"] for l in ls)
    b["size"] = ls[0]["size"]
    b["nwords"] = sum(len(l["plain"].split()) for l in ls)
    tot = max(1, sum(len(l["plain"]) for l in ls))
    b["italic_share"] = sum(l["italic_share"] * len(l["plain"]) for l in ls) / tot
    b["text"] = " ".join(l["plain"] for l in ls)


def build_blocks(page_no, W, H, chars, boxes, orn_fonts=frozenset(), images=(), hl=(), vl=()):
    # text outside the page box (facing-page copies in spreads) is not part of this page
    chars = [c for c in chars if -2 <= (c.x0 + c.x1) / 2 <= W + 2 and -2 <= (c.top + c.bot) / 2 <= H + 2]
    # text set at an angle is a watermark or an edge tab, never part of the reading flow
    rotated = sum(1 for c in chars if c.rot and c.ch.strip())
    chars = [c for c in chars if not c.rot]
    inside = lambda c, b: b[0] - 1 <= (c.x0 + c.x1) / 2 <= b[2] + 1 and b[1] - 1 <= (c.top + c.bot) / 2 <= b[3] + 1
    items, fig_words, fig_text = [], 0, []
    # a figure: its place in the flow, and the lettering inside it (diagram labels) belongs to the figure. Judged line
    # by line: only a line wholly inside the picture goes, and never a title or caption printed inside the picture's
    # box ("Fig. 2: ...", a wide line along its top or bottom edge): those become the figure's captions. A lot of text
    # over an image is real text set on a background: it stays.
    page_lines = build_lines(chars, page_no, orn_fonts) if images else []
    for k, im in enumerate(sorted(images, key=lambda b: (round(b[1]), b[0])), 1):
        h, w = im[3] - im[1], im[2] - im[0]

        def label(l):
            """a diagram label, as opposed to a title or caption printed inside the picture's box: one that reads like a
            caption, or a wide line (35%+ of the picture) in the top or bottom 15% of it, stays in the text"""
            if CAPTION.match("".join(c.ch for c in l["chars"]).replace("*", "")):
                return False
            wide = l["x1"] - l["x0"] >= 0.35 * w
            edge = l["bot"] <= im[1] + 0.15 * h or l["top"] >= im[3] - 0.15 * h
            return not (wide and edge)
        own_lines = [l for l in page_lines if l["chars"] and all(inside(c, im) for c in l["chars"]) and label(l)]
        own = [c for l in own_lines for c in l["chars"]]
        words = sum(len("".join(c.ch for c in l["chars"]).split()) for l in own_lines)
        if own and words <= 40:
            ids = {id(c) for c in own}
            chars = [c for c in chars if id(c) not in ids]
            fig_words += words
            fig_text.append(" ".join("".join(c.ch for c in l["chars"]) for l in own_lines))
        items.append(dict(kind="image", page=page_no, n=k, x0=im[0], top=im[1], x1=im[2], bot=im[3]))
    # ruled tables: one segment each; a table with merged cells is marked like a figure
    for k, gr in enumerate(find_grids(hl, vl) if hl and vl else [], 1):
        box = (gr["xs"][0], gr["ys"][0], gr["xs"][-1], gr["ys"][-1])
        own = [c for c in chars if inside(c, box)]
        md = None if gr["complex"] else grid_markdown(gr, own, page_no)
        if not own or (md is None and not gr["complex"]):
            continue
        ids = {id(c) for c in own}
        chars = [c for c in chars if id(c) not in ids]
        items.append(dict(kind="table", page=page_no, n=k, x0=box[0], top=box[1], x1=box[2], bot=box[3], md=md,
                          complex=gr["complex"], words=_word_count(own, page_no),
                          text="".join(c.ch for c in sorted(own, key=lambda c: c.idx))))
    lines = build_lines(chars, page_no, orn_fonts)
    for l in lines:
        _prepare_line(l)
        l["box"] = _in_box(l, boxes)
    if two_column_suspect([l for l in lines if l["size"] < 20], W)[0]:
        lines = _two_column_order(lines, W)
    margin_c = collections.defaultdict(collections.Counter)
    for l in lines:
        margin_c[(l["style"], l["box"] is not None)][round(l["x0"])] += 1
    margin = {k: min(v, key=lambda x: (-v[x], x)) for k, v in margin_c.items()}
    pitch = collections.defaultdict(list)
    for a, b in zip(lines, lines[1:]):
        if a["style"] == b["style"] and 0 < b["base"] - a["base"] < 2.2 * a["size"]:
            pitch[a["style"]].append(b["base"] - a["base"])
    pit = {k: statistics.median(v) for k, v in pitch.items()}
    blocks, cur = [], None

    def new(l):
        return dict(lines=[l], style=l["style"], box=l["box"], page=page_no, list=bool(ITEM.match(l["plain"])),
                    mix=collections.Counter(l["mix"]))

    full = [l["x1"] for l in lines if len(l["plain"].split()) >= 6]
    right = statistics.median(full) if full else W

    def inline_run(l, a):
        """a line whose own majority style differs from the block's is still the block's text when the change is an
        inline run (an italic title, a bold lead-in, another font): the same size, the previous line runs to the
        right edge (a short line is a heading or a paragraph's end), and either one of the two lines mixes both styles
        or the line goes on with the previous line's open sentence in lowercase (a heading starts with a capital)"""
        if abs(l["size"] - a["size"]) > 0.25 or a["x1"] < right - 1.5 * a["size"]:
            return False
        return (cur["style"] in l["mix"] or l["style"] in a["mix"]
                or (not terminal(a["plain"]) and bool(re.match(r"[\W\d_]*[a-z]", l["plain"]))))
    def indented_run(l, a, mg):
        """an indented line under a line with the same indent is no first-line indent but an indented block (a quote,
        an epigraph) going on: the line above ran to the right edge without ending its sentence"""
        return (abs(l["x0"] - a["x0"]) < 1 and a["x0"] - mg > 0.7 * l["size"] and a["x1"] >= right - 3 * a["size"]
                and not terminal(a["plain"]))
    for l in lines:
        if cur is None:
            cur = new(l)
            continue
        a = cur["lines"][-1]
        gap = l["base"] - a["base"]
        restyled = l["style"] != cur["style"] and not (l["box"] == cur["box"] and inline_run(l, a))
        st = cur["style"] if not restyled else l["style"]
        ref = pit.get(st, pit.get(l["style"], 1.3 * l["size"]))
        mg = margin.get((st, l["box"] is not None), margin[(l["style"], l["box"] is not None)])
        if restyled or l["box"] != cur["box"]:
            brk = True
        elif gap > 1.45 * ref or (gap < 0.3 * l["size"] and l["x0"] < a["x1"]):
            brk = True
        elif ITEM.match(l["plain"]) and len(l["plain"].strip()) > 3:
            brk = True
        elif (l["x0"] - mg > 0.7 * l["size"] and not (cur["list"] and l["x0"] >= cur["lines"][0]["x0"] - 1)
              and not indented_run(l, a, mg)):
            brk = True
        else:
            brk = False
        if brk:
            blocks.append(cur)
            cur = new(l)
        else:
            cur["lines"].append(l)
            cur["mix"].update(l["mix"])
            cur["style"] = cur["mix"].most_common(1)[0][0]     # a block's role follows its dominant style
    if cur:
        blocks.append(cur)
    for b in blocks:
        _finish_block(b)
    return dict(p=page_no, W=W, H=H, blocks=blocks, boxes=boxes, rotated=rotated, items=items, fig_words=fig_words,
                fig_text=fig_text)


def _roman(s):
    v = dict(i=1, v=5, x=10, l=50, c=100, d=500, m=1000)
    n = [v[c] for c in s.lower()]
    return sum(-a if a < b else a for a, b in zip(n, n[1:] + [0]))


def _similar_key(text):
    """the letters of a line without its page-number-like words (numbers, words with digits, roman numerals), so a
    slug or footer that carries the page number compares equal from page to page"""
    return re.sub(r"[\W\d_]+", "", "".join(w.lower() for w in text.split()
                                         if not re.search(r"\d", w) and not re.fullmatch(r"[ivxlcdm]+\W*", w, re.I)))


# ---------------------------------------------------------------------------- document level analysis
class Doc:
    def __init__(self, pages):
        self.pages = pages
        self.NP = len(pages)
        widths = [p["W"] for p in pages]
        self.W0 = statistics.median(widths) if widths else 0
        self._mark_cover_and_repeats()
        self._filter_boxes()
        self._recheck_two_col()
        self.S = self._style_stats()
        self.sugg, self.why, self.body_style, self.body_styles = self._suggest()
        self._block_roles()
        self._title_running_heads()
        self._index_pages()
        self._object_groups()
        self._shape_roles()
        self.lex = self._hyphen_lexicon()

    # -- margin text: running heads/feet, page numbers, slug, watermark ------------------------------
    def _mark_cover_and_repeats(self):
        """The margin is what lies above or below the book's text block: the median top of the first and bottom of
        the last body-size line over all pages (a fixed share of the page height misses books whose text block sits
        in the middle of a large page). In the margin a lone number is a page number, and a line whose letters
        (spaces, digits and punctuation ignored, so letter-spaced heads compare too) recur on 3+ pages and is not
        larger than body text is a running head/foot or slug. In the outer bands of the page (top 9%, bottom 14%), a
        style at the same spot on 35%+ of the pages is a watermark or footer when its text (digits ignored) recurs
        too; never the body style or a body-size line wider than half the text block, as the body often runs into the
        band."""
        words, swords = collections.Counter(), collections.Counter()
        for p in self.pages:
            for b in p["blocks"]:
                for l in b["lines"]:
                    words[l["size"]] += len(l["plain"].split())
                    swords[l["style"]] += len(l["plain"].split())
        bsize = max(words, key=words.get) if words else 10.0
        bstyle = max(swords, key=swords.get) if swords else None
        widths = [l["x1"] - l["x0"] for p in self.pages for b in p["blocks"] for l in b["lines"] if l["style"] == bstyle]
        bwidth = statistics.median(widths) if widths else 0.0
        # The text block is where body lines sit. A running head set near the body size counts as one unless it is
        # left out: lines short of the full measure whose text (page numbers ignored) recurs on 3+ pages, and, for the
        # top edge, lines narrower than half the text block (a head, a folio with a title).
        recur = collections.defaultdict(set)
        for p in self.pages:
            for b in p["blocks"]:
                for l in b["lines"]:
                    recur[_similar_key(l["plain"])].add(p["p"])
        tops, bots = [], []
        for p in self.pages:
            bl = [l for b in p["blocks"] for l in b["lines"] if abs(l["size"] - bsize) <= 0.6 and len(l["plain"].split()) >= 3
                  and not (len(recur[_similar_key(l["plain"])]) >= 3 and l["x1"] - l["x0"] < 0.9 * bwidth)]
            # (narrow lines only say nothing about the top: at the bottom a paragraph's short last line is body text)
            wide = [l for l in bl if l["x1"] - l["x0"] >= 0.5 * bwidth]
            if len(bl) >= 3 and wide:
                tops.append(min(l["top"] for l in wide))
                bots.append(max(l["bot"] for l in bl))
        self.text_top = statistics.median(tops) if tops else None
        self.text_bot = statistics.median(bots) if bots else None

        def zone(p, l):
            if self.text_top is None:
                return "top" if l["top"] < 0.09 * p["H"] else "bot" if l["top"] > 0.86 * p["H"] else None
            return "top" if l["bot"] <= self.text_top - 0.5 else "bot" if l["top"] >= self.text_bot + 0.5 else None
        rep, pos, same = collections.defaultdict(set), collections.defaultdict(set), collections.defaultdict(set)
        for p in self.pages:
            for b in p["blocks"]:
                for l in b["lines"]:
                    l["zone"] = zone(p, l)
                    l["key"] = "".join(ch for ch in l["plain"].lower() if ch.isalpha())
                    if l["zone"] and l["key"]:
                        rep[(l["zone"], l["key"])].add(p["p"])
                    pos[(l["style"], round(l["top"] / 4))].add(p["p"])
                    same[(l["style"], _similar_key(l["plain"]))].add(p["p"])
        # a running head carries the page number: a margin line of number + a few words whose number is the page's
        # (the same offset from the PDF page as most such lines; roman and arabic counted apart) goes whatever its
        # text, so heads that change from chapter to chapter go too
        def folio(l):
            m = l["zone"] and l["size"] <= 1.05 * bsize and FOLIO.match(l["plain"])
            if not m or len(l["plain"].split()) > 9:
                return None
            n = m.group(1) or m.group(2)
            return ("a", int(n)) if n.isdigit() else ("r", _roman(n))
        offs = collections.Counter()
        for p in self.pages:
            for b in p["blocks"]:
                for l in b["lines"]:
                    f = folio(l)
                    if f and f[1]:
                        offs[(f[0], f[1] - p["p"])] += 1
        best = {k: max((o for o in offs if o[0] == k), key=offs.get, default=None) for k in ("a", "r")}
        need = max(4, 0.35 * self.NP)
        for p in self.pages:
            p["cover"] = bool(self.W0) and abs(p["W"] - self.W0) > 0.2 * self.W0
            p["running_removed"] = 0
            for b in p["blocks"]:
                for l in b["lines"]:
                    f = folio(l)
                    head = bool(f and f[1] and best[f[0]] and offs[best[f[0]]] >= 3 and f[1] - p["p"] == best[f[0]][1])
                    bodylike = l["style"] == bstyle or (abs(l["size"] - bsize) <= 0.6 and l["x1"] - l["x0"] > 0.5 * bwidth)
                    band = ((l["top"] < 0.09 * p["H"] or l["top"] > 0.86 * p["H"]) and not bodylike
                            and len(same[(l["style"], _similar_key(l["plain"]))]) >= 3)
                    l["sil"] = bool(
                        (l["zone"] and PAGE_NO.match(l["plain"]))
                        or (l["zone"] and l["key"] and len(rep[(l["zone"], l["key"])]) >= 3 and l["size"] <= 1.05 * bsize)
                        or head
                        or (band and len(pos[(l["style"], round(l["top"] / 4))]) >= need))
                    p["running_removed"] += l["sil"] and not PAGE_NO.match(l["plain"])
                kept = [l for l in b["lines"] if not l["sil"]]
                b["sil"] = not kept
                if kept and len(kept) < len(b["lines"]):
                    b["lines"] = kept
                    _finish_block(b)
                b["edge"] = all(l["zone"] for l in b["lines"])

    def _filter_boxes(self, min_words=15):
        """tinted shapes holding fewer than min_words words are figure panels, not boxes"""
        for p in self.pages:
            wc = collections.Counter()
            for b in p["blocks"]:
                if b["box"] is not None and not b["sil"]:
                    wc[b["box"]] += b["nwords"]
            for b in p["blocks"]:
                if b["box"] is not None and wc[b["box"]] < min_words:
                    b["box"] = None

    def _recheck_two_col(self):
        for p in self.pages:
            ls = [l for b in p["blocks"] if not b["sil"] for l in b["lines"] if l["size"] < 20]
            p["two_col"], p["two_rows"] = two_column_suspect(ls, p["W"])

    def _style_stats(self):
        S = collections.OrderedDict()
        for p in self.pages:
            if p["cover"]:
                continue
            for b in p["blocks"]:
                if b["sil"]:
                    continue
                s = S.setdefault(b["style"], dict(words=0, pages=set(), first=p["p"], last=p["p"], blocks=0, lines=0,
                                                  chars=0, inbox=0, caps=0, short=0, ital=0, num=0, dash=0, letters=0,
                                                  multiline=0, mlines=0, sent=0, wts=[], edge=0))
                txt = b["text"]
                letters = [c for c in txt if c.isalpha()]
                s["words"] += b["nwords"]
                s["pages"].add(p["p"])
                s["last"] = p["p"]
                s["blocks"] += 1
                s["lines"] += len(b["lines"])
                s["chars"] += len(txt)
                s["inbox"] += b["box"] is not None
                s["caps"] += (sum(c.isupper() for c in letters) / max(1, len(letters)) > 0.8)
                s["letters"] += len(letters)
                s["short"] += (len(b["lines"]) <= 3 and len(txt) <= 120)
                s["ital"] += b["italic_share"] > 0.6
                s["num"] += bool(NUM.match(b["lines"][0]["plain"]))
                s["dash"] += bool(DASH.match(txt))
                s["multiline"] += len(b["lines"]) >= 2
                s["mlines"] += len(b["lines"]) if len(b["lines"]) >= 2 else 0
                s["sent"] += bool(re.search(r"[.!?]\s*$", txt))
                s["edge"] += b.get("edge", False)
                s["wts"] += [l["w0"] for l in b["lines"]]
        for s in S.values():
            s["weight"] = statistics.median(s["wts"]) if s["wts"] else 400
        return S

    @staticmethod
    def score(style, s):
        """heading rank: size, +2 for ALL CAPS (a caps section title outranks a mixed-case sub-title of similar size),
        +0.3 for bold"""
        return fam_of(style)[1] + (2.0 if s["caps"] / s["blocks"] > 0.8 else 0) + (0.3 if s["weight"] >= 600 or "Bold" in style else 0)

    def _suggest(self):
        S = self.S
        if not S:
            self.body_keys = set()
            return {}, {}, None, set()
        body_style = max(S, key=lambda k: S[k]["words"])
        bfam, bsize = fam_of(body_style)
        # decorative front-matter styles live on the first pages; in a short document "first pages" is just a couple
        front = min(8, max(2, int(0.03 * self.NP)))
        self.bfam, self.bsize = bfam, bsize
        sugg, why = {}, {}
        body_styles = {k for k, s in S.items() if s["words"] >= 0.15 * S[body_style]["words"] and s["lines"] / s["blocks"] >= 3}
        body_styles.add(body_style)         # the most-worded style is body even when its paragraphs come out short
        body_keys = {fam_of(k) for k in body_styles}
        # fonts that behave like body text are not anomalies; they do not change roles (a bold run-in font is also
        # the font of the sub-heads)
        self.body_keys = body_keys | {fam_of(k) for k in self._body_variants(body_styles)}
        self.variant_of_body = lambda k: fam_of(k) in body_keys and k not in body_styles
        for k, s in S.items():
            n = s["blocks"]
            f, sz = fam_of(k)
            avgl = s["lines"] / n
            poemlike = (f != bfam and s["mlines"] / s["lines"] >= 0.6 and avgl >= 1.5 and 0.9 * bsize <= sz <= 1.3 * bsize
                        and s["ital"] / n >= 0.5 and s["sent"] / n < 0.95)
            if s["letters"] == 0 and sz >= 1.5 * bsize:
                sugg[k] = ("sil", why.setdefault(k, "harf yok, büyük boy: süs rakamı"))
                continue
            if s["letters"] == 0:
                sugg[k] = ("gövde", "harf yok, küçük boy: rakam/kesir")
                continue
            if k in body_styles:
                sugg[k] = ("gövde", "en çok kelimeli stil")
                continue
            if self.variant_of_body(k) and n <= 0.2 * max(1, S[body_style]["blocks"]):
                sugg[k] = ("gövde", "gövde ailesi/boyutunun italik-kalın varyantı")
                continue
            if s["dash"] / n >= 0.6:
                sugg[k] = ("atıf", "—'yle başlıyor")
                continue
            if s["inbox"] / n >= 0.6 and s["short"] / n >= 0.9:
                sugg[k] = ("kutu-başlık", "kutu içinde, kısa")
                continue
            if s["num"] / n >= 0.6:
                sugg[k] = ("liste", "numarayla başlıyor")
                continue
            if poemlike:
                sugg[k] = ("şiir/epigraf", "farklı aileden, çok satırlı, kısa satırlar")
                continue
            if s["last"] <= front and s["words"] <= 150 and not (n >= 2 and avgl >= 3 and s["words"] >= 100):
                sugg[k] = ("ön sayfa", "yalnızca ilk sayfalarda")
                continue
        emph = lambda k: (fam_of(k)[1] > bsize * 0.95 or S[k]["caps"] / S[k]["blocks"] > 0.5 or S[k]["weight"] >= 600 or "Bold" in k)
        # a heading is at least 0.6 of the body size (small bold sub-heads exist; 4.5pt lettering above the text block
        # is a running head); a small style that mostly sits in the margin is a running head that varied too much to
        # be removed, not a heading. Display type (1.4x body and up) may run longer: a profile or chapter title that
        # names a work
        display = lambda k: fam_of(k)[1] >= 1.4 * bsize
        big_enough = lambda k: fam_of(k)[1] >= 0.6 * bsize
        marginal = lambda k: S[k]["edge"] / S[k]["blocks"] >= 0.5 and fam_of(k)[1] <= 1.05 * bsize
        cands = [k for k, s in S.items() if k not in sugg and s["blocks"] >= 5 and len(s["pages"]) >= 3
                 and s["chars"] / s["blocks"] >= 4 and s["short"] / s["blocks"] >= (0.8 if display(k) else 0.9)
                 and s["words"] / s["blocks"] <= (20 if display(k) else 12)
                 and s["sent"] / s["blocks"] < 0.5 and emph(k) and s["inbox"] / s["blocks"] < 0.5
                 and s["words"] >= 8 and s["num"] / s["blocks"] < 0.3 and big_enough(k) and not marginal(k)]
        cands.sort(key=lambda k: -self.score(k, S[k]))
        tiers = []
        for k in cands:
            if tiers and abs(self.score(tiers[-1][0], S[tiers[-1][0]]) - self.score(k, S[k])) <= 0.5:
                tiers[-1].append(k)
            else:
                tiers.append([k])
        for ti, tier in enumerate(tiers):
            role = ["kısım", "bölüm", "alt başlık 1", "alt başlık 1", "alt başlık 2"][ti] if ti < 5 else "alt başlık 2"
            for k in tier:
                sugg[k] = (role, f"başlık kademesi {ti + 1}/{len(tiers)}")
        for k, s in S.items():
            if k not in sugg:
                sugg[k] = ("ön sayfa" if (s["words"] <= 15 and s["last"] <= front) else "gövde", "kural eşleşmedi")
        return sugg, why, body_style, body_styles

    def _body_variants(self, body_styles):
        """Fonts are matched by behaviour, not by name (subset names like AdvP6F00/AdvP6F09 say nothing, and such
        fonts often carry no italic/bold flag either). A body variant is
        - a font set inside body lines, next to the body font at the body's size (ten such lines; three when the font
          carries the italic/bold flag), or
        - a font at the body's size and line pitch that sets paragraphs of its own: a fifth of its blocks run to
          several lines and they end a sentence at least half as often as body paragraphs do (emphasised
          statements, an italic body)."""
        pitch = collections.defaultdict(list)
        for p in self.pages:
            for b in p["blocks"]:
                if not b["sil"]:
                    pitch[b["style"]] += [y["base"] - x["base"] for x, y in zip(b["lines"], b["lines"][1:])]
        main = max(body_styles, key=lambda k: self.S[k]["words"]) if body_styles else None
        bp = statistics.median(pitch[main] or [0]) if main else 0
        bsz = {fam_of(k)[1] for k in body_styles}
        own = {k for k, s in self.S.items() if k not in body_styles and bp and pitch[k]
               and any(abs(fam_of(k)[1] - z) <= 0.25 for z in bsz) and abs(statistics.median(pitch[k]) - bp) <= 0.05 * bp
               and s["multiline"] >= 0.2 * s["blocks"]
               and s["sent"] / s["blocks"] >= 0.5 * self.S[main]["sent"] / self.S[main]["blocks"]}
        inside, flagged = collections.Counter(), collections.Counter()
        for p in self.pages:
            for b in p["blocks"]:
                if b["sil"]:
                    continue
                for l in b["lines"]:
                    if l["style"] not in body_styles:
                        continue
                    mix = l.get("mix", {})
                    for k in mix:
                        if k != l["style"] and abs(fam_of(k)[1] - fam_of(l["style"])[1]) <= 0.25:
                            inside[k] += 1
                            flagged[k] += l["mix_flag"][k] >= 0.8 * mix[k]
        return own | {k for k, n in inside.items() if n >= 10 or (n >= 3 and flagged[k] >= 0.8 * n)}

    def _heading_for(self, block):
        """role for a one-off heading-like block, taken from the heading style whose rank is closest"""
        S = self.S
        letters = [c for c in block["text"] if c.isalpha()]
        caps = sum(c.isupper() for c in letters) / max(1, len(letters)) > 0.8
        w = block["lines"][0]["w0"]
        sc = block["size"] + (2.0 if caps else 0) + (0.3 if w >= 600 else 0)
        best = None
        for k, (r, _) in self.sugg.items():
            if r in HEAD and S[k]["blocks"] >= 5:
                d = abs(self.score(k, S[k]) - sc)
                if best is None or d < best[0]:
                    best = (d, r)
        return best[1] if best else None

    def _block_roles(self):
        S, sugg = self.S, self.sugg
        front = min(8, max(2, int(0.03 * self.NP)))
        titles = set()
        for pi, p in enumerate(self.pages):
            prev = None
            real = [b for b in p["blocks"] if not b["sil"]]
            # a front page set in display type only (title page, author's name) is no chapter heading, and neither is
            # a later half title that repeats it
            title_page = False
            if real and all(b["size"] >= 1.3 * self.bsize for b in real):
                key = "".join(c for b in real for c in b["text"].lower() if c.isalpha())
                title_page = pi < front or key in titles
                if pi < front:
                    titles.add(key)
            for b in p["blocks"]:
                r = "sil" if b["sil"] else sugg.get(b["style"], ("gövde",))[0]
                if p["cover"] or (title_page and r != "sil"):
                    r = "ön sayfa"
                # a paragraph whose dominant style is a heading style only through its runs (long italic titles
                # among body text) is no heading; a long title set wholly in its style still is
                mix = b.get("mix") or {}
                if (r in HEAD and len(b["lines"]) > 3 and b["nwords"] > 30
                        and sum(n for k, n in mix.items() if k in self.body_styles) >= 0.15 * sum(mix.values())):
                    r = "gövde"
                txt = b["text"]
                if r not in ("sil", "ön sayfa"):
                    if r == "şiir/epigraf":
                        if len(b["lines"]) == 1 and b["nwords"] <= 8:
                            r = self._heading_for(b) or "gövde"
                        else:
                            r = "epigraf" if len(b["lines"]) <= 4 else "şiir"
                    if r != "kutu-başlık" and DASH.match(txt) and len(b["lines"]) <= 2 and prev in ("şiir", "epigraf"):
                        r = "atıf"
                    if NUM.match(b["lines"][0]["plain"]) and r in ("gövde", "liste"):
                        r = "liste"
                    if b["box"] is not None and r == "gövde":
                        r = "kutu"
                b["role"] = r
                if r != "sil":
                    prev = r
            # one-off headings: a short single line in a non-body style, between body paragraphs
            for i, b in enumerate(real):
                if b["role"] != "gövde" or b["box"] is not None or p["cover"]:
                    continue
                st = b["style"]
                if st in self.body_styles or self.variant_of_body(st) or len(b["lines"]) > 2 or b["nwords"] > 10:
                    continue
                txt = b["text"].strip()
                if not txt or re.search(r"[.,;!?]\s*$", txt) or NUM.match(txt):
                    continue
                letters = [c for c in txt if c.isalpha()]
                caps = sum(c.isupper() for c in letters) / max(1, len(letters)) > 0.8
                if not (b["size"] >= 0.95 * self.bsize or (caps and b["size"] >= 0.75 * self.bsize)):
                    continue
                if i > 0 and real[i - 1]["style"] == st:
                    continue
                if i + 1 >= len(real) or real[i + 1]["role"] not in ("gövde", "kutu", "şiir", "epigraf", "liste"):
                    continue
                role = self._heading_for(b)
                if role:
                    b["role"] = role
            # first block of a box whose style differs from the box majority (and short) is its title
            byb = collections.defaultdict(list)
            for b in p["blocks"]:
                if b["box"] is not None and b["role"] in ("kutu", "gövde", "kutu-başlık"):
                    byb[b["box"]].append(b)
            for bl in byb.values():
                maj = collections.Counter(b["style"] for b in bl).most_common(1)[0][0]
                if bl[0]["style"] != maj and len(bl[0]["lines"]) <= 2:
                    bl[0]["role"] = "kutu-başlık"
            # a heading follows a block that ended: one in the same column, size and flow (below it, not overlaid) after
            # a sentence left open with no extra space above, or starting in lowercase, is the paragraph going on
            prev = None
            for b in real:
                if (b["role"] in HEAD and prev is not None and prev["role"] in ("gövde", "kutu", "liste")
                        and not terminal(prev["text"]) and prev["box"] == b["box"] and abs(b["x0"] - prev["x0"]) < 2 * self.bsize
                        and abs(b["size"] - prev["size"]) <= 0.25 and b["top"] >= prev["bot"] - 1
                        and (b["top"] - prev["bot"] < 0.6 * self.bsize or re.match(r"[*_\W]*[a-z]", b["text"]))):
                    b["role"], b["cont"] = prev["role"] if prev["role"] != "liste" else "gövde", True
                if b["role"] != "sil":
                    prev = b

    def _title_running_heads(self):
        """a margin line that repeats a heading of the book on two or more pages is a running head (the chapter or
        section title printed in the head or foot) even when it is not on 3+ pages or carries no page number"""
        small = lambda b: b.get("edge") and b["size"] <= 1.05 * self.bsize
        heads = {_similar_key(b["text"]) for p in self.pages for b in p["blocks"] if b["role"] in HEAD and not small(b)}
        heads.discard("")
        seen = collections.defaultdict(list)
        for p in self.pages:
            for b in p["blocks"]:
                # a sub-heading at the top of a page keeps its heading role; only text not taken for a heading goes
                if small(b) and b["role"] not in HEAD and b["role"] != "sil" and _similar_key(b["text"]) in heads:
                    seen[_similar_key(b["text"])].append((p, b))
        for hits in seen.values():
            if len({p["p"] for p, _ in hits}) >= 2:
                for p, b in hits:
                    b["role"] = "sil"
                    p["running_removed"] += len(b["lines"])

    def deviates(self, b):
        """set apart from body text: a family/size the book's body styles do not use (italic and bold variants of a
        body style are body text)"""
        return fam_of(b["style"]) not in self.body_fams

    def _object_groups(self):
        """A figure or table owns the short blocks set apart from body text that sit inside its frame or right above or
        below it, block after block within 2.5 body sizes: title, caption, explanation, credit line. They are written
        as captions around the object, never as headings or paragraphs. A heading can be an object's title (above or
        inside it), never the line under it (that is the next section starting); part and chapter headings - the ones a
        table of contents lists - never belong to an object. Body-styled blocks only join when they read like a
        caption ("Figure 3", "Fig. 3", "Şekil 3"). Blocks set apart from body text that join nothing are flagged."""
        self.body_fams = set(self.body_keys)
        # the book's own secondary text styles (notes, references: 5%+ of all words) are set apart from body text but
        # are not anomalies; they are never flagged
        total = sum(s["words"] for s in self.S.values()) or 1
        fam_words = collections.Counter()
        for k, s in self.S.items():
            fam_words[fam_of(k)] += s["words"]
        self.text_fams = self.body_fams | {f for f, w in fam_words.items() if w >= 0.05 * total}
        gap = 2.5 * self.bsize
        front = min(8, max(2, int(0.03 * self.NP)))

        def fits(b, where):
            cap = bool(CAPTION.match(b["text"].replace("*", "")))
            if b["role"] in ("kısım", "bölüm", "liste", "şiir", "epigraf", "alıntı", "atıf", "dizin") or b.get("obj"):
                return False
            if b["role"] in HEAD and where == "below" and not cap:
                return False
            if b["nwords"] > 80 or len(b["lines"]) > 6:
                return False
            return cap or b["deviates"]
        for p in self.pages:
            for b in p["blocks"]:
                b["deviates"] = self.deviates(b)
            if p["p"] <= front or p["cover"] or p["p"] in self.index_pages:
                continue                        # logos and ornaments of the front pages own nothing
            blocks = [b for b in p["blocks"] if b["role"] not in ("sil", "ön sayfa", "dizin")]
            cy = lambda b: (b["top"] + b["bot"]) / 2      # a title may overlap the frame's edge by a few points
            for it in sorted(p.get("items", []), key=lambda it: (round(it["top"]), it["x0"])):
                side = [b for b in blocks if b["x1"] > it["x0"] + 1 and b["x0"] < it["x1"] - 1]
                inside = [b for b in side if it["top"] <= cy(b) <= it["bot"] and fits(b, "inside")]
                mid = (it["top"] + it["bot"]) / 2
                before = [b for b in inside if cy(b) < mid]           # printed in the top half: written before it
                after = [b for b in inside if cy(b) >= mid]
                edge = it["bot"]
                for b in sorted((b for b in side if cy(b) > it["bot"]), key=lambda b: b["top"]):
                    if b["top"] - edge > gap or not fits(b, "below"):
                        break
                    after.append(b)
                    edge = b["bot"]
                edge, above = it["top"], []
                for b in sorted((b for b in side if cy(b) < it["top"]), key=lambda b: -b["bot"]):
                    if edge - b["bot"] > gap or not fits(b, "above"):
                        break
                    above.insert(0, b)
                    edge = b["top"]
                before = above + sorted(before, key=lambda b: (b["top"], b["x0"]))
                for b in before + after:
                    b["role"], b["obj"] = "altyazı", True
                it["before"], it["after"] = before, sorted(after, key=lambda b: (b["top"], b["x0"]))
            for b in p["blocks"]:
                if b["role"] in ("gövde", "kutu") and fam_of(b["style"]) not in self.text_fams and not b.get("obj"):
                    # set apart and joining nothing: in the margin band or a bare number/folio it is page furniture
                    # and goes; anything else stays, flagged for review
                    band = b.get("edge") and (b["bot"] < 0.12 * p["H"] or b["top"] > 0.86 * p["H"])
                    if band or PAGE_NO.match(b["text"]) or re.fullmatch(r"[\W\d_]*", b["text"]):
                        b["role"], b["flag_removed"] = "sil", True
                    else:
                        b["font_dev"] = True            # a deviation reason; _shape_roles names it or flags it

    # -- named block roles from shape: epigraph, block quote, verse, attribution, box -------------------------------
    def _columns(self):
        """the body text column of every page: (left, right) from its body paragraphs (first lines may be indented,
        last lines short, so those are left out); pages without enough body take the median of pages of the same
        parity (facing pages mirror their margins)"""
        col, par = {}, {0: ([], []), 1: ([], [])}
        for p in self.pages:
            xs, rs = [], []
            for b in p["blocks"]:
                if b["role"] == "gövde" and b["style"] in self.body_styles and b["box"] is None and len(b["lines"]) >= 2:
                    xs += [l["x0"] for l in b["lines"][1:]]
                    rs += [l["x1"] for l in b["lines"][:-1]]
            if len(xs) >= 3:
                col[p["p"]] = (statistics.median(xs), statistics.median(rs))
                par[p["p"] % 2][0].append(col[p["p"]][0])
                par[p["p"] % 2][1].append(col[p["p"]][1])
        every = par[0][0] + par[1][0], par[0][1] + par[1][1]
        for p in self.pages:
            if p["p"] not in col:
                L, R = par[p["p"] % 2] if par[p["p"] % 2][0] else every
                col[p["p"]] = (statistics.median(L), statistics.median(R)) if L else (0.12 * p["W"], 0.88 * p["W"])
        return col

    def _geometry(self, b, L, R):
        """indents against the body column, width, alignment and line-length spread of one block"""
        ls, z = b["lines"], b["size"]
        x0s, x1s = [l["x0"] for l in ls], [l["x1"] for l in ls]
        n, colw = len(ls), max(1.0, R - L)
        g = dict(n=n, first=x0s[0] - L, left=(statistics.median(x0s[1:]) if n > 1 else x0s[0]) - L,
                 right=R - (statistics.median(x1s[:-1]) if n > 1 else x1s[0]), width=max(l["x1"] - l["x0"] for l in ls) / colw)
        g["centered"] = all(abs((l["x0"] - L) - (R - l["x1"])) < 0.8 * z and l["x0"] - L > 1.5 * z for l in ls)
        g["flush_right"] = all(abs(R - l["x1"]) < 0.8 * z for l in ls) and x0s[0] - L > 0.3 * colw
        widths = [l["x1"] - l["x0"] for l in ls]
        top = max(x1s)
        g["justified"] = n >= 2 and all(abs(top - x) < 0.8 * z for x in x1s[:-1])
        # spread of the line lengths, a paragraph's short last line left out
        g["spread"] = statistics.pstdev(widths[:-1]) / statistics.mean(widths[:-1]) if n >= 3 else 0.0
        g["hyphenated"] = any(re.search(r"[A-Za-z][\x02-]$", l["plain"].rstrip()) for l in ls[:-1])
        return g

    def _reasons(self, b, g):
        """what sets a block apart from the book's normal paragraph (calibrated on the test books: a body paragraph
        sits flush with the column within 1.2 body sizes, its first line indented by the book's usual amount)"""
        z, pr = self.bsize, self.profile
        r = []
        if b.get("font_dev"):
            r.append("font")
        if g["centered"]:
            r.append("centered")
        elif g["flush_right"]:
            r.append("right")
        else:
            # a body paragraph's lines after the first sit flush (within a point or two); an indented block's do not
            if (g["left"] > 0.6 * z) if g["n"] > 1 else (g["first"] - pr["first"] > 1.2 * z):
                r.append("indent")
            if g["n"] > 1 and (g["right"] > 2.5 * z or (g["right"] > 0.6 * z and g["left"] > 0.6 * z)):
                r.append("indent-right")
        if g["n"] >= 2 and g["width"] < 0.75:
            r.append("narrow")
        if g["n"] >= 3 and g["spread"] > 0.2 and not g["justified"]:
            r.append("ragged")
        if b["italic_share"] > 0.6 and pr["italic"] < 0.3:
            r.append("italic-block")
        if b["size"] < z - 0.6:
            r.append("smaller")
        if g["n"] > 1 and g["left"] - g["first"] > 0.6 * z:
            r.append("hanging")             # a reference or list entry: first line out, the rest indented
        return r

    @staticmethod
    def _nameable(b):
        """hanging-indent entries, list items, references and lists of web addresses are set apart, but never a quote
        or verse"""
        return ("hanging" not in b["why"] and not ITEM.match(b["text"]) and not re.search(r"://|www\.", b["text"])
                and not re.search(r"\(\d{4}[a-z]?\)|\bet al\.|\bdoi\b|\bpp\. \d", b["text"]))

    def _shape_roles(self):
        """Every body-like block is measured against the book's normal paragraph; what deviates is named by rule:
        - attribution: a short line that starts with a dash or sits flush right, right after an epigraph, quote or
          verse (a second flush-right line, the work's title, joins it);
        - epigraph: set apart, right after a heading (chapter or part start), body text following;
        - verse: three or more short lines of irregular length, set apart, not justified;
        - block quote: indented (one or both sides) inside the body flow, body text on both sides;
        - box: the tinted/framed box logic.
        Each named block keeps its reasons in flags and a confidence; a deviation no rule explains keeps the review
        flag ("stil-farkli")."""
        col = self._columns()
        body = [b for p in self.pages for b in p["blocks"] if b["role"] == "gövde" and b["style"] in self.body_styles
                and b["box"] is None]
        firsts = [b["lines"][0]["x0"] - col[b["page"]][0] for b in body if len(b["lines"]) >= 2]
        self.profile = dict(first=statistics.median(firsts) if firsts else 0.0,
                            italic=statistics.median([b["italic_share"] for b in body]) if body else 0.0)
        flow = [(p, b) for p in self.pages for b in p["blocks"]
                if b["role"] not in ("sil", "ön sayfa", "dizin", "altyazı") and not b.get("obj")]
        for p, b in flow:
            if b["role"] in ("gövde", "kutu", "şiir", "epigraf", "atıf"):
                b["geo"] = self._geometry(b, *col[p["p"]])
                b["why"] = self._reasons(b, b["geo"])
        named = ("epigraf", "alıntı", "şiir")
        front = {p["p"] for p in self.pages[:min(8, max(2, int(0.03 * self.NP)))]}
        # epigraphs first: right after a heading of the top two levels (a sub-section's own title starts none), a run
        # of at most four short set-apart blocks (60 words each, 150 in all), an attribution possibly among them, and
        # then the body resumes with a normal paragraph or a sub-heading
        for k, (p, b) in enumerate(flow):
            if b["role"] not in ("kısım", "bölüm", "alt başlık 1"):
                continue
            run, words, j = [], 0, k + 1
            while (j < len(flow) and len(run) < 4 and flow[j][1].get("why") and flow[j][1]["role"] not in HEAD
                   and flow[j][1]["nwords"] <= 60 and self._nameable(flow[j][1])):
                run.append(flow[j][1])
                words += flow[j][1]["nwords"]
                j += 1
            nb = flow[j][1] if j < len(flow) else None
            if run and words <= 150 and nb is not None and ((nb["role"] == "gövde" and nb.get("why") == []) or nb["role"] in HEAD):
                for x in run:
                    if x is not run[0] and len(x["lines"]) <= 3 and x["nwords"] <= 25 and (DASH.match(x["text"]) or "right" in x["why"]):
                        x["role"] = "atıf"      # named in the main pass below, with its title line
                    else:
                        x["role"], x["conf"], x["start"] = "epigraf", 0.9 if {"indent", "italic-block"} & set(x["why"]) else 0.7, True
                        # an epigraph set in lines (a poem): most lines stop well short of the longest one
                        ws = [l["x1"] - l["x0"] for l in x["lines"]]
                        x["breaks"] = len(ws) >= 3 and sum(w < 0.9 * max(ws) for w in ws[:-1]) >= 0.5 * (len(ws) - 1)
        k = 0
        while k < len(flow):
            p, b = flow[k]
            prev = flow[k - 1][1] if k else None
            nxt = flow[k + 1][1] if k + 1 < len(flow) else None
            k += 1
            if b["role"] in ("kutu", "kutu-başlık"):
                b["conf"] = 0.9
                continue
            # a source line set in a heading style ("—A NAME (1988, p. 140)" in small caps) is still the attribution
            if (b["role"] in HEAD and prev is not None and prev["role"] in named and DASH.match(b["text"].strip("* "))
                    and len(b["lines"]) <= 2 and b["nwords"] <= 20):
                b["role"], b["conf"] = "atıf", 0.8
                continue
            if "why" not in b:
                continue
            why, g = b["why"], b["geo"]
            shape = set(why) - {"ragged"}
            short = len(b["lines"]) <= 3 and b["nwords"] <= 25
            # a source line: dash, flush right, a name in parentheses, or a few italic words ("Female, 16-17")
            source = (DASH.match(b["text"]) or "right" in why or re.fullmatch(r"\(.{2,80}\)", b["text"].strip("* "))
                      or (b["nwords"] <= 6 and "italic-block" in why))
            if prev is not None and prev["role"] in named and short and source:
                b["role"], b["conf"] = "atıf", 0.9 if DASH.match(b["text"]) else 0.7
                # the work's title on its own line, right edge on the attribution's, belongs to the attribution
                while (nxt is not None and abs(nxt["x1"] - b["x1"]) < 1.5 and len(nxt["lines"]) <= 2 and nxt["size"] <= b["size"] + 0.6
                       and nxt["page"] == b["page"] and 0 <= nxt["top"] - b["bot"] < 1.5 * b["size"]):
                    b["lines"] += nxt["lines"]
                    _finish_block(b)
                    p["blocks"].remove(nxt)
                    flow.pop(k)
                    nxt = flow[k][1] if k < len(flow) else None
                continue
            if b["role"] == "atıf" and prev is not None and prev["role"] in named:
                b["conf"] = 0.8
                continue
            # a chapter or part start: a heading of the top two levels (a sub-section's own title starts no epigraph),
            # or an epigraph that began there
            if b.get("start"):
                b["flags"] = why
                continue
            # a quote or poem cut by a page break (or by a note number between its parts) goes on at the same left edge
            # and size
            if (prev is not None and prev["role"] in ("alıntı", "şiir") and abs(b["x0"] - prev["x0"]) < 1.5
                    and abs(b["size"] - prev["size"]) < 0.3
                    and (prev["page"] != b["page"] or (not terminal(prev["text"]) and re.match(r"[*_]*[a-z]", b["text"])))):
                b["role"], b["conf"], b["flags"] = prev["role"], prev.get("conf"), why
                continue
            if not self._nameable(b):
                b["flags"] = why + ["stil-farkli"] if b.get("font_dev") else why
                continue
            body_next = nxt is not None and nxt["role"] in ("gövde", "kutu", "liste", "atıf") + tuple(HEAD)
            # a block drawn over another block's lines (stray pieces of a second text layer) is no quote or verse
            over = any(o is not b and o["role"] != "sil" and o["top"] < b["bot"] - 1 and o["bot"] > b["top"] + 1
                       and o["x0"] < b["x1"] - 1 and o["x1"] > b["x0"] + 1 for o in p["blocks"])
            layout = shape & {"indent", "indent-right", "narrow", "centered", "italic-block"}
            # verse: short irregular lines of words set apart by layout (a list of web addresses is not verse)
            # (prose broken over lines splits words at line ends; verse lines end with whole words)
            verse = (len(b["lines"]) >= 3 and "ragged" in why and not g["justified"] and bool(layout) and not g["hyphenated"]
                     and not re.search(r"://|www\.", b["text"]))
            if not over and (verse or (b["role"] in ("şiir", "epigraf") and shape and not g["hyphenated"])):
                # (the style rule names short-lined multi-line blocks şiir/epigraf; away from a chapter start: verse)
                b["role"], b["conf"] = "şiir", 0.9 if b["role"] == "şiir" else 0.7
            elif (not over and ("indent" in why or "indent-right" in why) and "centered" not in why and prev is not None
                  and prev["role"] in ("gövde", "liste", "alıntı") + tuple(HEAD) and body_next):
                b["role"], b["conf"] = "alıntı", 0.8 if "indent" in why and "indent-right" in why else 0.6
            elif b["role"] in ("şiir", "epigraf"):
                b["conf"] = 0.5                 # named by its style only
            elif why:
                # italics or a smaller size alone are emphasis or notes, not a layout to review; nor is the layout of
                # the front pages (title, copyright, dedication, contents are set centered or narrow)
                b["flags"] = why + ["stil-farkli"] if shape - {"smaller", "italic-block"} and p["p"] not in front else why
                continue
            b["flags"] = why

    def _index_pages(self):
        """A back-of-book index: pages in the last quarter of the book whose lines are mostly short entries ending in
        page numbers, in alphabetical order. Its blocks (not its heading) leave the flow."""
        self.index_pages = []
        for p in self.pages:
            if p["p"] < 0.75 * self.NP:
                continue
            ls = [l["plain"].strip() for b in p["blocks"] if not b["sil"] for l in b["lines"]]
            ls = [x for x in ls if x]
            if len(ls) < 15:
                continue
            num = sum(1 for x in ls if re.search(r"\d+(?:[–-]\d+)?[.,;)]?$", x) or re.search(r",\s*\d", x))
            first = [x[0].lower() for x in ls if x[:1].isalpha() and x[:1].isupper()]
            order = sum(1 for a, b in zip(first, first[1:]) if a <= b) / max(1, len(first) - 1)
            if num >= 0.5 * len(ls) and statistics.median(len(x) for x in ls) <= 50 and len(first) >= 5 and order >= 0.7:
                self.index_pages.append(p["p"])
        # the first and last index page can be short: grow the run while the neighbours still look like entries
        def entries(p):
            ls = [l["plain"].strip() for b in p["blocks"] if not b["sil"] for l in b["lines"]]
            ls = [x for x in ls if x]
            return len(ls) >= 3 and statistics.median(len(x) for x in ls) <= 50 and sum(
                1 for x in ls if re.search(r"\d+(?:[–-]\d+)?[.,;)]?$", x) or re.search(r",\s*\d", x)) >= 0.5 * len(ls)
        if self.index_pages:
            lo, hi = self.index_pages[0], self.index_pages[-1]
            while lo > 1 and entries(self.pages[lo - 2]):
                lo -= 1
            while hi < self.NP and entries(self.pages[hi]):
                hi += 1
            self.index_pages = list(range(lo, hi + 1))
        for p in self.pages:
            if p["p"] in self.index_pages:
                for b in p["blocks"]:
                    if b["role"] not in HEAD and b["role"] != "sil":
                        b["role"] = "dizin"

    def _hyphen_lexicon(self):
        """compounds the book writes with a hyphen mid-line; and how often each word occurs, for counting the
        line-end joins that have no evidence either way"""
        lex, words = set(), collections.Counter()
        cut_before = False                      # the previous line, in any block or page, ended in a split word
        for p in self.pages:
            for b in p["blocks"]:
                for l in b["lines"]:
                    t = l["plain"].rstrip()
                    for m in re.finditer(r"[A-Za-z]+(?:-[A-Za-z]+)+", t):
                        lex.add(m.group().lower())
                    toks = re.findall(r"[^\W\d_]+", t)
                    cut = t.endswith("\x02")
                    if cut and toks:
                        toks = toks[:-1]        # "ele" of "ele-/mentary": a piece, not a word
                    if cut_before and toks:
                        toks = toks[1:]         # "mentary"
                    words.update(x.lower() for x in toks)
                    cut_before = cut
        self.words = words
        return lex

    def hyphen_real(self, left, right):
        """A line-end hyphen before a lowercase word is real only when the book writes that compound with a hyphen
        elsewhere ("half-" + "moon-shaped" counts "half-moon" too); otherwise the word is joined."""
        return (left + "-" + right).lower() in self.lex or (left + "-" + right.split("-")[0]).lower() in self.lex

    def hyphen_ambiguous(self, left, right):
        """A join with no evidence in the book: the joined form never occurs, yet both pieces do as words of their own
        ("stress" + "free"). Only counted, for a later review list; the join itself does not change."""
        a, b = left.lower(), right.split("-")[0].lower()
        return (a + b) not in self.words and a in self.words and b in self.words


# ---------------------------------------------------------------------------- markdown writer
CAPTION_OPEN, CAPTION_CLOSE = '::: {custom-style="Caption"}', ":::"
# named block roles -> the segment type segtypes derives from the markdown, and its Word style (Pandoc custom-style)
ROLE_TYPE = {"epigraf": "epigraph", "alıntı": "blockquote", "şiir": "verse", "atıf": "attribution", "kutu": "box",
             "kutu-başlık": "box"}
FENCE = {"epigraf": "Epigraph", "şiir": "Verse", "atıf": "Attribution", "kutu": "Box", "kutu-başlık": "Box"}


def fenced(role, text):
    return '::: {custom-style="%s"}\n%s\n:::' % (FENCE[role], text)


def verse_lines(texts):
    """lines kept as written: a Pandoc hard break (backslash at the line end) after every line but the last"""
    return "\\\n".join(t.replace("\x02", "-") for t in texts)


def terminal(s):
    return bool(re.search(r"[.!?…:;\"”’)\]»][*_]*\s*$", s))


def join_hyphen(left, right, hy, stats, mark="\x02", amb=None):
    """left ends with a line-end hyphen (`mark`, possibly followed by emphasis markers): the joined text, or None.
    A real hyphen stays, a soft one goes. Emphasis that closes before the hyphen and reopens after it is one
    emphasised word ("*Geogra-*" + "*phy*" -> "*Geography*"), so both markers go."""
    m = re.search(r"([A-Za-z]+)" + re.escape(mark) + r"([*_]*)$", left)
    if not m:
        return None
    real_fn = hy if callable(hy) else (lambda a, b: (a + "-" + b).lower() in hy)
    r = re.match(r"([*_]*)([A-Za-z]+(?:-[A-Za-z]+)*)?", right)
    opener, rw = r.group(1), r.group(2) or ""
    first = right[len(opener):][:1]
    real = real_fn(m.group(1), rw) if (rw and first.islower()) else (first.isupper() or first.isdigit())
    stats["hyphen_real" if real else "hyphen_soft"] += 1
    if not real and amb and rw and first.islower() and amb(m.group(1), rw):
        stats["hyphen_ambiguous"] += 1
    closer = m.group(2)
    if closer and closer == opener:
        return left[:m.end(1)] + ("-" if real else "") + right[len(opener):]
    return left[:m.end(1)] + ("-" if real else "") + closer + right


def join_lines(texts, hy, stats, amb=None):
    """Line breaks inside a block. Hyphenation chars arrive as U+0002 (the PDF cannot say soft from real): join,
    keeping the hyphen only when it is a real one. `hy` is Doc.hyphen_real, or a set of hyphenated compounds."""
    out = texts[0]
    for nxt in texts[1:]:
        j = join_hyphen(out, nxt, hy, stats, amb=amb)
        out = j if j is not None else out + " " + nxt
    return out.replace("\x02", "-")


def _style_name(style):
    f, s = style.split("|")
    return f"{f} {s}"


def _meta_of(b=None, page=None, box=None):
    """where a segment comes from: pages, boxes [page, x0, y0, x1, y1] (PDF points, top-left origin), style, flags"""
    if b is not None:
        return dict(pages={b["page_no"]}, boxes=[[b["page_no"]] + [round(v, 1) for v in (b["x0"], b["top"], b["x1"], b["bot"])]],
                    style=_style_name(b["style"]), flags=set(b.get("flags", ())),
                    role=ROLE_TYPE.get(b["role"]) if b.get("conf") else None, conf=b.get("conf"))
    return dict(pages={page}, boxes=[[page] + [round(v, 1) for v in box]], style=None, flags=set(), role=None, conf=None)


def _meta_add(m, other):
    m["pages"] |= other["pages"]
    m["boxes"] += other["boxes"]
    m["flags"] |= other["flags"]
    m["style"] = m["style"] or other["style"]
    m["role"], m["conf"] = m.get("role") or other.get("role"), m.get("conf") or other.get("conf")


def build_markdown(doc, first_idx, last_idx):
    """pages[first_idx..last_idx] (0-based, inclusive) -> markdown, stats. The source of every segment (pages, boxes,
    style, flags) is left in doc.seg_meta, one entry per blank-line-separated segment of the markdown."""
    md, cur = [], None                  # md: (kind, value, meta)
    stats = collections.Counter()
    lex, amb = doc.hyphen_real, doc.hyphen_ambiguous
    pending = []                        # figures/tables met while a paragraph is still open: written after it
    index_done = False

    def captions(blocks):
        """one caption per run of blocks that reads on (no closing punctuation, lowercase start)"""
        runs = []
        for b in blocks:
            text = join_lines([l["md"].strip() for l in b["lines"]], lex, stats, amb)
            if runs and not terminal(runs[-1][0]) and re.match(r"[*_]*[a-z]", text):
                j = join_hyphen(runs[-1][0], text, doc.hyphen_real, stats, mark="-", amb=amb)
                runs[-1][0] = j if j is not None else runs[-1][0] + " " + text
                _meta_add(runs[-1][1], _meta_of(b))
            else:
                runs.append([text, _meta_of(b)])
        for text, m in runs:
            stats["captions"] += 1
            md.append(("raw", CAPTION_OPEN + "\n" + text + "\n" + CAPTION_CLOSE, m))

    def write_item(it):
        p = it["page"]
        captions(it.get("before", []))
        m = _meta_of(page=p, box=(it["x0"], it["top"], it["x1"], it["bot"]))
        if it["kind"] == "image":
            stats["images"] += 1
            md.append(("raw", f"![Görsel · s.{p}](gorsel-{p}-{it['n']})", m))
        elif it["complex"]:
            stats["tables_complex"] += 1
            stats["table_words_out"] += it["words"]
            doc.complex_table_pages.append(p)
            md.append(("raw", f"![Tablo · s.{p}](tablo-{p}-{it['n']})", m))
        else:
            stats["tables"] += 1
            md.append(("raw", it["md"], m))
        captions(it.get("after", []))

    def place(it):
        if cur:
            pending.append(it)
        else:
            write_item(it)

    def flush():
        nonlocal cur
        if cur:
            md.append(("p", cur, cur["meta"]))
            cur = None
        while pending:
            write_item(pending.pop(0))
    doc.complex_table_pages = []
    doc.object_merge_log = []
    for p in doc.pages:
        for b in p["blocks"]:
            b["page_no"] = p["p"]           # a caption above a figure is written before the loop reaches it
    for p in doc.pages[first_idx:last_idx + 1]:
        items = [] if p["p"] in doc.index_pages else sorted(p.get("items", []), key=lambda it: (round(it["top"]), it["x0"]))
        for b in p["blocks"]:
            while items and items[0]["top"] < b["top"] - 1:
                place(items.pop(0))
            role, ls = b["role"], b["lines"]
            if role == "altyazı":
                continue                                    # written with its figure or table
            if role == "dizin":
                stats["index_words"] += b["nwords"]
                if not index_done:
                    flush()
                    pages = [n for n in doc.index_pages if first_idx < n <= last_idx + 1]
                    md.append(("raw", f"![Dizin · s.{pages[0]}–{pages[-1]}](dizin-{pages[0]}-{pages[-1]})",
                               dict(pages=set(pages), boxes=[], style=None, flags=set())))
                    index_done = True
                continue
            if role in ("sil", "ön sayfa"):
                stats["dropped_words"] += b["nwords"]
                stats["flag_removed"] += bool(b.get("flag_removed"))
                continue
            toks_ = [t.strip("*") for t in b["text"].split()]
            if role in ("gövde", "kutu") and (len(re.sub(r"[\s*]", "", b["text"])) <= 2 or (len(toks_) <= 6 and all(len(t) == 1 for t in toks_))):
                stats["dropped_words"] += b["nwords"]       # single stray letters: ornate lettering, not text
                continue
            if "stil-farkli" in b.get("flags", ()):
                stats["style_flags"] += 1
            if b.get("conf") and role in ROLE_TYPE:
                stats["role_" + ROLE_TYPE[role]] += 1
            texts = [(l["md_nobold"] if role in HEAD else l["md"]).strip() for l in ls]
            box_here = b["box"] is not None
            if role in ("gövde", "kutu"):
                text = join_lines(texts, lex, stats, amb)
                last = md[-1] if md and cur is None else None
                # a quote cut by a page break whose rest did not keep the quote's shape (one line, no indent)
                if (last and last[0] == "quote" and not box_here and not terminal(last[1])
                        and re.match(r"[*_]*[a-z]", text)):
                    j = join_hyphen(last[1], text, doc.hyphen_real, stats, mark="-", amb=amb)
                    md[-1] = ("quote", j if j is not None else last[1] + " " + text, last[2])
                    _meta_add(last[2], _meta_of(b))
                    continue
                # a paragraph cut by a page break, or split into two blocks on the same page (around a drop cap, a
                # removed running head, a line set mostly in italics): same family and size (italic/bold variants
                # included), same box, no closing punctuation, lowercase start. With a figure or table in between
                # (pending), the continuation may start with a capital ("... Even among" / "CEOs, the top ...").
                # A heading demoted to the paragraph going on (cont) joins whatever its font.
                if (cur and cur["page"] <= p["p"] and (fam_of(cur["style"]) == fam_of(b["style"]) or b.get("cont"))
                        and cur["box"] == box_here and not terminal(cur["text"])
                        and (re.match(r"[*_]*[a-z]", text) or pending or b.get("cont"))):
                    prev_text = cur["text"]
                    j = join_hyphen(cur["text"], text, doc.hyphen_real, stats, mark="-", amb=amb)
                    cur["text"] = j if j is not None else cur["text"] + " " + text
                    stats["page_merges" if cur["page"] < p["p"] else "inpage_merges"] += 1
                    if pending and not cur.get("joined_over_object"):
                        stats["object_merges"] += 1
                        cur["joined_over_object"] = True
                        doc.object_merge_log.append(dict(page=p["p"], before=prev_text, after=text))   # for review
                    cur["page"] = p["p"]
                    _meta_add(cur["meta"], _meta_of(b))
                    continue
                flush()
                cur = dict(page=p["p"], style=b["style"], text=text, box=box_here, meta=_meta_of(b))
                continue
            flush()
            if role in HEAD:
                t = join_lines([re.sub(r"(?<!\*)\*\*(?!\*)", "", x) for x in texts], lex, stats, amb)
                if len(re.sub(r"[\s*]", "", t)) <= 2:
                    stats["dropped_words"] += b["nwords"]
                    continue
                lvl = HEAD[role]
                prev = md[-1][1] if md and md[-1][0] == "head" else None
                near = prev is not None and prev["page"] == p["p"] and 0 < b["lines"][0]["base"] - prev["base"] < 2.6 * max(b["size"], prev["size"])
                if near and prev["lvl"] == lvl:                       # a heading that wrapped
                    prev["text"] += " " + t
                    prev["base"] = b["lines"][-1]["base"]
                    _meta_add(md[-1][2], _meta_of(b))
                elif near and prev["lvl"] > lvl:                      # "Part One" above "Preparing ...": one heading
                    prev.update(lvl=lvl, text=prev["text"] + " " + t, base=b["lines"][-1]["base"], size=max(prev["size"], b["size"]))
                    _meta_add(md[-1][2], _meta_of(b))
                else:
                    md.append(("head", dict(lvl=lvl, page=p["p"], base=b["lines"][-1]["base"], size=b["size"], text=t), _meta_of(b)))
            elif role == "kutu-başlık":
                md.append(("raw", fenced(role, "**" + re.sub(r"\*+", "", join_lines(texts, lex, stats, amb)) + "**"), _meta_of(b)))
            elif role == "şiir" or (role == "epigraf" and b.get("breaks")):
                md.append(("raw", fenced(role, verse_lines(texts)), _meta_of(b)))
            elif role in ("epigraf", "atıf"):
                md.append(("raw", fenced(role, join_lines(texts, lex, stats, amb)), _meta_of(b)))
            elif role == "alıntı":
                text = join_lines(texts, lex, stats, amb)
                last = md[-1] if md else None
                # a quote cut by a page break goes on in the next page's quote
                if last and last[0] == "quote" and not terminal(last[1]) and re.match(r"[*_]*[a-z]", text):
                    j = join_hyphen(last[1], text, doc.hyphen_real, stats, mark="-", amb=amb)
                    md[-1] = ("quote", j if j is not None else last[1] + " " + text, last[2])
                    _meta_add(last[2], _meta_of(b))
                else:
                    md.append(("quote", text, _meta_of(b)))
            elif role == "liste":
                t = join_lines(texts, lex, stats, amb)
                # the number goes first, outside any emphasis: "**1. Lead-in.** text" -> "1. **Lead-in.** text"
                m = re.match(r"\s*(\**)(\d+\.)\s*(.*)", t, re.S)
                if m:
                    mk, num, rest = m.groups()
                    rest = rest[len(mk):].lstrip() if mk and rest.startswith(mk) else mk + rest
                    t = num + " " + rest
                md.append(("raw", t, _meta_of(b)))
            else:
                md.append(("raw", join_lines(texts, lex, stats, amb), _meta_of(b)))
        for it in items:
            place(it)
    flush()
    out, meta = [], []
    for kind, v, m in md:
        text = (v["text"] if not v.get("box") else fenced("kutu", v["text"])) if kind == "p" else (
            "#" * MD_LEVEL[v["lvl"]] + " " + v["text"] if kind == "head" else "> " + v if kind == "quote" else v)
        if not text.strip():
            continue
        out.append(text)
        meta.append(dict(pages=sorted(m["pages"]), boxes=m["boxes"], style=m["style"], flags=sorted(m["flags"]),
                         role=m.get("role"), conf=m.get("conf")))
    doc.seg_meta = meta
    return "\n\n".join(out) + "\n", stats


# ---------------------------------------------------------------------------- public entry point
def _page_ranges(first_page, last_page, n):
    first = 1 if first_page in (None, "") else int(first_page)
    last = n if last_page in (None, "") else int(last_page)
    if first < 1 or last < first or first > n:
        raise PdfImportError(f"Sayfa aralığı geçersiz: {first}–{last} (PDF {n} sayfa).")
    return first, min(last, n)


def _pages_text(pages, first_idx, last_idx):
    return sum(len(b["text"].strip()) for p in pages[first_idx:last_idx + 1] for b in p["blocks"])


def is_scanned(producer, signals):
    """A scanned book with an OCR text layer: made by OCR software, or most text-bearing pages are a full-page image
    under invisible text. The text of such a layer can be wrong in ways the importer cannot repair (pdfium drops the
    last letter of most words in some ABBYY layers)."""
    text_pages = [s for s in signals if s[1] + s[2]]
    scan_pages = [s for s in text_pages if s[0] and s[1] and s[1] >= 9 * s[2]]
    return bool(OCR_PRODUCER.search(producer or "")) or (bool(text_pages) and len(scan_pages) >= 0.5 * len(text_pages))


def _analyse(pdf_bytes, first_page=None, last_page=None):
    """convert() with its internals: dict(md, warnings, doc, stats, tint, orn, info)"""
    try:
        doc = pdfium.PdfDocument(pdf_bytes)
    except pdfium.PdfiumError as e:
        msg = str(e)
        if "password" in msg.lower():
            raise PdfImportError("Bu PDF parola korumalı. Parolayı kaldırıp (ya da parolasız bir kopya kaydedip) tekrar dene.")
        raise PdfImportError(f"PDF açılamadı: {msg}")
    n = len(doc)
    if n == 0:
        raise PdfImportError("PDF'te hiç sayfa yok.")
    first, last = _page_ranges(first_page, last_page, n)
    # pass 1: characters of every page; ornament fonts (a handful of dingbat glyphs in the whole book) are known only now
    raw_pages = []
    font_count = collections.Counter()
    signals = []
    for i in range(n):
        page = doc[i]
        chars = _page_chars(page)
        for c in chars:
            if c.ch.strip():
                font_count[c.font] += 1
        gfx = _page_graphics(page)
        raw_pages.append((page.get_width(), page.get_height(), chars, _tint_boxes(page), gfx))
        signals.append(gfx["signals"])
    total_chars = sum(len(rp[2]) for rp in raw_pages[first - 1:last])
    if total_chars < 20 * (last - first + 1) and total_chars < 400:
        raise PdfImportError("Bu PDF'te (seçilen sayfalarda) metin katmanı yok ya da çok az. Taranmış bir PDF olabilir; "
                             "önce OCR ile metin katmanı eklenmeli.")
    orn = frozenset(f for f, c in font_count.items() if c < 30 and ORNAMENT_FONT.search(f))
    tint = {i + 1: rp[3] for i, rp in enumerate(raw_pages)}
    pages = []
    for i, (W, H, chars, boxes, gfx) in enumerate(raw_pages):
        pages.append(build_blocks(i + 1, W, H, chars, boxes, orn, gfx["images"], gfx["hl"], gfx["vl"]))
    raw_pages = None
    d = Doc(pages)
    md, stats = build_markdown(d, first - 1, last - 1)
    if len(md.split()) < 20:
        raise PdfImportError("Seçilen sayfalardan okunabilir metin çıkmadı (yalnızca filigran/üst-alt bilgi olabilir). "
                             "Sayfa aralığını kontrol et.")
    meta = doc.get_metadata_dict()
    producer = " ".join(v for v in (meta.get("Producer"), meta.get("Creator")) if v)
    scanned = is_scanned(producer, signals)
    rotated = sum(p.get("rotated", 0) for p in d.pages[first - 1:last])
    warnings = [SCANNED_WARNING] if scanned else []
    two = [p["p"] for p in d.pages[first - 1:last] if p["two_col"] and p["p"] not in d.index_pages]
    if two:
        warnings.append("İki sütunlu görünen sayfalar: " + _fmt_pages(two) + " (önce sol, sonra sağ sütun yazıldı; metni kontrol et).")
    if stats["page_merges"]:
        warnings.append(f"{stats['page_merges']} paragraf sayfa geçişinde birleştirildi.")
    if stats["hyphen_real"]:
        warnings.append(f"{stats['hyphen_real']} satır sonu tiresi gerçek tire sayıldı (kitapta aynı sözcük tireli de geçiyor); "
                        f"{stats['hyphen_soft']} tanesi hece bölme sayılıp birleştirildi.")
    if stats["hyphen_ambiguous"]:
        warnings.append(f"{stats['hyphen_ambiguous']} belirsiz tire birleştirildi.")
    if stats["images"]:
        fig_words = sum(p.get("fig_words", 0) for p in d.pages[first - 1:last])
        warnings.append(f"{stats['images']} görsel yer tutucuyla işaretlendi"
                        + (f"; görsellerin içindeki {fig_words} sözcük metne alınmadı." if fig_words else "."))
    if stats["captions"]:
        warnings.append(f"Görsel ve tablolara {stats['captions']} başlık/altyazı/açıklama satırı bağlandı (altyazı olarak yazıldı).")
    if stats["object_merges"]:
        warnings.append(f"{stats['object_merges']} paragraf araya giren görsel/tablo yüzünden bölünmüştü; birleştirildi, "
                        "görsel/tablo paragrafın ardına alındı.")
    if stats["tables"]:
        warnings.append(f"{stats['tables']} tablo markdown tablosu olarak yazıldı; çeviride satır ve sütunlar korunmalı.")
    if stats["tables_complex"]:
        warnings.append(f"{stats['tables_complex']} karmaşık (birleşik hücreli) tablo görsel gibi işaretlendi, çevrilmeyecek: s. "
                        + _fmt_pages(sorted(set(d.complex_table_pages))) + ".")
    idx = [n for n in d.index_pages if first <= n <= last]
    if idx:
        warnings.append(f"Dizin (s. {idx[0]}–{idx[-1]}) çeviriye dahil edilmedi.")
    running = sum(p.get("running_removed", 0) for p in d.pages[first - 1:last])
    if running:
        warnings.append(f"{running} üst/alt bilgi satırı (sayfa üstü/altı başlık, künye, filigran) metinden çıkarıldı.")
    if stats["flag_removed"]:
        warnings.append(f"Kenarda duran ya da yalnızca numara olan {stats['flag_removed']} farklı stilli blok çıkarıldı.")
    if stats["style_flags"]:
        warnings.append(f"{stats['style_flags']} blok gövdeden farklı stilde ama hiçbir başlığa ya da görsel/tabloya "
                        "bağlanamadı; \"stil-farkli\" olarak gözden geçirmeye işaretlendi.")
    if rotated >= 20:
        warnings.append(f"Eğik ya da dikey yazılmış {rotated} karakter (filigran, kenar yazısı) metne alınmadı.")
    skipped = [p["p"] for p in d.pages[first - 1:last] if p["cover"]]
    if skipped:
        warnings.append("Kapak/açık yayılım sayfası atlandı: " + _fmt_pages(skipped) + ".")
    return dict(md=md, warnings=warnings, doc=d, stats=stats, tint=tint, orn=orn, meta=d.seg_meta,
                info=dict(scanned=scanned, producer=producer, rotated_chars=rotated,
                          scan_pages=sum(1 for s in signals if s[0] and s[1] and s[1] >= 9 * s[2])))


def convert(pdf_bytes, first_page=None, last_page=None):
    a = _analyse(pdf_bytes, first_page, last_page)
    return a["md"], a["warnings"]


def convert_with_meta(pdf_bytes, first_page=None, last_page=None):
    """convert() plus the source of every segment (see build_markdown): (markdown, warnings, seg_meta)"""
    a = _analyse(pdf_bytes, first_page, last_page)
    return a["md"], a["warnings"], a["meta"]


def _fmt_pages(nums):
    """[3,4,5,9] -> '3–5, 9'"""
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}–{nums[j]}")
        i = j + 1
    return ", ".join(out)
