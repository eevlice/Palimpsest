"""Run: python3 -I tests/test_server_missing_libs.py   (a temporary HOME; the optional libraries are hidden)

The server starts without its optional libraries (pypdfium2, pdfminer.six, pypandoc, python-docx) and says, in
Turkish, which one a feature needs when it is used; a launcher that could not install new libraries opens with the
previous setup, so this happens."""
import io
import os
import sys
import tempfile

HOME = tempfile.mkdtemp(prefix="palimpsest-test-")
os.environ["HOME"] = HOME
for name in ("pypdfium2", "pypdfium2.raw", "pdfminer", "pypandoc", "docx"):
    sys.modules[name] = None                        # import raises ImportError, as if not installed
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

failed = 0


def check(name, cond, extra=""):
    global failed
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        failed += 1


import server
c = server.app.test_client()
check("the server starts and serves the page without the optional libraries", c.get("/").status_code == 200)

os.makedirs(server.PROJECTS_DIR, exist_ok=True)
open(server.pdf_path("p_x"), "wb").write(b"%PDF-1.4\n")
r = c.get("/page_image?project=p_x&page=1")
check("page preview: 400 naming pypdfium2, in Turkish", r.status_code == 400 and "pypdfium2 kütüphanesi eksik" in r.get_json()["error"], r.data[:200])
r = c.post("/export_docx", json={"mode": "word", "name": "x", "markdown": "a"})
check("Word export: names pypandoc_binary", r.status_code == 400 and "pypandoc_binary kütüphanesi eksik" in r.get_json()["error"], r.data[:200])
r = c.post("/export_docx", json={"mode": "redaksiyon", "name": "x", "rows": [{"src": "a", "tgt": "b"}]})
check("review copy: names python-docx", r.status_code == 400 and "python-docx kütüphanesi eksik" in r.get_json()["error"], r.data[:200])
r = c.post("/setup", data={"file": (io.BytesIO(b"%PDF-1.4\n"), "kitap.pdf"), "model": "anthropic::x"}, content_type="multipart/form-data")
check("PDF import: names pypdfium2", r.status_code == 400 and "pypdfium2 kütüphanesi eksik" in r.get_json()["error"], r.data[:200])
r = c.post("/setup", data={"file": (io.BytesIO(b"PK\x03\x04"), "kitap.docx"), "model": "anthropic::x"}, content_type="multipart/form-data")
check(".docx import: names python-docx, without an English wrapper", r.status_code == 400
      and r.get_json()["error"].startswith(".docx okuma için python-docx kütüphanesi eksik"), r.data[:200])

import shutil
shutil.rmtree(HOME, ignore_errors=True)
print("FAILED %d" % failed if failed else "ALL PASS")
sys.exit(1 if failed else 0)
