"""The review screen's questions: the places a PDF import is not sure about, asked once per look-alike group.

Built only from what pdf_import already knows (roles and their confidence, review flags, unmatched notes, two-column
pages, margin lines it dropped or kept, the PDF's own bookmarks); nothing new is guessed here. An answer becomes
decisions for pdf_import.apply_overrides (decisions_for), so the import is rendered again with them.

    questions(state, rendered) -> [question]     state: pdf_import.analyse(); rendered: pdf_import.render(state)
    decisions_for(question, key, apply_all) -> [decision]
    integrity(state) -> dict(lost, pages, sentence, notes, contents)
"""
import collections
import difflib
import re

import pdf_import as P
import segtypes

ROLE_LABEL = {"gövde": "Paragraf", "bölüm": "Başlık 1", "alt başlık 1": "Başlık 2", "alt başlık 2": "Başlık 3",
              "kısım": "Kısım", "epigraf": "Epigraf", "alıntı": "Alıntı", "şiir": "Şiir", "atıf": "Atıf",
              "altyazı": "Altyazı", "kutu": "Kutu", "kutu-başlık": "Kutu", "liste": "Paragraf", "dipnot": "Dipnot",
              "son not": "Son not", "sil": "Atılsın"}
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
          "sutun": "Bu sayfa iki sütunlu okundu. Doğru mu?", "tekrar": "Bu tekrar eden yazı ne yapılsın?",
          "bagla": "Hangi atıfa bağlansın?", "icindekiler": "İçindekilerde başlık, gövdede değil. Başlık olsun mu?",
          "icindekiler-yok": "İçindekilerdeki bu başlıklar gövdede bulunamadı.",
          "onsayfa": "Bu ön sayfa yazısı çevrilsin mi?"}


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

    def add(sig, qtype, p, b, guess=None, target=None, choices=None, title=None):
        q = groups.get(sig)
        if q is None:
            q = groups[sig] = dict(type=qtype, title=title or TITLES[qtype], page=p["p"], boxes=[_box(p, b)] if b else [],
                                   seg=seg_of.get(b["bid"]) if b else None, targets=[], choices=choices or [],
                                   guess=guess, other=OTHER if qtype in ("rol", "stil", "baslik", "eslestir", "icindekiler") else [])
        q["targets"].append(target if target is not None else b["bid"])
        return q

    # 1. named roles set with low or middling confidence, captions joined by look alone; 2. blocks flagged for review.
    # Asked per unit, the importer's seg_meta "unit" (a multi-paragraph epigraph, quote, poem or box; any other
    # segment is a unit of its own): one shaky part makes the whole unit asked, a sure part gets no exemption. An
    # attribution closing a unit keeps its own role. A note that found its callout is never asked.
    for unit in _units(d, rendered["meta"], first, last, _segments(rendered.get("md", ""))):
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
    # front matter kept as text (a dedication, a note on the author, praise): asked once per page
    for p in pages:
        fb = [b for b in p["blocks"] if b.get("front") and b.get("bid")]
        if fb:
            q = add(("onsayfa", p["p"]), "onsayfa", p, fb[0], "none", target=[b["bid"] for b in fb], choices=[
                {"key": "none", "label": "Çevrilsin (şimdiki)"}, {"key": "role:sil", "label": "Atılsın"}])
            q["boxes"], q["bids"] = [_box(p, b) for b in fb], [b["bid"] for b in fb]
    # 3. the contents: headings they do not list (the bookmarks too, when they are a real contents), asked only at the
    # levels the contents name (sub-headings are seldom listed: only counted); entries the body sets as no heading
    toc = getattr(d, "toc", None) if getattr(d, "toc", None) and len(d.toc["entries"]) >= 3 else None
    blocks = {b.get("bid"): (p, b) for p in d.pages for b in p["blocks"]}
    outline = [o["title"] for o in state["ctx"].get("outline", []) if o["title"] and not _filename_like(o["title"])]
    if len(outline) >= 3 or toc:
        names = [_norm(t) for t in outline] if len(outline) >= 3 else []
        listed_bids = {bid for e in toc["entries"] for bid in e.get("match", [])} if toc else set()
        covered = {blocks[bid][1]["role"] for bid in listed_bids if bid in blocks}

        def listed(b):
            n = _norm(b["text"])
            return b["bid"] in listed_bids or bool(n) and any(
                n in o or o in n or difflib.SequenceMatcher(None, n, o).ratio() >= 0.8 for o in names)
        for p in pages:
            for b in p["blocks"]:
                if b.get("bid") and b["role"] in P.HEAD and not listed(b) and (not toc or b["role"] in covered):
                    add(("baslik", b["role"], b["style"]), "baslik", p, b, "role:" + b["role"], choices=_role_choices(b["role"]))
    if toc:
        level_role = {}
        for e in toc["entries"]:
            if e.get("match") and e["match"][0] in blocks:
                level_role.setdefault(e["level"], blocks[e["match"][0]][1]["role"])
        missing = []
        for e in toc["entries"]:
            if e.get("match"):
                continue
            if e.get("cand") in blocks:
                p, b = blocks[e["cand"]]
                if not first <= p["p"] <= last:
                    continue
                r = level_role.get(e["level"], "bölüm")
                add(("icindekiler", e["level"], b["style"]), "icindekiler", p, b, "none", choices=[
                    {"key": "role:" + r, "label": ROLE_LABEL[r]}, {"key": "none", "label": "Olduğu gibi kalsın (şimdiki)"}])
            else:
                missing.append(e)
        if missing:
            p = d.pages[missing[0]["toc_page"] - 1]
            q = add(("icindekiler-yok",), "icindekiler-yok", p, None, "none", target=missing[0]["toc_page"],
                    choices=[{"key": "none", "label": "Tamam"}])
            q["boxes"] = [x["box"] for x in missing]
            q["count"] = 1
            q["detail"] = " · ".join(f"{x['title'][:60]}" + (f" (s. {x['num']})" if x["num"] else "") for x in missing[:6]) + (
                f" · ve {len(missing) - 6} başlık daha" if len(missing) > 6 else "")
    # 4. notes: a callout with no note and a note-like block near it (its mark at the start) are one question; else
    # callouts with no note (not asked when they are a book-level problem, see integrity), notes with no callout, and
    # the user's notes still to be tied to a callout
    notes = getattr(d, "notes", None) or dict(refs=0, matched=0, orphans=[], loose=[])
    orphans = [o for o in (_orphan(o, blocks) for o in notes["orphans"]) if o]
    for o in orphans:
        o["seg"] = seg_of.get(o["bid"])
    book_level = _book_level(notes)
    flagged = {i: m for i, m in enumerate(rendered["meta"]) if d.seg_bids[i] and {"atif-eslesmedi", "not-eslesmedi"} & set(m["flags"])}
    paired = set()                                  # note block ids asked as a pair
    for o in orphans:
        near = [(p, b) for p, b in _note_like(d, blocks, o, first, last) if b["bid"] not in paired]
        if len(near) == 1 and not book_level:
            p, b = near[0]
            paired.add(b["bid"])
            q = add(("eslestir", b["bid"]), "eslestir", p, b, "none", target=b["bid"],
                    title=f"Bu yazı, s. {o['page']}'teki {o['mark']} numaralı atıfın notu mu?", choices=[
                        {"key": "link", "label": "Evet"}, {"key": "none", "label": "Hayır (şimdiki)"}])
            q["ref"] = dict(bid=o["bid"], mark=o["mark"])
            o["paired"] = True
    asked = {o["seg"] for o in orphans if o.get("paired")}
    for i, m in flagged.items():
        p = d.pages[m["pages"][0] - 1]
        bids = d.seg_bids[i]
        b = next((x for x in p["blocks"] if x.get("bid") == bids[0]), None)
        if b is None:
            continue
        if "atif-eslesmedi" in m["flags"] and not book_level and i not in asked:
            add(("atif", b["style"]), "atif", p, b, "none", choices=[
                {"key": "none", "label": "Rakam metinde kalsın (şimdiki)"}, {"key": "drop_refs", "label": "Rakam silinsin"}])
        if "not-eslesmedi" in m["flags"] and not paired & set(bids) and not any(
                blocks.get(x, (0, {}))[1].get("user_note") for x in bids):
            add(("not", b["style"]), "not", p, b, "none", choices=[
                {"key": "none", "label": "Olduğu gibi kalsın (şimdiki)"}, {"key": "role:sil", "label": "Atılsın"}])
    for n in notes["loose"]:
        if not n["bids"] or n["bids"][0] not in blocks:
            continue
        p, b = blocks[n["bids"][0]]
        cand = [o for o in orphans if o["mark"] == n["mark"]] if n["role"] == "son not" and n["mark"] else []
        cand = cand or [o for o in orphans if abs(o["page"] - n["page"]) <= 1]
        refs = [dict(bid=o["bid"], mark=o["mark"]) for o in cand]
        q = add(("bagla", b["bid"]), "bagla", p, b, "none", target=b["bid"],
                title=None if refs else "Atıfsız not: yakında notu olmayan bir atıf yok.",
                choices=[{"key": "ref:%d" % k, "label": f"s. {o['page']} · {o['mark']}"} for k, o in enumerate(cand[:2])]
                + [{"key": "none", "label": "Atıfsız kalsın (şimdiki)"}])
        q["other"] = [{"key": "ref:%d" % k, "label": f"s. {o['page']} · {o['mark']}"} for k, o in enumerate(cand) if k >= 2]
        q["refs"] = refs
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
    out = _regions(list(groups.values()), d, blocks) + _repeats(d, pages, seg_of)
    out.sort(key=lambda q: (q["page"], q["type"]))
    for k, q in enumerate(out, 1):
        q["count"] = q.get("count") or len(q["targets"])
        t = q["targets"][0]                        # the block shown: the segment it is in, after any answer
        t = t[0] if isinstance(t, list) else t
        q.setdefault("bid", t["bid"] if isinstance(t, dict) else t if isinstance(t, str) else None)
        q.setdefault("bids", [q["bid"]] if q["bid"] else [])
        q["id"] = "n:" + q["bid"] if q["type"] == "bagla" else "q%d" % k     # asked after an answer: a stable id
    return out


def _regions(qs, d, blocks):
    """Role and style questions asked as one where one answer would do: a block the contents question already asks
    about is not asked again; questions in one chapter at most T["q_region_gap"] pages apart, on one page or at one
    text size (a reference list, a table, a page of figure text), are one; then, anywhere in the book, short lines of
    one style, and questions of one style the importer gave one role. A group whose blocks have different roles now
    keeps them unless answered ("Olduğu gibi kalsın")."""
    in_toc = {t for q in qs if q["type"] == "icindekiler" for t in q["targets"]}
    chapter, k = {}, 0                             # block -> the chapter (kısım/bölüm heading) it is in
    for p in d.pages:
        for b in p["blocks"]:
            k += b["role"] in ("kısım", "bölüm")
            chapter[b.get("bid")] = k
    keep, asked = [], []
    for q in qs:
        if q["type"] not in ("rol", "stil"):
            keep.append(q)
        elif not ({t for u in q["targets"] for t in u} & in_toc):
            asked.append(q)
    shown = lambda q: blocks[q["bids"][0]][1]
    runs = []
    for q in sorted(asked, key=lambda q: (q["page"], chapter.get(q["bids"][0], 0))):
        last = runs[-1][-1] if runs else None
        if last and chapter.get(q["bids"][0]) == chapter.get(last["bids"][0]) and q["page"] - last["page"] <= P.T["q_region_gap"] and (
                q["page"] == last["page"] or shown(q).get("size") == shown(last).get("size")):
            runs[-1].append(q)
        else:
            runs.append([q])
    by_style = collections.OrderedDict()
    for run in runs:
        b = shown(run[0])
        if len(run) == 1 and b.get("nwords", 0) <= P.T["q_short_words"]:
            by_style.setdefault((b.get("style"),), []).extend(run)
        elif all(shown(q).get("style") == b.get("style") and q["guess"] == run[0]["guess"] for q in run):
            by_style.setdefault((b.get("style"), run[0]["guess"]), []).extend(run)
        else:
            keep.append(_one(run, blocks))
    keep += [_one(run, blocks) for run in by_style.values()]
    return keep


def _one(run, blocks):
    """questions as one: every target, the first one shown; its current role when they share it"""
    if len(run) == 1:
        return run[0]
    q = dict(run[0], targets=[t for x in run for t in x["targets"]])
    q["boxes"] = [b for x in run if x["page"] == q["page"] for b in x["boxes"]]
    guesses = collections.Counter(x["guess"] for x in run)
    if len(guesses) > 1:
        q["type"], q["guess"] = "rol", "none"
        q["choices"] = [{"key": "none", "label": "Olduğu gibi kalsın (şimdiki)"}] + [
            {"key": g, "label": ROLE_LABEL[g[5:]]} for g, _ in guesses.most_common(2)]
    pages = sorted({blocks[t][0]["p"] for u in q["targets"] for t in u if t in blocks})
    q["detail"] = f"s. {P._fmt_pages(pages)}"
    return q


def _book_level(notes):
    """callouts without a note as a problem of the whole book: more than T["orphan_share"] of them, and many"""
    left = notes["refs"] - notes["matched"]
    return left >= P.T["orphan_min"] and left > P.T["orphan_share"] * notes["refs"]


def _segments(md):
    """the importer's markdown in segments, one per seg_meta entry"""
    return [x for x in re.split(r"\n\s*\n", md) if x.strip()]


def _orphan(o, blocks):
    """a callout left without a note, at its block: the first of its segment's blocks with a line carrying the mark"""
    for bid in o["bids"]:
        if bid in blocks and any(o["mark"] in l.get("refs", ()) for l in blocks[bid][1]["lines"]):
            return dict(bid=bid, mark=o["mark"], page=blocks[bid][0]["p"])
    return None


def _note_like(d, blocks, o, first, last):
    """blocks on the callout's page or the next either side that start with its mark and look like a note (set
    smaller than the body, or a note the importer found with no callout; also when dropped as margin text, but never
    a margin line proper, a folio or a running foot: three words at least): [(page, block)]"""
    body = P.fam_of(d.body_style)[1] if d.body_style else None
    out = []
    for pg in (o["page"] - 1, o["page"], o["page"] + 1):
        if not first <= pg <= last:
            continue
        p = d.pages[pg - 1]
        for b in p["blocks"]:
            if not b.get("bid") or not b["lines"] or b["role"] in P.HEAD or b["role"] in ("ön sayfa", "dizin") \
                    or b.get("user_note") or b["bid"] == o["bid"]:
                continue
            m = P.FOOT_START.match(b["lines"][0]["plain"])
            if m and m.group(1) == o["mark"] and len(b["lines"][0]["plain"].split()) >= 4 and (
                    b["role"] == "dipnot" or body and b["size"] <= P.T["foot_size"] * body):
                out.append((p, b))
    return out


def _units(d, meta, first, last, segs=None):
    """[(page, block)] per unit: the blocks of consecutive segments sharing seg_meta "unit" (one definition, the
    importer's), or of one segment; an attribution is a unit of its own (its role is not the unit's); blocks the
    questions never ask about are left out, and so are notes that found their callout ("[^label]: ...")"""
    where = {b.get("bid"): (p, b) for p in d.pages for b in p["blocks"]}
    out, prev = [], None
    for i, m in enumerate(meta):
        if not first <= m["pages"][0] <= last if m.get("pages") else True:
            prev = None
            continue
        if segs and i < len(segs) and segtypes.FOOTNOTE.match(segs[i]):
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
    if key == "link":                                  # "Evet": the block is the callout's footnote
        return [{"bid": q["bid"], "role": "dipnot", "ref": q["ref"]}]
    if key.startswith("ref:"):
        k = int(key[4:])
        return [{"bid": q["bid"], "note_ref": q["refs"][k]}] if 0 <= k < len(q.get("refs", [])) else []
    if key == "drop_refs":
        return [{"bid": t, "drop_refs": True} for t in targets]
    if key == "order:rows":
        return [{"page": t, "order": "rows"} for t in targets]
    return []


def integrity(state):
    """Every character pdfium read on the requested pages is either in a block (written or dropped as margin text,
    front matter, index), inside a figure or table, set at an angle, or an ornament glyph; what is none of these is
    lost. With callouts, how many found their note: notes = dict(matched, refs, low), low when the ones without
    are a book-level problem (then they are not asked one by one). With a table of contents: its pages and whether
    the user kept it in the text. -> dict(lost, pages, sentence, notes, contents)"""
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
    nt = getattr(d, "notes", None)
    notes = dict(matched=nt["matched"], refs=nt["refs"], low=_book_level(nt)) if nt and nt["refs"] else None
    toc = getattr(d, "toc", None)
    contents = dict(pages=P._fmt_pages(toc["pages"]), kept=bool(getattr(d, "toc_keep", False))) if toc else None
    return dict(lost=n, pages=sorted(lost), sentence=sentence, notes=notes, contents=contents)
