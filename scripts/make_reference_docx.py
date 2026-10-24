#!/usr/bin/env python3
"""reference.docx for the Word export: Pandoc's default reference document plus the styles of the named blocks.

    python3 scripts/make_reference_docx.py            # rewrites reference.docx in place
    python3 scripts/make_reference_docx.py IN OUT     # from another base

Pandoc maps `::: {custom-style="X"}` to the Word paragraph style X and a block quote to "Block Text" (already in
Pandoc's reference). Added on top of Body Text: Epigraph (indented, italic, smaller), Verse (indented, lines kept),
Attribution (flush right, smaller), Box (light tint, indented both sides). A style already there is set again to the
same values, so running the script twice changes nothing. The zip is written with fixed dates and the original
member order, so the same input always gives the same bytes."""
import io
import os
import sys
import zipfile

import docx
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STYLES = {
    "Epigraph": dict(left_indent=Cm(2), right_indent=Cm(1), italic=True, size=Pt(10), space_after=Pt(4)),
    "Verse": dict(left_indent=Cm(1.5), space_after=Pt(8)),
    "Attribution": dict(alignment=WD_ALIGN_PARAGRAPH.RIGHT, right_indent=Cm(1), size=Pt(10), space_after=Pt(12)),
    "Box": dict(left_indent=Cm(0.5), right_indent=Cm(0.5), space_after=Pt(6), fill="F2EFE8"),
}


def build(src, dst):
    d = docx.Document(src)
    base = d.styles["Body Text"]
    names = {s.name for s in d.styles}
    for name, props in STYLES.items():
        st = d.styles[name] if name in names else d.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        st.base_style, st.next_paragraph_style, st.quick_style = base, base, True
        for k, v in props.items():
            if k == "italic":
                st.font.italic = v
            elif k == "size":
                st.font.size = v
            elif k == "fill":
                ppr = st.element.get_or_add_pPr()
                for old in ppr.findall(qn("w:shd")):
                    ppr.remove(old)
                shd = OxmlElement("w:shd")
                shd.set(qn("w:val"), "clear")
                shd.set(qn("w:color"), "auto")
                shd.set(qn("w:fill"), v)
                ppr.append(shd)
            else:
                setattr(st.paragraph_format, k, v)
    buf = io.BytesIO()
    d.save(buf)
    # same bytes for the same content: fixed member dates, order kept
    zin = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zi = zipfile.ZipInfo(info.filename, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type, zi.external_attr = zipfile.ZIP_DEFLATED, 0o644 << 16
            zout.writestr(zi, zin.read(info.filename))
    open(dst, "wb").write(out.getvalue())


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "reference.docx")
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "reference.docx")
    build(src, dst)
