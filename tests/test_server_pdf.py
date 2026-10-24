"""Run: python3 -I tests/test_server_pdf.py   (plain asserts; a temporary HOME, no network, no real projects)

The imported PDF travels with its project: kept aside at /setup, moved next to the project on the first /save,
copied by /duplicate, moved to the trash by /delete. segMeta comes back from /setup one entry per segment."""
import io
import os
import sys
import json
import ctypes
import tempfile

HOME = tempfile.mkdtemp(prefix="palimpsest-test-")
os.environ["HOME"] = HOME                       # before server is imported: it reads ~/.palimpsest at import time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import server
import pypdfium2 as pdfium
import pypdfium2.raw as raw

failed = 0


def check(name, cond, extra=""):
    global failed
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)))
    if not cond:
        failed += 1


check("the test runs in a temporary HOME", server.PROJECTS_DIR.startswith(HOME), server.PROJECTS_DIR)

# no model calls: a fixed, valid context answer
TOK = {"input": 10, "output": 5, "cache_read": 0, "cache_write": 0, "stop_reason": "end_turn", "truncated": False}
server.build_context = lambda provider, model, src, tgt, text: ("=== SYNOPSIS ===\nKısa özet.\n\n=== TERM SHEET ===\n", 0.0, dict(TOK))
server.condense_brief = lambda provider, model, synopsis: (synopsis, None, None)
server.keys.get_key = lambda provider: "test-key"


def synth_pdf(n=4):
    pdf = pdfium.PdfDocument.new()
    for p in range(n):
        page = pdf.new_page(522, 738)
        for k in range(8):
            text = f"Plain sentence {k} on page {p} keeps going for a while here."
            obj = raw.FPDFPageObj_NewTextObj(pdf.raw, b"Helvetica", 11)
            buf = (ctypes.c_ushort * (len(text) + 1))(*([ord(c) for c in text] + [0]))
            raw.FPDFText_SetText(obj, buf)
            raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 72, 650 - 14 * k)
            raw.FPDFPage_InsertObject(page.raw, obj)
        raw.FPDFPage_GenerateContent(page.raw)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


c = server.app.test_client()
data = synth_pdf()
r = c.post("/setup", data={"file": (io.BytesIO(data), "kitap.pdf"), "project_id": "p_test1", "model": "anthropic::claude-opus-5-5",
                           "name": "deneme"}, content_type="multipart/form-data")
d = r.get_json()
check("/setup with a PDF answers 200", r.status_code == 200, d)
check("segMeta: one entry per segment, with pages and boxes", isinstance(d.get("segMeta"), list) and len(d["segMeta"]) == len(d["segments"])
      and all(m and m["pages"] and m["boxes"] for m in d["segMeta"]), d.get("segMeta", [])[:2])
pending = server.pdf_path("p_test1", True)
check("the PDF waits under projects/_pending until the project is saved", os.path.exists(pending) and open(pending, "rb").read() == data
      and not os.path.exists(server.pdf_path("p_test1")))
proj = {"id": "p_test1", "name": "deneme", "segments": d["segments"], "finals": [None] * len(d["segments"]),
        "approved": [False] * len(d["segments"]), "segMeta": d["segMeta"]}
c.post("/save", json=proj)
check("first /save moves it next to the project as <id>.pdf", os.path.exists(server.pdf_path("p_test1")) and not os.path.exists(pending))
saved = json.load(open(server.project_path("p_test1"), encoding="utf-8"))
check("segMeta is saved with the project, same length", len(saved["segMeta"]) == len(saved["segments"]))
r = c.post("/duplicate", json={"id": "p_test1"})
dup = r.get_json()["id"]
check("/duplicate copies the PDF for the copy", os.path.exists(server.pdf_path(dup)) and open(server.pdf_path(dup), "rb").read() == data)
c.post("/delete", json={"id": "p_test1"})
trash = os.path.join(server.PROJECTS_DIR, "_trash")
check("/delete takes the PDF along to the trash", not os.path.exists(server.pdf_path("p_test1")) and os.path.exists(os.path.join(trash, "p_test1.pdf"))
      and os.path.exists(os.path.join(trash, "p_test1.json")))
r = c.post("/setup", data={"file": (io.BytesIO(b"Bir paragraf.\n\nIki paragraf."), "kitap.txt"), "project_id": "p_test2",
                           "model": "anthropic::claude-opus-5-5", "name": "txt"}, content_type="multipart/form-data")
d = r.get_json()
check("a non-PDF import has no segMeta and keeps no file", d.get("segMeta") is None and not os.path.exists(server.pdf_path("p_test2", True)))
check("project listing ignores the PDFs", all(p["id"] != "p_test1" for p in c.get("/projects").get_json()))

# segMeta follows segment()'s footnote reordering
raw_segs = ["Text with a note[^1].", "Other text.", "[^1]: The note."]
segs, _ = server.segment("\n\n".join(raw_segs))
meta = [{"pages": [1]}, {"pages": [2]}, {"pages": [3]}]
moved = server.carry_meta(raw_segs, segs, meta)
check("segMeta follows a footnote moved by segment()", segs[1].startswith("[^1]:") and moved[1]["pages"] == [3] and moved[2]["pages"] == [2], (segs, moved))
check("a count mismatch gives no meta rather than misplaced meta", server.carry_meta(raw_segs, segs, meta[:2]) is None)

# abandoned imports: pending PDFs older than a day go at startup
pend = os.path.dirname(server.pdf_path("x", True))
os.makedirs(pend, exist_ok=True)
old, new = os.path.join(pend, "p_old.pdf"), os.path.join(pend, "p_new.pdf")
for f in (old, new):
    open(f, "wb").write(b"%PDF-1.4")
day = 24 * 3600
os.utime(old, (os.path.getmtime(old) - day - 60,) * 2)
os.utime(new, (os.path.getmtime(new) - day + 600,) * 2)
removed = server.clean_pending()
check("startup cleanup removes pending PDFs older than 24 hours, keeps newer ones", removed == 1 and not os.path.exists(old) and os.path.exists(new))

import shutil
shutil.rmtree(HOME, ignore_errors=True)
print("FAILED %d" % failed if failed else "ALL PASS")
sys.exit(1 if failed else 0)
