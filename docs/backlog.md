# Backlog

Agreed, not built yet. Short items; add new ones here, remove them when they ship.

## Importer

- **Part and chapter roles** (`{.kisim}` / `{.bolum}`): kept apart in the markdown, a level of their own in the rail
  and in Word.
- **Tables without rules** (e.g. Characters, appendix A) are read as text today.
- **Figures as files**: export images instead of placeholders; vector diagrams too.
- **Far Calls**: a paragraph indent drawn as a leading space is not seen as a new paragraph.
- **Endnote matching is weak on the holdout** (measured only). Never look at the holdout: the tuning set needs a new
  book with endnotes.
- **The 2 wrongly kept line-end hyphens** (in-authenticity, pseudo-dementia): ask them as a review-screen question.
- **OCR engine for scanned PDFs**: Apple Vision first, then Tesseract, behind an engine-independent interface.
- **AI role suggestion** for doubtful blocks, shown as the first answer on the review screen.

## Editor / UI

- **Review screen, "örnekleri gör":** when "Kitapta aynı görünen N yere de uygula" has a large N (Sazima: 103 block
  quotes), a link on the question card listing those places' pages with small previews of the first few. The box is
  checked by default, so nobody should change 103 places without seeing them.
- **Merge selection colour** clashes with the rail's "buradasın" red.
- **Tables**: the search highlight disappears; a line break inside a cell turns into a space.
- **Export from the project list**: the format choices the workspace has.
- **Truncation check in /translate_simple**: a cut-off answer is not caught.
- **Glossary filter speed**: about 660 ms on a long book.
- **Search bar and rail overflow** on narrow windows.

## Export

- **Endnotes in Word**: notes as Word endnotes, not footnotes, when the book has them.

## Distribution

- **start.bat** has not run on a real Windows machine yet.

## Long term (not planned, a note)

- **A publishing-house kitchen (SaaS)**: many users with roles (manager, translator, editor, proofreader) and a
  progress bar per role.
