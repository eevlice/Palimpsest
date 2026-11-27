"""Marker (marker-pdf) as the layout reader of a PDF import: blocks, their types and boxes, reading order.

Only the layout is taken from Marker; the text of every block is rebuilt from pdfium's glyphs in pdf_import
(marker_blocks). Marker runs with OCR and LLM off. Its layout is cached per PDF (sha256), so a second import of the
same book, or the review screen, does not run it again.

    layout(pdf_bytes) -> dict(version, pages=[dict(W, H, blocks=[dict(t, bbox, id)])])

Marker runs in the Python named by PALIMPSEST_MARKER_PY when it is set (a separate environment that has marker-pdf),
as `python marker_reader.py <pdf> <out.json>`; otherwise in this process.
Cache: PALIMPSEST_MARKER_CACHE, else ~/.palimpsest/cache/marker.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

# block types that hold other blocks: their children are read instead (in their order)
GROUPS = {"ListGroup", "PictureGroup", "FigureGroup", "TableGroup"}
# parts of a block, never a block of their own
PARTS = {"Line", "Span", "Char", "TableCell"}
CACHE_VERSION = "1"


class MarkerError(Exception):
    pass


def _flatten(blocks):
    out = []
    for b in blocks or ():
        t = b.get("block_type")
        if t in PARTS:
            continue
        if t in GROUPS:
            out.extend(_flatten(b.get("children")))
            continue
        out.append(dict(t=t, bbox=[round(v, 2) for v in b["bbox"]], id=b.get("id", "")))
    return out


def _compact(js):
    """Marker's JSON document -> per page: its size and its blocks in reading order"""
    pages = []
    for pg in js.get("children") or ():
        x0, y0, x1, y1 = pg["bbox"]
        pages.append(dict(W=x1 - x0, H=y1 - y0, blocks=_flatten(pg.get("children"))))
    return pages


def _run_here(pdf_path):
    from marker.config.parser import ConfigParser
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.renderers.json import JSONRenderer
    cp = ConfigParser({"output_format": "json", "disable_ocr": True})
    conv = PdfConverter(config=cp.generate_config_dict(), artifact_dict=create_model_dict(),
                        processor_list=cp.get_processors(), renderer=cp.get_renderer())
    doc = conv.build_document(pdf_path)
    js = conv.resolve_dependencies(JSONRenderer)(doc)
    return _compact(json.loads(js.model_dump_json(exclude=["metadata"])))


def _marker_version():
    try:
        from importlib.metadata import version
        return version("marker-pdf")
    except Exception:
        return None


def layout(pdf_bytes):
    key = hashlib.sha256(pdf_bytes).hexdigest()
    cache = os.environ.get("PALIMPSEST_MARKER_CACHE") or os.path.join(os.path.expanduser("~"), ".palimpsest", "cache", "marker")
    path = os.path.join(cache, f"{key}.v{CACHE_VERSION}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    with tempfile.TemporaryDirectory() as tmp:
        pdf = os.path.join(tmp, "kitap.pdf")
        with open(pdf, "wb") as f:
            f.write(pdf_bytes)
        py = os.environ.get("PALIMPSEST_MARKER_PY")
        if not py:
            if not _marker_version():
                raise MarkerError("Marker kurulu değil (marker-pdf).")
            res = dict(version=_marker_version(), pages=_run_here(pdf))
        else:
            out = os.path.join(tmp, "layout.json")
            r = subprocess.run([py, os.path.abspath(__file__), pdf, out], capture_output=True, text=True)
            if r.returncode or not os.path.exists(out):
                raise MarkerError("Marker çalışmadı: " + (r.stderr or "").strip()[-500:])
            with open(out, encoding="utf-8") as f:
                res = json.load(f)
    os.makedirs(cache, exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(res, f)
    os.replace(path + ".tmp", path)
    return res


if __name__ == "__main__":
    pdf_path, out_path = sys.argv[1], sys.argv[2]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(dict(version=_marker_version(), pages=_run_here(pdf_path)), f)
