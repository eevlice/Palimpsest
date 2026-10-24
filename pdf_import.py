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


# ---------------------------------------------------------------------------- characters
class Ch:
    __slots__ = ("idx", "ch", "ox", "lr", "x0", "x1", "top", "bot", "base", "font", "size", "weight",
                 "italic", "bold", "dropcap")

    def __init__(self, ch, x0, x1, top, bot, base, font, size, weight=400, italic=False, bold=False,
                 idx=0, ox=None, lr=None):
        self.idx, self.ch, self.x0, self.x1, self.top, self.bot, self.base = idx, ch, x0, x1, top, bot, base
        self.font, self.size, self.weight, self.italic, self.bold = font, size, weight, italic, bold
        self.ox = x0 if ox is None else ox          # glyph origin
        self.lr = x1 if lr is None else lr          # end of the advance box (loose box)
        self.dropcap = False


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
        out.append(Ch(chr(u), l.value, r.value, H - t.value, H - b.value, H - oy.value, fn,
                      raw.FPDFText_GetFontSize(tp, i) * (abs(m.d) or 1.0), w,
                      bool(fl.value & 0x40) or bool(re.search(r"italic|oblique", fn, re.I)),
                      w >= 600 or bool(re.search(r"bold|black|heavy", fn, re.I)),
                      idx=i, ox=ox.value, lr=loose.right))
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
            fwd, back = b.x0 - a.x1, a.x1 - b.x0
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


def line_tokens(line):
    """(char, italic, bold) tokens with inferred word spaces. Real ' ' chars are honoured; extra spaces are
    inferred from the advance gap: tracked lettering 1.7x the median gap, tracked ALL-CAPS 0.5 em,
    ordinary text 0.2 em."""
    cs = line["chars"]
    has_space = any(c.ch == " " for c in cs)
    letters = [c for c in cs if c.ch.isalpha()]
    caps = sum(c.ch.isupper() for c in letters) / max(1, len(letters))
    gaps = sorted(b.ox - a.lr for a, b in zip(cs, cs[1:]) if a.ch != " " and b.ch != " ")
    med = gaps[len(gaps) // 2] if gaps else 0.0
    size = line["size"]
    if len(cs) >= 6 and med > 0.3 * size:
        thr = 1.7 * med
    elif has_space and caps > 0.8:
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


def build_blocks(page_no, W, H, chars, boxes, orn_fonts=frozenset()):
    # text outside the page box (facing-page copies in spreads) is not part of this page
    chars = [c for c in chars if -2 <= (c.x0 + c.x1) / 2 <= W + 2 and -2 <= (c.top + c.bot) / 2 <= H + 2]
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
        ls = b["lines"]
        b["top"], b["bot"] = min(l["top"] for l in ls), max(l["bot"] for l in ls)
        b["x0"], b["x1"] = min(l["x0"] for l in ls), max(l["x1"] for l in ls)
        b["size"] = ls[0]["size"]
        b["nwords"] = sum(len(l["plain"].split()) for l in ls)
        tot = max(1, sum(len(l["plain"]) for l in ls))
        b["italic_share"] = sum(l["italic_share"] * len(l["plain"]) for l in ls) / tot
        b["text"] = " ".join(l["plain"] for l in ls)
    return dict(p=page_no, W=W, H=H, blocks=blocks, boxes=boxes)


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

    # -- repeated text at the same place on many pages (watermark, slug, running footer) ------------
    def _mark_cover_and_repeats(self):
        rep = collections.defaultdict(set)
        for p in self.pages:
            for b in p["blocks"]:
                for l in b["lines"]:
                    rep[(l["style"], round(l["top"] / 4))].add(p["p"])
        need = max(4, 0.35 * self.NP)
        for p in self.pages:
            p["cover"] = bool(self.W0) and abs(p["W"] - self.W0) > 0.2 * self.W0
            for b in p["blocks"]:
                def rp(l):
                    return (len(rep[(l["style"], round(l["top"] / 4))]) >= need
                            and (l["top"] < 0.09 * p["H"] or l["top"] > 0.86 * p["H"]))
                b["sil"] = all(rp(l) for l in b["lines"])

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
                                                  multiline=0, mlines=0, sent=0, wts=[]))
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
        cands = [k for k, s in S.items() if k not in sugg and s["blocks"] >= 5 and len(s["pages"]) >= 3
                 and s["chars"] / s["blocks"] >= 4 and s["short"] / s["blocks"] >= 0.9 and s["words"] / s["blocks"] <= 12
                 and s["sent"] / s["blocks"] < 0.5 and emph(k) and s["inbox"] / s["blocks"] < 0.5
                 and s["words"] >= 8 and s["num"] / s["blocks"] < 0.3]
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
        lex = set()
        for p in self.pages:
            for b in p["blocks"]:
                for l in b["lines"]:
                    for m in re.finditer(r"[A-Za-z]+(?:-[A-Za-z]+)+", l["plain"].rstrip()):
                        lex.add(m.group().lower())
        return lex


# ---------------------------------------------------------------------------- markdown writer
def terminal(s):
    return bool(re.search(r"[.!?…:;\"”’)\]»][*_]*\s*$", s))


def join_lines(texts, lex, stats):
    """Line breaks inside a block. Hyphenation chars arrive as U+0002 (the PDF cannot say soft from real): join,
    keeping the hyphen only for a compound the book itself writes hyphenated elsewhere ('real')."""
    out = texts[0]
    for nxt in texts[1:]:
        m = re.search(r"([A-Za-z]+)\x02[*_]*$", out)
        if m:
            right = re.match(r"[*_]*([A-Za-z]+(?:-[A-Za-z]+)*)", nxt)
            rw = right.group(1) if right else ""
            real = ((m.group(1) + "-" + rw).lower() in lex) if (rw and nxt[:1].islower()) else (nxt[:1].isupper() or nxt[:1].isdigit())
            stats["hyphen_real" if real else "hyphen_soft"] += 1
            out = re.sub(r"\x02([*_]*)$", ("-" if real else "") + r"\1", out) + nxt
        else:
            out = out + " " + nxt
    return out.replace("\x02", "-")


def build_markdown(doc, first_idx, last_idx):
    """pages[first_idx..last_idx] (0-based, inclusive) -> markdown, stats"""
    md, cur = [], None
    stats = collections.Counter()
    lex = doc.lex

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
                text = join_lines(texts, lex, stats)
                if (cur and cur["page"] < p["p"] and cur["style"] == b["style"] and cur["box"] == box_here
                        and not terminal(cur["text"]) and re.match(r"[*_]*[a-z]", text)):
                    m = re.search(r"([A-Za-z]+)-$", cur["text"])
                    if m:
                        rw = re.match(r"[*_]*([A-Za-z]+(?:-[A-Za-z]+)*)", text).group(1)
                        real = (m.group(1) + "-" + rw).lower() in lex
                        stats["hyphen_real" if real else "hyphen_soft"] += 1
                        cur["text"] = cur["text"] + text if real else cur["text"][:-1] + text
                    else:
                        cur["text"] += " " + text
                    stats["page_merges"] += 1
                    cur["page"] = p["p"]
                    continue
                flush()
                cur = dict(page=p["p"], style=b["style"], text=text, box=box_here)
                continue
            flush()
            if role in HEAD:
                t = join_lines([re.sub(r"(?<!\*)\*\*(?!\*)", "", x) for x in texts], lex, stats)
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
                md.append(("raw", "**" + re.sub(r"\*+", "", join_lines(texts, lex, stats)) + "**"))
            elif role in ("şiir", "epigraf"):
                md.append(("raw", "\n".join(t.replace("\x02", "-") + ("  " if i < len(texts) - 1 else "") for i, t in enumerate(texts))))
            elif role == "atıf":
                md.append(("raw", "**" + re.sub(r"\*+", "", join_lines(texts, lex, stats)) + "**"))
            elif role == "liste":
                t = join_lines(texts, lex, stats)
                m = re.match(r"\s*\**(\d+\.)\**\s*(.*)", t, re.S)
                md.append(("raw", (m.group(1) + " " + m.group(2)) if m else t))
            else:
                md.append(("raw", join_lines(texts, lex, stats)))
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


def convert(pdf_bytes, first_page=None, last_page=None):
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
    for i in range(n):
        page = doc[i]
        chars = _page_chars(page)
        for c in chars:
            if c.ch.strip():
                font_count[c.font] += 1
        raw_pages.append((page.get_width(), page.get_height(), chars, _tint_boxes(page)))
    total_chars = sum(len(c) for _, _, c, _ in raw_pages[first - 1:last])
    if total_chars < 20 * (last - first + 1) and total_chars < 400:
        raise PdfImportError("Bu PDF'te (seçilen sayfalarda) metin katmanı yok ya da çok az. Taranmış bir PDF olabilir; "
                             "önce OCR ile metin katmanı eklenmeli.")
    orn = frozenset(f for f, c in font_count.items() if c < 30 and ORNAMENT_FONT.search(f))
    pages = []
    for i, (W, H, chars, boxes) in enumerate(raw_pages):
        pages.append(build_blocks(i + 1, W, H, chars, boxes, orn))
    raw_pages = None
    d = Doc(pages)
    md, stats = build_markdown(d, first - 1, last - 1)
    if len(md.split()) < 20:
        raise PdfImportError("Seçilen sayfalardan okunabilir metin çıkmadı (yalnızca filigran/üst-alt bilgi olabilir). "
                             "Sayfa aralığını kontrol et.")
    warnings = []
    two = [p["p"] for p in d.pages[first - 1:last] if p["two_col"]]
    if two:
        warnings.append("İki sütun şüphesi olan sayfalar: " + _fmt_pages(two) + " (sütun sırası düzeltilmedi, metni kontrol et).")
    if stats["page_merges"]:
        warnings.append(f"{stats['page_merges']} paragraf sayfa geçişinde birleştirildi.")
    if stats["hyphen_real"]:
        warnings.append(f"{stats['hyphen_real']} satır sonu tiresi gerçek tire sayıldı (kitapta aynı sözcük tireli de geçiyor); "
                        f"{stats['hyphen_soft']} tanesi hece bölme sayılıp birleştirildi.")
    skipped = [p["p"] for p in d.pages[first - 1:last] if p["cover"]]
    if skipped:
        warnings.append("Kapak/açık yayılım sayfası atlandı: " + _fmt_pages(skipped) + ".")
    return md, warnings


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
