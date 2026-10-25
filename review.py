"""The review screen's questions: the places a PDF import is not sure about, asked once per look-alike group.

Built only from what pdf_import already knows (roles and their confidence, review flags, unmatched notes, two-column
pages, margin lines it dropped or kept, the PDF's own bookmarks); nothing new is guessed here. An answer becomes
decisions for pdf_import.apply_overrides (decisions_for), so the import is rendered again with them.

    questions(state, rendered) -> [question]     state: pdf_import.analyse(); rendered: pdf_import.render(state)
    decisions_for(question, key, apply_all) -> [decision]
    integrity(state) -> dict(lost, pages, sentence)
"""
import collections
import difflib
import re

import pdf_import as P

ROLE_LABEL = {"gövde": "Paragraf", "bölüm": "Başlık 1", "alt başlık 1": "Başlık 2", "alt başlık 2": "Başlık 3",
              "kısım": "Başlık 1", "epigraf": "Epigraf", "alıntı": "Alıntı", "şiir": "Şiir", "atıf": "Atıf",
              "altyazı": "Altyazı", "kutu": "Kutu", "kutu-başlık": "Kutu", "liste": "Paragraf", "sil": "Atılsın"}
# "Başka…": every role the user can give, in this order
OTHER = [{"key": "role:" + r, "label": ROLE_LABEL[r]} for r in P.USER_ROLES]
# the two likeliest alternatives to a guessed role
ALTERNATIVES = {"epigraf": ["gövde", "alıntı"], "alıntı": ["gövde", "şiir"], "şiir": ["alıntı", "gövde"],
                "atıf": ["gövde", "epigraf"], "kutu": ["gövde", "alıntı"], "kutu-başlık": ["gövde", "bölüm"],
                "altyazı": ["gövde", "sil"], "gövde": ["alıntı", "sil"], "bölüm": ["gövde", "sil"],
                "kısım": ["gövde", "sil"], "alt başlık 1": ["gövde", "sil"], "alt başlık 2": ["gövde", "sil"]}
SHAPE = {"indent", "indent-right", "narrow", "centered", "right", "ragged", "italic-block", "smaller", "font", "hanging"}
TITLES = {"rol": "İşaretli yazı ne?", "stil": "İşaretli yazı ne?", "baslik": "Bu başlık içindekilerde yok. Ne olsun?",
          "atif": "Bu atıf rakamının notu bulunamadı. Ne olsun?", "not": "Bu notun atfı bulunamadı. Ne olsun?",
          "sutun": "Bu sayfa iki sütunlu okundu. Doğru mu?", "tekrar": "Bu tekrar eden yazı ne yapılsın?"}


def _box(p, b):
    return [p["p"], round(b["x0"], 1), round(b["top"], 1), round(b["x1"], 1), round(b["bot"], 1)]


def _norm(t):
    return re.sub(r"[^a-z0-9]+", "", t.lower())


def _filename_like(t):
    """bookmarks named like files ("WallinCh01") are no contents"""
    return bool(re.fullmatch(r"[A-Za-z]+[A-Z][A-Za-z]*\d+|[\w-]+\.(?:indd|pdf|doc)", t.strip()))


def _role_choices(guess):
    keys = [guess] + [r for r in ALTERNATIVES.get(guess, ["gövde", "sil"]) if r != guess]
    return [{"key": "role:" + r, "label": ROLE_LABEL[r] + (" (şimdiki)" if r == guess else "")} for r in keys[:3]]


def questions(state, rendered):
    d, first, last = state["doc"], state["first"], state["last"]
    seg_of = {}
    for i, bids in enumerate(d.seg_bids):
        for bid in bids:
            seg_of.setdefault(bid, i)
    pages = [p for p in d.pages[first - 1:last]]
    groups = collections.OrderedDict()            # signature -> question being built

    def add(sig, qtype, p, b, guess=None, target=None, choices=None):
        q = groups.get(sig)
        if q is None:
            q = groups[sig] = dict(type=qtype, title=TITLES[qtype], page=p["p"], boxes=[_box(p, b)] if b else [],
                                   seg=seg_of.get(b["bid"]) if b else None, targets=[], choices=choices or [],
                                   guess=guess, other=OTHER if qtype in ("rol", "stil", "baslik") else [])
        q["targets"].append(target if target is not None else b["bid"])
        return q

    # 1. named roles set with low or middling confidence, captions joined by look alone; 2. blocks flagged for review.
    # Asked per unit, the importer's seg_meta "unit" (a multi-paragraph epigraph, quote, poem or box; any other
    # segment is a unit of its own): one shaky part makes the whole unit asked, a sure part gets no exemption. An
    # attribution closing a unit keeps its own role.
    for unit in _units(d, rendered["meta"], first, last):
        reason = None
        for p, b in unit:
            role, flags = b["role"], set(b.get("flags") or ())
            if role in P.ROLE_TYPE and b.get("conf") is not None and b["conf"] < 0.8:
                reason = reason or ("rol", p, b)
            elif role == "altyazı" and b.get("obj") and not P.CAPTION.match(b["text"].replace("*", "")):
                reason = reason or ("rol", p, b)
            elif "stil-farkli" in flags:
                reason = reason or ("stil", p, b)
        if reason is None:
            continue
        qtype, p, b = reason
        role = b["role"]
        shape = tuple(sorted(set(b.get("flags") or ()) & SHAPE))
        q = add((qtype, role, b["style"], shape), qtype, p, b, "role:" + role,
                target=[x["bid"] for _, x in unit], choices=_role_choices(role))
        if len(q["targets"]) == 1:                   # the shown unit: every one of its boxes, every block
            q["boxes"] = [_box(pp, x) for pp, x in unit]
            q["bids"] = [x["bid"] for _, x in unit]
    # 3. headings the PDF's bookmarks do not list (only when the bookmarks are a real contents)
    outline = [o["title"] for o in state["ctx"].get("outline", []) if o["title"] and not _filename_like(o["title"])]
    if len(outline) >= 3:
        toc = [_norm(t) for t in outline]

        def listed(t):
            n = _norm(t)
            return bool(n) and any(n in o or o in n or difflib.SequenceMatcher(None, n, o).ratio() >= 0.8 for o in toc)
        for p in pages:
            for b in p["blocks"]:
                if b.get("bid") and b["role"] in P.HEAD and not listed(b["text"]):
                    add(("baslik", b["role"], b["style"]), "baslik", p, b, "role:" + b["role"], choices=_role_choices(b["role"]))
    # 4. unmatched notes: callouts with no note, notes with no callout
    for i, m in enumerate(rendered["meta"]):
        bids = d.seg_bids[i]
        if not bids:
            continue
        p = d.pages[m["pages"][0] - 1]
        b = next((x for x in p["blocks"] if x.get("bid") == bids[0]), None)
        if b is None:
            continue
        if "atif-eslesmedi" in m["flags"]:
            add(("atif", b["style"]), "atif", p, b, "none", choices=[
                {"key": "none", "label": "Rakam metinde kalsın (şimdiki)"}, {"key": "drop_refs", "label": "Rakam silinsin"}])
        if "not-eslesmedi" in m["flags"]:
            add(("not", b["style"]), "not", p, b, "none", choices=[
                {"key": "none", "label": "Olduğu gibi kalsın (şimdiki)"}, {"key": "role:sil", "label": "Atılsın"}])
    # 5. two-column pages
    for p in pages:
        if p.get("two_col") and p["p"] not in d.index_pages and p["blocks"]:
            add(("sutun",), "sutun", p, None, "none", target=p["p"], choices=[
                {"key": "none", "label": "Doğru: önce sol, sonra sağ sütun (şimdiki)"},
                {"key": "order:rows", "label": "Tek sütun: yukarıdan aşağı"}])
    for q in groups.values():
        if q["type"] == "sutun":
            p = d.pages[q["page"] - 1]
            q["boxes"] = [_box(p, b) for b in p["blocks"][:1]]
            q["seg"] = seg_of.get(p["blocks"][0]["bid"])
    out = list(groups.values()) + _repeats(d, pages, seg_of)
    out.sort(key=lambda q: (q["page"], q["type"]))
    for k, q in enumerate(out, 1):
        q["id"] = "q%d" % k
        q["count"] = len(q["targets"])
        t = q["targets"][0]                        # the block shown: the segment it is in, after any answer
        t = t[0] if isinstance(t, list) else t
        q.setdefault("bid", t["bid"] if isinstance(t, dict) else t if isinstance(t, str) else None)
        q.setdefault("bids", [q["bid"]] if q["bid"] else [])
    return out


def _units(d, meta, first, last):
    """[(page, block)] per unit: the blocks of consecutive segments sharing seg_meta "unit" (one definition, the
    importer's), or of one segment; an attribution is a unit of its own (its role is not the unit's); blocks the
    questions never ask about are left out"""
    where = {b.get("bid"): (p, b) for p in d.pages for b in p["blocks"]}
    out, prev = [], None
    for i, m in enumerate(meta):
        if not first <= m["pages"][0] <= last if m.get("pages") else True:
            prev = None
            continue
        u = m.get("unit")
        blocks = [where[x] for x in d.seg_bids[i] if x in where
                  and where[x][1]["role"] not in ("sil", "ön sayfa", "dizin", "dipnot")]
        if blocks and all(bb["role"] == "atıf" for _, bb in blocks):
            out.append(blocks)                      # an attribution is asked on its own, never with its unit
            prev = None
            continue
        if u and u == prev:
            out[-1].extend(blocks)
        elif blocks:
            out.append(blocks)
        prev = u
    return [x for x in out if x]


def _repeats(d, pages, seg_of):
    """6. inconsistent repeats: the same margin line (same letters, or same non-body style at the same edge) dropped
    on some pages and kept on others. One question per text, then per style for the rest; an answer goes to all."""
    inst = []                                      # (page, block, line index, cut?, whole block?, line)
    for p in pages:
        for b in p["blocks"]:
            if not b.get("bid") or b["role"] in ("ön sayfa", "dizin"):
                continue
            dropped = b["role"] == "sil"
            for k, l in enumerate(b["lines"]):
                if l.get("zone"):
                    inst.append(dict(p=p, b=b, line=k, cut=False, kept=not dropped, whole=len(b["lines"]) == 1, l=l))
            for k, l in enumerate(b.get("cut_lines", [])):
                if l.get("zone"):
                    inst.append(dict(p=p, b=b, line=k, cut=True, kept=False, whole=False, l=l))
    body = set(d.body_styles)
    out, used = [], set()
    for by in ("text", "style"):
        groups = collections.defaultdict(list)
        for x in inst:
            if id(x["l"]) in used:
                continue
            if by == "text":
                key = P._similar_key(x["l"]["plain"])
                if len(key) < 4:
                    continue
                groups[(x["l"]["zone"], key)].append(x)
            elif x["l"]["style"] not in body:
                groups[(x["l"]["zone"], x["l"]["style"])].append(x)
        for key, xs in groups.items():
            kept, drop = [x for x in xs if x["kept"]], [x for x in xs if not x["kept"]]
            if not kept or not drop or len({x["p"]["p"] for x in xs}) < 3:
                continue
            used.update(id(x["l"]) for x in xs)
            rep = kept[0]
            roles = collections.Counter(x["b"]["role"] for x in kept)
            keep_role = roles.most_common(1)[0][0] if roles else "gövde"
            out.append(dict(
                type="tekrar", title=TITLES["tekrar"], page=rep["p"]["p"], boxes=[_box(rep["p"], rep["b"])], bid=rep["b"]["bid"],
                seg=seg_of.get(rep["b"]["bid"]), guess=None,
                detail=f"{len({x['p']['p'] for x in drop})} sayfada atıldı, {len({x['p']['p'] for x in kept})} sayfada metinde kaldı.",
                text=rep["l"]["plain"].strip()[:80],
                targets=[dict(bid=x["b"]["bid"], line=x["line"], cut=x["cut"], kept=x["kept"], whole=x["whole"] or (
                    not x["cut"] and len(x["b"]["lines"]) == 1)) for x in xs],
                keep_role=keep_role if keep_role != "sil" else "gövde", other=OTHER,
                choices=[{"key": "drop", "label": "Hepsini at"}, {"key": "keep", "label": "Hepsini tut"}]))
    return out


def decisions_for(q, key, apply_all=True):
    """an answer (a choice's key) as pdf_import.apply_overrides decisions, for one target or all of them"""
    if key in (None, "none", "skip"):
        return []
    if q["type"] == "tekrar":
        out = []
        for t in q["targets"]:
            if key == "drop" and t["kept"]:
                out.append({"bid": t["bid"], "role": "sil"} if t["whole"] else
                           {"bid": t["bid"], "line": t["line"], "cut": False, "keep": False})
            elif key == "keep" and not t["kept"]:
                out.append({"bid": t["bid"], "line": t["line"], "cut": True, "keep": True} if t["cut"] else
                           {"bid": t["bid"], "role": q.get("keep_role", "gövde")})
            elif key.startswith("role:"):                  # "Başka…": one role for every instance
                if t["cut"]:
                    out.append({"bid": t["bid"], "line": t["line"], "cut": True, "keep": True})
                elif t["whole"] or not t["kept"]:
                    out.append({"bid": t["bid"], "role": key[5:]})
        # lines leave and come back by index: the highest index of a block first
        return sorted(out, key=lambda x: (x["bid"], -x.get("line", -1)))
    targets = q["targets"] if apply_all else q["targets"][:1]
    targets = [b for t in targets for b in (t if isinstance(t, list) else [t])]     # units: all their blocks
    if key.startswith("role:"):
        return [{"bid": t, "role": key[5:]} for t in targets]
    if key == "drop_refs":
        return [{"bid": t, "drop_refs": True} for t in targets]
    if key == "order:rows":
        return [{"page": t, "order": "rows"} for t in targets]
    return []


def integrity(state):
    """Every character pdfium read on the requested pages is either in a block (written or dropped as margin text,
    front matter, index), inside a figure or table, set at an angle, or an ornament glyph; what is none of these is
    lost. -> dict(lost, pages, sentence)"""
    d, first, last = state["doc"], state["first"], state["last"]
    ns = lambda t: sum(1 for ch in t if ch.strip())
    lost = {}
    for p in d.pages[first - 1:last]:
        acc = sum(ns(l["plain"]) for b in p["blocks"] for l in b["lines"] + b.get("cut_lines", []))
        acc += sum(ns(t) for t in p.get("fig_text", [])) + sum(ns(it.get("text", "")) for it in p.get("items", []))
        acc += p.get("rotated", 0) + p.get("orn_chars", 0)
        if p.get("src_chars", acc) > acc:
            lost[p["p"]] = p["src_chars"] - acc
    n = sum(lost.values())
    sentence = "Metin kaybı yok" if not n else f"{n} karakter kayıp, s. {P._fmt_pages(sorted(lost))}'te"
    return dict(lost=n, pages=sorted(lost), sentence=sentence)
