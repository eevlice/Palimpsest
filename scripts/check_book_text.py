#!/usr/bin/env python3
"""Stop book text from entering the repo.

Run by the pre-commit hook (scripts/hooks/pre-commit) on the staged files, or by hand on given files:

    python3 scripts/check_book_text.py            # staged files (what the next commit would contain)
    python3 scripts/check_book_text.py FILE...    # these files as they are on disk

A file that shares a run of 6 words with one of the test books (the tuning and holdout books under
~/palimpsest-spike/karne and ~/palimpsest-spike/karne-holdout) stops the commit; every run found is shown with its
book. Files listed by exact path in scripts/book_text_allowlist.txt (each with its reason) are not read. The books
are read only from this machine and never copied into the repo: from their gt.json (text already
extracted) or, failing that, from kitap.pdf with pypdfium2. A word index is cached under ~/.palimpsest/. Without the
book folders the check warns and lets the commit through. PALIMPSEST_BOOK_DIRS (paths separated by ':') replaces
the default folders."""
import hashlib
import json
import os
import pickle
import re
import subprocess
import sys

N = 6
HOME = os.path.expanduser("~")
DEFAULT_DIRS = [os.path.join(HOME, "palimpsest-spike", "karne", "books"),
                os.path.join(HOME, "palimpsest-spike", "karne-holdout", "books")]
CACHE = os.path.join(HOME, ".palimpsest", "book_ngrams.pickle")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWLIST = os.path.join(ROOT, "scripts", "book_text_allowlist.txt")
WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?")


def words(text):
    return [w.replace("’", "'").lower() for w in WORD.findall(text)]


def key(ws):
    return int.from_bytes(hashlib.blake2b(" ".join(ws).encode("utf-8"), digest_size=8).digest(), "big")


def book_text(folder):
    gt = os.path.join(folder, "gt.json")
    if os.path.exists(gt):
        d = json.load(open(gt, encoding="utf-8"))
        return "\n".join(l["text"] for p in d["pages"] for l in p["lines"]), gt
    pdf = os.path.join(folder, "kitap.pdf")
    if os.path.exists(pdf):
        try:
            import pypdfium2 as pdfium
        except ImportError:
            print(f"uyarı: {pdf} okunamadı (pypdfium2 yok); bu kitap denetlenmiyor", file=sys.stderr)
            return None, None
        doc = pdfium.PdfDocument(pdf)
        return "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc))), pdf
    return None, None


def index(dirs):
    """{ngram key: book name} for every book folder; rebuilt when a book's source file changes"""
    books = []
    for d in dirs:
        if os.path.isdir(d):
            books += [os.path.join(d, b) for b in sorted(os.listdir(d)) if os.path.isdir(os.path.join(d, b))]
    if not books:
        return None
    try:
        cache = pickle.load(open(CACHE, "rb"))
    except Exception:
        cache = {}
    idx, changed = {}, False
    for b in books:
        name = os.path.basename(b)
        src = os.path.join(b, "gt.json") if os.path.exists(os.path.join(b, "gt.json")) else os.path.join(b, "kitap.pdf")
        stamp = (src, os.path.getmtime(src), os.path.getsize(src)) if os.path.exists(src) else None
        hit = cache.get(b)
        if stamp is None:
            continue
        if not hit or hit[0] != stamp:
            text, _ = book_text(b)
            if text is None:
                continue
            ws = words(text)
            hit = (stamp, {key(ws[i:i + N]) for i in range(len(ws) - N + 1)})
            cache[b], changed = hit, True
        for k in hit[1]:
            idx.setdefault(k, name)
    if changed:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        pickle.dump(cache, open(CACHE, "wb"))
    return idx


def staged():
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"], capture_output=True, text=True,
                         check=True).stdout.split("\n")
    for path in filter(None, out):
        blob = subprocess.run(["git", "show", ":" + path], capture_output=True, check=True).stdout
        yield path, blob


def on_disk(paths):
    for path in paths:
        yield path, open(path, "rb").read()


def allowlist():
    """the exact repo-relative paths in ALLOWLIST (a # starts the reason; no globs)"""
    try:
        lines = open(ALLOWLIST, encoding="utf-8").read().split("\n")
    except FileNotFoundError:
        return set()
    return {os.path.normpath(l.split("#", 1)[0].strip()) for l in lines if l.split("#", 1)[0].strip()}


def find(idx, files):
    hits, allowed = [], allowlist()
    for path, blob in files:
        if os.path.relpath(os.path.realpath(path), os.path.realpath(ROOT)) in allowed:    # the hook runs at the repo root
            continue                                # listed with its reason (a licence kept verbatim)
        if b"\0" in blob[:4096]:
            continue                                # binary (reference.docx): not text
        ws = words(blob.decode("utf-8", "replace"))
        for i in range(len(ws) - N + 1):
            k = key(ws[i:i + N])
            if k in idx:
                hits.append((path, " ".join(ws[i:i + N]), idx[k]))
    return hits


def main(argv):
    dirs = os.environ.get("PALIMPSEST_BOOK_DIRS", "").split(":") if os.environ.get("PALIMPSEST_BOOK_DIRS") else DEFAULT_DIRS
    idx = index(dirs)
    if idx is None:
        print("uyarı: kitap klasörleri bulunamadı (%s); kitap metni denetimi atlandı" % ", ".join(dirs), file=sys.stderr)
        return 0
    hits = find(idx, on_disk(argv) if argv else staged())
    if not hits:
        return 0
    print("Commit durduruldu: kitap metniyle %d sözcüklük ortak dizi bulundu." % N, file=sys.stderr)
    seen = set()
    for path, run, book in hits:
        if (path, run) not in seen:
            seen.add((path, run))
            print(f"  {path}: “{run}”  ({book})", file=sys.stderr)
    print("Testlerde ve fixture'larda yalnızca sentetik metin kullanılır.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
