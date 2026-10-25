# Backlog

Agreed, not built yet. Short items; add new ones here, remove them when they ship.

## Importer

- **Index out of the text like the contents**: found by structure (alphabetical entries, lists of page numbers),
  kept as skipped with the pending import, restorable from the review screen (today it is one untranslated
  placeholder segment).
- **Tuning set: endnotes grouped under part and chapter headings**: no tuning book has them; when one comes, it
  joins the set.
- **Tables without rules** (e.g. Characters, appendix A) are read as text today.
- **Figures as files**: export images instead of placeholders; vector diagrams too.
- **Far Calls**: a paragraph indent drawn as a leading space is not seen as a new paragraph.
- **The 2 wrongly kept line-end hyphens** (in-authenticity, pseudo-dementia): ask them as a review-screen question.
- **OCR engine for scanned PDFs**: Apple Vision first, then Tesseract, behind an engine-independent interface.
- **AI role suggestion** for doubtful blocks, shown as the first answer on the review screen.
- **Epigraph/verse edge cases**: a prose wrap where only one line reaches the right edge keeps its hard break; prose
  mistaken for verse (Characters' cataloguing page) keeps its line breaks.

## Editor / UI

- **Review screen, "örnekleri gör":** when "Kitapta aynı görünen N yere de uygula" has a large N (Sazima: 103 block
  quotes), a link on the question card listing those places' pages with small previews of the first few. The box is
  checked by default, so nobody should change 103 places without seeing them.
- **Merge selection colour** clashes with the rail's "buradasın" red.
- **Tables**: the search highlight disappears; a line break inside a cell turns into a space.
- **Export from the project list**: the format choices the workspace has.
- **Truncation check in /translate_simple**: a cut-off answer is not caught.
- **Part role inside a project**: "Kısım" (and "Dipnot", "Son not") are review-screen roles only; the editor's role
  menu has no note role (it would need a callout to tie the note to) and no part role (it would need {.kisim} /
  {.bolum} on the book's headings).
- **Glossary filter speed**: about 660 ms on a long book.
- **Search bar and rail overflow** on narrow windows.

## Export

- **Contents field in Word** (Pandoc --toc or a Word TOC field), built from the translated headings.
- **Endnotes in Word**: notes as Word endnotes, not footnotes, when the book has them.

## Distribution

- **start.bat** has not run on a real Windows machine yet.

## Long term (not planned, a note)

- **A publishing-house kitchen (SaaS)**: many users with roles (manager, translator, editor, proofreader) and a
  progress bar per role.
