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
         "şiir", "epigraf", "atıf", "liste", "ön sayfa", "sil"]
HEAD = {"kısım": 1, "bölüm": 2, "alt başlık 1": 3, "alt başlık 2": 4}   # internal depth
MD_LEVEL = {1: 1, 2: 1, 3: 2, 4: 3}                                    # '#' count written to the markdown
NUM = re.compile(r"^\s*\d+\.\s")
DASH = re.compile(r"^\s*[—–―]")
ORNAMENT_FONT = re.compile(r"dingbat|symbol|ornament|wingding|fleuron|picto", re.I)
PAGE_NO = re.compile(r"^[\s—–\-·•|]*(\d{1,4}|[ivxlcdm]{1,7})[\s—–\-·•|]*$", re.I)
OCR_PRODUCER = re.compile(r"abbyy|finereader|tesseract|omnipage|readiris|paper capture|clearscan|\bocr\b", re.I)
SCANNED_WARNING = "Bu PDF taranmış görünüyor, metin güvenilir olmayabilir."

# ---------------------------------------------------------------------------- characters
class Ch:
    __slots__ = ("idx", "ch", "ox", "lr", "x0", "x1", "top", "bot", "base", "font", "size", "weight",
                 "italic", "bold", "dropcap", "rot")

    def __init__(self, ch, x0, x1, top, bot, base, font, size, weight=400, italic=False, bold=False,
                 idx=0, ox=None, lr=None, rot=False):
        self.idx, self.ch, self.x0, self.x1, self.top, self.bot, self.base = idx, ch, x0, x1, top, bot, base
        self.font, self.size, self.weight, self.italic, self.bold = font, size, weight, italic, bold
        self.ox = x0 if ox is None else ox          # glyph origin
        self.lr = x1 if lr is None else lr          # end of the advance box (loose box)
        self.dropcap = False
        self.rot = rot                              # set at an angle (diagonal watermark, vertical edge tab)


def size_key(font, size):
    return f"{font}|{round(size * 2) / 2:g}"


def fam_of(style):
    f, s = style.split("|")
    return re.sub(r"-(BoldItalic|LightItalic|RegularItalic|Italic|Bold|Regular|Light|Medium|Semibold|ExtraBold|Roman|SmallCaps)$", "", f), float(s)


def _page_chars(page):
    """per-character records, top-left origin; effective size = FontSize * text-matrix scale"""
    H = page.get_height()
    tpg = page.get_textpage()
    tp = tpg.raw
    n = tpg.count_chars()
    l, b, r, t = (ctypes.c_double() for _ in range(4))
    ox, oy = ctypes.c_double(), ctypes.c_double()
    m, loose = raw.FS_MATRIX(), raw.FS_RECTF()
    buf, fl = ctypes.create_string_buffer(96), ctypes.c_int()
    out = []
    for i in range(n):
        if raw.FPDFText_IsGenerated(tp, i):
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
        rot = abs(m.b) + abs(m.c) > 0.1 * (abs(m.a) + abs(m.d))
        out.append(Ch(chr(u), l.value, r.value, H - t.value, H - b.value, H - oy.value, fn,
                      raw.FPDFText_GetFontSize(tp, i) * (math.hypot(m.c, m.d) or 1.0), w,
                      bool(fl.value & 0x40) or bool(re.search(r"italic|oblique", fn, re.I)),
                      w >= 600 or bool(re.search(r"bold|black|heavy", fn, re.I)),
                      idx=i, ox=ox.value, lr=loose.right, rot=rot))
    return out


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


# ---------------------------------------------------------------------------- rows, lines
def build_lines(chars, page_no, orn_fonts=frozenset()):
    """chars -> rows (baseline clustering, content-stream order inside a row) -> segments split at big gaps /
    backward jumps. Small super/subscript rows fold into the row they overlap; drop caps attach to the
    first line they sit next to."""
    chars = [c for c in chars if c.font not in orn_fonts]
    body = statistics.median([c.size for c in chars if c.ch.strip()] or [10.0])
    caps = [c for c in chars if c.size >= 2.5 * body and c.ch.isalpha() and c.size < 40]
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
            l = min(cands, key=lambda l: l["x0"])
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
    Other lines: real ' ' chars are honoured; extra spaces are inferred from the advance gap: 0.5 em for ALL-CAPS
    with real spaces, 0.2 em otherwise."""
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
    has_space = any(c.ch == " " for c in cs)
    letters = [c for c in cs if c.ch.isalpha()]
    caps = sum(c.ch.isupper() for c in letters) / max(1, len(letters))
    if has_space and caps > 0.8:
        thr = 0.5 * size
    else:
        thr = 0.2 * size
    toks, prev = [], None
    for c in cs:
        if prev is not None and c.ch != " " and prev.ch != " " and not prev.dropcap and c.ox - prev.lr > thr:
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
    del l["chars"]                      # keep memory flat on long books


def two_column_suspect(lines, W):
    left = [l for l in lines if l["x0"] < 0.4 * W]
    right = [l for l in lines if l["x0"] > 0.5 * W and l["x1"] <= W + 1]
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


def build_blocks(page_no, W, H, chars, boxes, orn_fonts=frozenset()):
    # text outside the page box (facing-page copies in spreads) is not part of this page
    chars = [c for c in chars if -2 <= (c.x0 + c.x1) / 2 <= W + 2 and -2 <= (c.top + c.bot) / 2 <= H + 2]
    # text set at an angle is a watermark or an edge tab, never part of the reading flow
    rotated = sum(1 for c in chars if c.rot and c.ch.strip())
    chars = [c for c in chars if not c.rot]
    lines = build_lines(chars, page_no, orn_fonts)
    for l in lines:
        _prepare_line(l)
        l["box"] = _in_box(l, boxes)
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
        return dict(lines=[l], style=l["style"], box=l["box"], page=page_no, list=bool(NUM.match(l["plain"])))
    for l in lines:
        if cur is None:
            cur = new(l)
            continue
        a = cur["lines"][-1]
        gap = l["base"] - a["base"]
        ref = pit.get(l["style"], 1.3 * l["size"])
        mg = margin[(l["style"], l["box"] is not None)]
        if l["style"] != cur["style"] or l["box"] != cur["box"]:
            brk = True
        elif gap > 1.45 * ref or (gap < 0.3 * l["size"] and l["x0"] < a["x1"]):
            brk = True
        elif NUM.match(l["plain"]) and len(l["plain"].strip()) > 3:
            brk = True
        elif l["x0"] - mg > 0.7 * l["size"] and not (cur["list"] and l["x0"] >= cur["lines"][0]["x0"] - 1):
            brk = True
        else:
            brk = False
        if brk:
            blocks.append(cur)
            cur = new(l)
        else:
            cur["lines"].append(l)
    if cur:
        blocks.append(cur)
    for b in blocks:
        _finish_block(b)
    return dict(p=page_no, W=W, H=H, blocks=blocks, boxes=boxes, rotated=rotated)


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
        self.lex = self._hyphen_lexicon()

    # -- margin text: running heads/feet, page numbers, slug, watermark ------------------------------
    def _mark_cover_and_repeats(self):
        """The margin is what lies above or below the book's text block: the median top of the first and bottom of
        the last body-size line over all pages (a fixed share of the page height misses books whose text block sits
        in the middle of a large page). In the margin a lone number is a page number, and a line whose letters
        (spaces, digits and punctuation ignored, so letter-spaced heads compare too) recur on 3+ pages and is not
        larger than body text is a running head/foot or slug. In the outer bands of the page (top 9%, bottom 14%), a
        style at the same spot on 35%+ of the pages is a watermark or footer whatever its text."""
        words = collections.Counter()
        for p in self.pages:
            for b in p["blocks"]:
                for l in b["lines"]:
                    words[l["size"]] += len(l["plain"].split())
        bsize = max(words, key=words.get) if words else 10.0
        tops, bots = [], []
        for p in self.pages:
            bl = [l for b in p["blocks"] for l in b["lines"] if abs(l["size"] - bsize) <= 0.6 and len(l["plain"].split()) >= 3]
            if len(bl) >= 3:
                tops.append(min(l["top"] for l in bl))
                bots.append(max(l["bot"] for l in bl))
        self.text_top = statistics.median(tops) if tops else None
        self.text_bot = statistics.median(bots) if bots else None

        def zone(p, l):
            if self.text_top is None:
                return "top" if l["top"] < 0.09 * p["H"] else "bot" if l["top"] > 0.86 * p["H"] else None
            return "top" if l["bot"] <= self.text_top - 0.5 else "bot" if l["top"] >= self.text_bot + 0.5 else None
        rep, pos = collections.defaultdict(set), collections.defaultdict(set)
        for p in self.pages:
            for b in p["blocks"]:
                for l in b["lines"]:
                    l["zone"] = zone(p, l)
                    l["key"] = "".join(ch for ch in l["plain"].lower() if ch.isalpha())
                    if l["zone"] and l["key"]:
                        rep[(l["zone"], l["key"])].add(p["p"])
                    pos[(l["style"], round(l["top"] / 4))].add(p["p"])
        need = max(4, 0.35 * self.NP)
        for p in self.pages:
            p["cover"] = bool(self.W0) and abs(p["W"] - self.W0) > 0.2 * self.W0
            for b in p["blocks"]:
                for l in b["lines"]:
                    band = l["top"] < 0.09 * p["H"] or l["top"] > 0.86 * p["H"]
                    l["sil"] = bool(
                        (l["zone"] and PAGE_NO.match(l["plain"]))
                        or (l["zone"] and l["key"] and len(rep[(l["zone"], l["key"])]) >= 3 and l["size"] <= 1.05 * bsize)
                        or (band and len(pos[(l["style"], round(l["top"] / 4))]) >= need))
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
        # be removed, not a heading
        big_enough = lambda k: fam_of(k)[1] >= 0.6 * bsize
        marginal = lambda k: S[k]["edge"] / S[k]["blocks"] >= 0.5 and fam_of(k)[1] <= 1.05 * bsize
        cands = [k for k, s in S.items() if k not in sugg and s["blocks"] >= 5 and len(s["pages"]) >= 3
                 and s["chars"] / s["blocks"] >= 4 and s["short"] / s["blocks"] >= 0.9 and s["words"] / s["blocks"] <= 12
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
        for p in self.pages:
            prev = None
            real = [b for b in p["blocks"] if not b["sil"]]
            for b in p["blocks"]:
                r = "sil" if b["sil"] else sugg.get(b["style"], ("gövde",))[0]
                if p["cover"]:
                    r = "ön sayfa"
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

    def _hyphen_lexicon(self):
        """compounds the book writes with a hyphen mid-line; and how often each word occurs, for counting the
        line-end joins that have no evidence either way"""
        lex, words = set(), collections.Counter()
        for p in self.pages:
            for b in p["blocks"]:
                for l in b["lines"]:
                    t = l["plain"].rstrip()
                    for m in re.finditer(r"[A-Za-z]+(?:-[A-Za-z]+)+", t):
                        lex.add(m.group().lower())
                    words.update(x.lower() for x in re.findall(r"[^\W\d_]+", t))
        self.words = words
        return lex

    def hyphen_real(self, left, right):
        """A line-end hyphen before a lowercase word is real only when the book writes that compound with a hyphen
        elsewhere; otherwise the word is joined."""
        return (left + "-" + right).lower() in self.lex

    def hyphen_ambiguous(self, left, right):
        """A join with no evidence in the book: the joined form never occurs, yet both pieces do as words of their own
        ("stress" + "free"). Only counted, for a later review list; the join itself does not change."""
        a, b = left.lower(), right.split("-")[0].lower()
        return (a + b) not in self.words and a in self.words and b in self.words


# ---------------------------------------------------------------------------- markdown writer
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


def build_markdown(doc, first_idx, last_idx):
    """pages[first_idx..last_idx] (0-based, inclusive) -> markdown, stats"""
    md, cur = [], None
    stats = collections.Counter()
    lex, amb = doc.hyphen_real, doc.hyphen_ambiguous

    def flush():
        nonlocal cur
        if cur:
            md.append(("p", cur))
            cur = None
    for p in doc.pages[first_idx:last_idx + 1]:
        for b in p["blocks"]:
            role, ls = b["role"], b["lines"]
            if role in ("sil", "ön sayfa"):
                stats["dropped_words"] += b["nwords"]
                continue
            toks_ = [t.strip("*") for t in b["text"].split()]
            if role in ("gövde", "kutu") and (len(re.sub(r"[\s*]", "", b["text"])) <= 2 or (len(toks_) <= 6 and all(len(t) == 1 for t in toks_))):
                stats["dropped_words"] += b["nwords"]       # single stray letters: ornate lettering, not text
                continue
            texts = [(l["md_nobold"] if role in HEAD else l["md"]).strip() for l in ls]
            box_here = b["box"] is not None
            if role in ("gövde", "kutu"):
                text = join_lines(texts, lex, stats, amb)
                # a paragraph cut by a page break, or split into two blocks on the same page (around a drop cap, a
                # removed running head, a figure, a line set mostly in italics): same family and size (italic/bold
                # variants included), same box, no closing punctuation, lowercase start
                if (cur and cur["page"] <= p["p"] and fam_of(cur["style"]) == fam_of(b["style"]) and cur["box"] == box_here
                        and not terminal(cur["text"]) and re.match(r"[*_]*[a-z]", text)):
                    j = join_hyphen(cur["text"], text, doc.hyphen_real, stats, mark="-", amb=amb)
                    cur["text"] = j if j is not None else cur["text"] + " " + text
                    stats["page_merges" if cur["page"] < p["p"] else "inpage_merges"] += 1
                    cur["page"] = p["p"]
                    continue
                flush()
                cur = dict(page=p["p"], style=b["style"], text=text, box=box_here)
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
                elif near and prev["lvl"] > lvl:                      # "Part One" above "Preparing ...": one heading
                    prev.update(lvl=lvl, text=prev["text"] + " " + t, base=b["lines"][-1]["base"], size=max(prev["size"], b["size"]))
                else:
                    md.append(("head", dict(lvl=lvl, page=p["p"], base=b["lines"][-1]["base"], size=b["size"], text=t)))
            elif role == "kutu-başlık":
                md.append(("raw", "**" + re.sub(r"\*+", "", join_lines(texts, lex, stats, amb)) + "**"))
            elif role in ("şiir", "epigraf"):
                md.append(("raw", "\n".join(t.replace("\x02", "-") + ("  " if i < len(texts) - 1 else "") for i, t in enumerate(texts))))
            elif role == "atıf":
                md.append(("raw", "**" + re.sub(r"\*+", "", join_lines(texts, lex, stats, amb)) + "**"))
            elif role == "liste":
                t = join_lines(texts, lex, stats, amb)
                m = re.match(r"\s*\**(\d+\.)\**\s*(.*)", t, re.S)
                md.append(("raw", (m.group(1) + " " + m.group(2)) if m else t))
            else:
                md.append(("raw", join_lines(texts, lex, stats, amb)))
    flush()
    out = []
    for kind, v in md:
        out.append(v["text"] if kind == "p" else ("#" * MD_LEVEL[v["lvl"]] + " " + v["text"] if kind == "head" else v))
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


def _scan_signals(page):
    """(an image covers 80%+ of the page, text objects drawn invisible, text objects drawn visible)"""
    W, H = page.get_width(), page.get_height()
    big, inv, vis = False, 0, 0
    for o in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE, raw.FPDF_PAGEOBJ_TEXT]):
        if o.type == raw.FPDF_PAGEOBJ_IMAGE:
            l, b, r, t = o.get_bounds()
            big = big or (r - l) * (t - b) >= 0.8 * W * H
        elif raw.FPDFTextObj_GetTextRenderMode(o.raw) == raw.FPDF_TEXTRENDERMODE_INVISIBLE:
            inv += 1
        else:
            vis += 1
    return big, inv, vis


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
        raw_pages.append((page.get_width(), page.get_height(), chars, _tint_boxes(page)))
        signals.append(_scan_signals(page))
    total_chars = sum(len(c) for _, _, c, _ in raw_pages[first - 1:last])
    if total_chars < 20 * (last - first + 1) and total_chars < 400:
        raise PdfImportError("Bu PDF'te (seçilen sayfalarda) metin katmanı yok ya da çok az. Taranmış bir PDF olabilir; "
                             "önce OCR ile metin katmanı eklenmeli.")
    orn = frozenset(f for f, c in font_count.items() if c < 30 and ORNAMENT_FONT.search(f))
    tint = {i + 1: rp[3] for i, rp in enumerate(raw_pages)}
    pages = []
    for i, (W, H, chars, boxes) in enumerate(raw_pages):
        pages.append(build_blocks(i + 1, W, H, chars, boxes, orn))
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
    two = [p["p"] for p in d.pages[first - 1:last] if p["two_col"]]
    if two:
        warnings.append("İki sütun şüphesi olan sayfalar: " + _fmt_pages(two) + " (sütun sırası düzeltilmedi, metni kontrol et).")
    if stats["page_merges"]:
        warnings.append(f"{stats['page_merges']} paragraf sayfa geçişinde birleştirildi.")
    if stats["hyphen_real"]:
        warnings.append(f"{stats['hyphen_real']} satır sonu tiresi gerçek tire sayıldı (kitapta aynı sözcük tireli de geçiyor); "
                        f"{stats['hyphen_soft']} tanesi hece bölme sayılıp birleştirildi.")
    if stats["hyphen_ambiguous"]:
        warnings.append(f"{stats['hyphen_ambiguous']} belirsiz tire birleştirildi.")
    if rotated >= 20:
        warnings.append(f"Eğik ya da dikey yazılmış {rotated} karakter (filigran, kenar yazısı) metne alınmadı.")
    skipped = [p["p"] for p in d.pages[first - 1:last] if p["cover"]]
    if skipped:
        warnings.append("Kapak/açık yayılım sayfası atlandı: " + _fmt_pages(skipped) + ".")
    return dict(md=md, warnings=warnings, doc=d, stats=stats, tint=tint, orn=orn,
                info=dict(scanned=scanned, producer=producer, rotated_chars=rotated,
                          scan_pages=sum(1 for s in signals if s[0] and s[1] and s[1] >= 9 * s[2])))


def convert(pdf_bytes, first_page=None, last_page=None):
    a = _analyse(pdf_bytes, first_page, last_page)
    return a["md"], a["warnings"]


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
