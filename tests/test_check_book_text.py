"""Run: python3 -I tests/test_check_book_text.py   (a temporary HOME and a synthetic book; no real book is read)"""
import io
import json
import os
import sys
import tempfile
import contextlib

HOME = tempfile.mkdtemp(prefix="palimpsest-test-")
os.environ["HOME"] = HOME                       # the word index cache goes under the temporary HOME
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import check_book_text as C

failed = 0


def check(name, cond, extra=""):
    global failed
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        failed += 1


books = os.path.join(HOME, "books")
os.makedirs(os.path.join(books, "sample_book"))
json.dump({"pages": [{"lines": [{"text": "The quiet lantern keeper counted seven grey boats"},
                                {"text": "before the harbour bell rang twice at dawn."}]}]},
          open(os.path.join(books, "sample_book", "gt.json"), "w"))
os.environ["PALIMPSEST_BOOK_DIRS"] = books


def run(text, binary=False):
    path = os.path.join(HOME, "file.bin" if binary else "file.py")
    open(path, "wb").write(text if binary else text.encode("utf-8"))
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = C.main([path])
    return code, err.getvalue()


code, err = run('rows = [("the quiet lantern keeper counted seven", 100, 200)]')
check("six words in a row from a book stop the commit and are shown", code == 1 and "the quiet lantern keeper counted seven" in err
      and "sample_book" in err, err)
code, err = run('x = "Seven grey boats, before the harbour bell!"')
check("...across a line break of the book, case and punctuation aside", code == 1, err)
code, err = run('x = "the quiet lantern keeper counted"')
check("five shared words are not enough", code == 0, err)
code, err = run(b"\x00\x01the quiet lantern keeper counted seven grey boats", binary=True)
check("binary files are not read", code == 0)
# a file listed by its exact path in the allowlist is not read; the same text at any other path is
repo = os.path.join(HOME, "repo")
os.makedirs(os.path.join(repo, "data")); os.makedirs(os.path.join(repo, "other"))
C.ROOT, C.ALLOWLIST = repo, os.path.join(repo, "allow.txt")
open(C.ALLOWLIST, "w").write("# reasons after a hash\ndata/LICENCE.txt  # a licence kept verbatim\n")
text = "Granted, including the quiet lantern keeper counted seven grey boats."
for rel in ("data/LICENCE.txt", "other/LICENCE.txt", "data/LICENCE.txt.bak"):
    open(os.path.join(repo, rel), "w").write(text)
err = io.StringIO()
with contextlib.redirect_stderr(err):
    listed = C.main([os.path.join(repo, "data", "LICENCE.txt")])
    elsewhere = C.main([os.path.join(repo, "other", "LICENCE.txt")])
    lookalike = C.main([os.path.join(repo, "data", "LICENCE.txt.bak")])
check("allowlist: the listed path passes; the same text at another path or a look-alike name stops the commit",
      (listed, elsewhere, lookalike) == (0, 1, 1), (listed, elsewhere, lookalike, err.getvalue()))
cwd = os.getcwd(); os.chdir(repo)
with contextlib.redirect_stderr(io.StringIO()):
    rel_ok = C.main(["data/LICENCE.txt"])
os.chdir(cwd)
check("allowlist: a path given relative to the repo root (as the hook gives staged files) is matched too", rel_ok == 0, rel_ok)
check("the repo's allowlist names exact paths only, each with a reason",
      all("#" in l and not any(ch in l.split("#")[0] for ch in "*?[") for l in open(os.path.join(os.path.dirname(HERE), "scripts", "book_text_allowlist.txt"))
          if l.strip() and not l.startswith("#")))

os.environ["PALIMPSEST_BOOK_DIRS"] = os.path.join(HOME, "nowhere")
code, err = run('rows = [("the quiet lantern keeper counted seven", 100, 200)]')
check("without the book folders: a warning, and the commit goes through", code == 0 and "atlandı" in err, err)

import shutil
shutil.rmtree(HOME, ignore_errors=True)
print("FAILED %d" % failed if failed else "ALL PASS")
sys.exit(1 if failed else 0)
