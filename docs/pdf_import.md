# pdf_import: PDF → markdown

`pdf_import.py` turns a book PDF into the markdown the app segments and translates. It reads the PDF with
pypdfium2 only (no PyMuPDF, no model, no OCR) and decides everything from the document itself: there are no
per-book constants. Every tuned number is in `THRESHOLDS` at the top of the file (table below).

## Stages

Each stage is a section of `pdf_import.py`; its input and output are written at the section's head.

| # | Stage | In | Out |
|---|---|---|---|
| 1 | Reading (`_page_chars`, `_dedupe`, `_tint_boxes`, `_page_graphics`) | a pdfium page | `Ch` glyph records (top-left origin, effective size, advance box, italic/bold, turned), tinted boxes, images, rules, scan signals |
| 2 | Lines (`build_lines`, `line_tokens`, `inline_md`, `_prepare_line`) | glyphs of a page | lines: rows by baseline, split at jumps; word spaces inferred; `md`, `plain`, style mix |
| 3 | Blocks (`build_blocks`) | lines, boxes, images, rules | page dict: blocks (paragraph breaks by gap, indent, list item, style change unless inline), two-column order, figures and ruled tables cut out as items |
| 4 | Margins (`_Margins`) | all pages | `sil` on running heads/feet, folios, slugs, watermarks; the text block (`text_top`/`text_bot`); covers; tiny tinted panels stop being boxes |
| 5 | Objects (`find_grids`, `grid_markdown`, `_Objects`) | rules, glyphs; items and roles | tables as markdown or a placeholder; blocks above/inside/below a figure or table become its captions (`altyazı`) |
| 6 | Roles (`_Styles`, `_Roles`, `_ShapeRoles`) | blocks with margins marked | style statistics → heading tiers, body, lists, boxes, front matter, index; shape → epigraph, block quote, verse, attribution; reasons in `flags`, a confidence in `conf` |
| 7 | Merges (`_Merges`, `join_hyphen`, `join_lines`) | lines of the book; texts to join | hyphen lexicon; joins inside blocks, across page breaks, around objects; a hyphen kept only when the book writes the compound |
| 8 | Writing (`build_markdown`, `_analyse`) | `Doc`, the page range | markdown, warnings (Turkish), `seg_meta` (one entry per segment) |

Stages 4–7 run over the whole book in `Doc.__init__`. One step runs out of order: margin lines that repeat a
heading (stage 4) need the heading roles of stage 6 first. The whole document is always analysed; only the
requested pages are written.

## Roles and what they become in the markdown

| Role | Markdown | Segment type (segtypes) |
|---|---|---|
| `kısım`, `bölüm` | `# Title` | heading 1 |
| `alt başlık 1` | `## Title` | heading 2 |
| `alt başlık 2` | `### Title` | heading 3 |
| `gövde` | paragraph | para |
| `kutu` | `::: {custom-style="Box"}` paragraph | box |
| `kutu-başlık` | Box fence, bold | box |
| `liste` | `1. text` (one segment per item) | para |
| `epigraf` | `::: {custom-style="Epigraph"}` (lines kept with `\` when set as a poem) | epigraph |
| `alıntı` | `> text` | blockquote |
| `şiir` | `::: {custom-style="Verse"}`, `\` after each line but the last | verse |
| `atıf` | `::: {custom-style="Attribution"}` | attribution |
| `altyazı` | `::: {custom-style="Caption"}` around its figure/table | caption |
| `dizin` | one placeholder `![Dizin · s.a–b](…)` | image |
| `ön sayfa`, `sil` | left out (counted in the warnings) | — |

Figures become `![Görsel · s.N](…)`, tables a markdown table or `![Tablo · s.N](…)` when cells are merged.
Named roles carry a confidence in `seg_meta` (0.5 style only, 0.6–0.7 shape, 0.8–0.9 shape and position);
these are fixed values in `_shape_roles`, not thresholds. A deviation no rule names keeps the review flag
`stil-farkli` together with its reasons (`indent`, `indent-right`, `narrow`, `centered`, `right`, `ragged`,
`italic-block`, `smaller`, `font`, `hanging`).

## Thresholds

Generated from `THRESHOLDS` in `pdf_import.py` (keep the two in step: the code is the source). "round 0" is the
first version of the importer; later rounds are the karne rounds, with the test book that called for the value.

| Name | Value | Why | Round / book |
|---|---|---|---|
| `rotated_b` | 0.1 | matrix b above this share of a+d: the glyph is turned (watermark, edge tab); c alone is a faux-italic shear | round 5, Characters |
| `bold_weight` | 600 | font weight from which a glyph counts as bold | round 0 (first version) |
| `hidden_text_pt` | 3 | glyphs under this size are hidden text (running-head variables on chapter openers) | round 8, Attachment |
| `layer_repeat_pt` | 0.3 | same glyph within this distance = drawn twice (second text layer over a watermark) | round 8, John Reveal |
| `layer_repeat_gap` | 3 | ...only when this far on in the stream; ligature letters (ff) follow each other | round 8, NEI/Lonely (ff) |
| `tint_lum_min` | 150 | fill luminance range of a tinted box: darker is a picture | round 0 |
| `tint_lum_max` | 248 | ...lighter is the page itself | round 0 |
| `tint_min_area` | 1500 | smallest tinted box (pt^2) | round 0 |
| `tint_max_share` | 0.9 | a fill wider/taller than this share of the page is a background | round 0 |
| `tint_max_chroma` | 30 | grey-ish fills only (max-min of RGB) | round 0 |
| `fullpage_image_share` | 0.8 | an image covering this share of the page is a scan or background, not a figure | round 1 |
| `image_min_side` | 25 | smaller images are ornaments, not figures | round 2 |
| `rule_straight` | 0.6 | a path piece within this much of a line is a straight rule | round 2 |
| `hrule_min_len` | 10 | shortest horizontal rule | round 2 |
| `vrule_min_len` | 6 | shortest vertical rule | round 2 |
| `scan_invisible_ratio` | 9 | invisible text objects at least this many times the visible ones under a full-page image: a scanned page | round 1, Trauma |
| `scan_page_share` | 0.5 | share of text pages that look scanned for the whole book to count as scanned | round 1, Trauma |
| `ornament_glyphs` | 30 | an ornament-named font with fewer glyphs in the book is decoration | round 1 |
| `min_chars_page` | 20 | fewer characters per selected page: no text layer | round 0 |
| `min_chars_total` | 400 | ...and fewer in total | round 0 |
| `min_md_words` | 20 | fewer words written: the pages hold only furniture | round 0 |
| `default_body_pt` | 10.0 | body size when a page or book has no text to measure | round 0 |
| `dropcap_min` | 2.5 | a letter this many times the page's median size is an initial | round 0 |
| `dropcap_max` | 12 | ...up to this many times (an ornamental initial can be 8x; beyond 12 it is art) | round 8, John Reveal |
| `dropcap_alone_base` | 0.3 | big letters closer than this (x size) on the baseline are a title, not an initial | round 5, Girl |
| `dropcap_alone_x` | 2 | ...within this many sizes sideways | round 5, Girl |
| `dropcap_x_slack` | 1 | a line may start this far left of the initial | round 0 |
| `dropcap_top_em` | 0.2 | lines whose top is within this share of the initial's size (+ dropcap_top_pt) sit beside it | round 0 |
| `dropcap_top_pt` | 3 | ...plus this many points | round 0 |
| `dropcap_line_top` | 0.5 | the line beside the initial's top: within this share of its size | round 8, John Reveal |
| `row_base_pt` | 1.2 | glyphs within this many points (or row_base_em) of a baseline share a row | round 0 |
| `row_base_em` | 0.18 | ...or this share of their size | round 0 |
| `script_size` | 0.8 | a row smaller than this share of a neighbour can be its super/subscript | round 0 |
| `script_slack` | 1 | horizontal slack for a script row inside its base row | round 0 |
| `script_reach` | 0.9 | a script row within this many base sizes of its base row folds into it | round 0 |
| `script_reach_tiny` | 1.1 | ...this many for rows of superscript digits only | round 0 |
| `ligature_box_em` | 0.05 | chars whose box edges agree within this share of the size are one ligature glyph | round 5, Girl |
| `seg_gap_em` | 2.5 | a forward jump wider than this many sizes (and seg_gap_pt) splits a row into segments | round 0 |
| `seg_gap_pt` | 18 | ...and wider than this many points | round 0 |
| `seg_back_em` | 0.9 | a backward jump of this many sizes splits a row (content-stream order) | round 0 |
| `tracked_min_glyphs` | 4 | fewest glyphs to judge letter-spacing | round 1 |
| `tracked_gap_em` | 0.08 | median letter gap above this share of the size: a tracked (letter-spaced) line | round 1 |
| `tracked_split_em` | 0.15 | word and letter gap clusters this far apart (x size) | round 1 |
| `tracked_ratio` | 1.8 | ...and the word gaps this many times the letter gaps | round 1 |
| `tracked_floor_em` | 0.02 | floor for the letter gap in that ratio | round 1 |
| `fake_space_em` | 0.2 | a space glyph starting within this many sizes per ligature letter after the ligature is no word break | round 5, Girl/Lonely |
| `fake_space_gap_em` | 0.08 | ...when the next letter follows the ligature within this share of the size | round 5, Girl |
| `caps_share` | 0.8 | letters at least this share upper case: an ALL-CAPS line/block | round 0 |
| `space_caps_em` | 0.5 | inferred word space in ALL-CAPS lines with real spaces | round 0 |
| `space_em` | 0.2 | inferred word space otherwise | round 0 |
| `gen_space_em` | 0.06 | pdfium's generated space counts when the gap stands this far out of the line's usual gap | round 5, fourth_trimester |
| `page_slack` | 2 | text this far outside the page box still belongs to it | round 2 |
| `box_slack` | 1 | slack for a line or glyph inside a box, figure or table | round 0 |
| `col_left` | 0.4 | two columns: a left-column line starts left of this share of the width | round 2 |
| `col_right` | 0.5 | ...a right-column line starts right of this share | round 2 |
| `col_slack` | 1 | a right-column line may end this far past the page edge | round 2 |
| `col_word_len` | 3 | a right-hand line needs a word this long (page numbers are no column) | round 5, Characters |
| `col_base_pt` | 2 | left and right lines on the same baseline within this many points | round 2 |
| `col_min_rows` | 4 | fewest side-by-side rows for a two-column page | round 2 |
| `col_max_size` | 20 | lines from this size up (titles) are left out of the two-column test | round 2 |
| `gutter_lo` | 0.3 | the gutter is searched from this share of the width (to 0.7) | round 2 |
| `gutter_slack` | 1 | a line crossing the gutter by less than this does not cross it | round 2 |
| `fig_title_width` | 0.35 | a line this wide (share of the figure) along its edge is a title, not a label | round 2 |
| `fig_title_band` | 0.15 | ...within this share of the figure's height from its top or bottom | round 2 |
| `fig_label_words` | 40 | more words inside a picture is text set on a background, not labels | round 2 |
| `pitch_max_em` | 2.2 | line pitch is measured between lines closer than this many sizes | round 0 |
| `default_pitch_em` | 1.3 | line pitch when a style has none measured | round 0 |
| `para_gap` | 1.45 | a gap over this many line pitches starts a new block | round 0 |
| `overlap_em` | 0.3 | a line closer than this (x size) above-left overlaps: new block | round 0 |
| `item_min_chars` | 3 | a list number needs this much text after it to start an item | round 0 |
| `indent_em` | 0.7 | a first-line indent of this many sizes starts a paragraph | round 0 |
| `list_slack` | 1 | a list item's continuation may start this far left of the item | round 0 |
| `full_line_words` | 6 | lines with this many words define the column's right edge | round 6, Characters |
| `full_line_em` | 1.5 | a line ending within this many sizes of the right edge is full | round 6, Characters |
| `inline_size_pt` | 0.25 | an inline run (italic title, bold lead-in) is this close in size | round 6, fourth_trimester |
| `indent_same_pt` | 1 | two lines with the same indent within this many points | round 7, Characters (Angelou) |
| `indented_full_em` | 3 | an indented line reaching within this many sizes of the right edge runs on | round 7, Characters |
| `body_size_pt` | 0.6 | a size within this many points of the body size counts as body size | round 1 |
| `body_line_words` | 3 | a body line for the text block has this many words | round 1 |
| `page_body_lines` | 3 | a page needs this many body lines to mark the text block | round 1 |
| `repeat_pages` | 3 | text recurring on this many pages is a running head / slug | round 1 |
| `full_measure` | 0.9 | a recurring line shorter than this share of the measure is a head, not body | round 8, Attachment |
| `half_measure` | 0.5 | lines narrower than this share of the measure do not mark the top edge; wider body-size lines are body | round 5/8, Characters/Attachment |
| `zone_slack` | 0.5 | a line this far outside the text block is in the margin | round 1 |
| `band_top` | 0.09 | top band of the page (share of its height) | round 1 |
| `band_bot` | 0.86 | bottom band starts here | round 1 |
| `pos_cell_pt` | 4 | spot (top position) grid for the band rule | round 1 |
| `band_min_pages` | 4 | a style at one spot on this many pages (and band_share) is a watermark/footer | round 1 |
| `band_share` | 0.35 | ...or this share of the pages | round 1 |
| `margin_size` | 1.05 | margin text is at most this many times the body size | round 1 |
| `folio_words` | 9 | a running head with a page number has at most this many words | round 6, Characters |
| `cover_width` | 0.2 | a page this much wider or narrower than the median is a cover/spread | round 1 |
| `box_min_words` | 15 | a tinted shape with fewer words is a figure panel, not a box | round 1 |
| `title_head_pages` | 2 | a margin line repeating a heading on this many pages is a running head | round 6, Characters |
| `grid_slack` | 2 | rule ends and crossings meet within this many points | round 2 |
| `grid_merge_pos` | 3 | rule positions closer than this are one rule | round 2 |
| `grid_min_rules` | 3 | a table has at least this many rules each way (2 x 2 cells) | round 2 |
| `grid_frame_span` | 0.9 | the outer frame must cover this share of each side | round B, NEI (badge) |
| `grid_cell_pad` | 1 | a cell border is looked for this far inside the cell | round 2 |
| `rule_collinear` | 1.2 | rule pieces this close in position are collinear | round 2 |
| `rule_join` | 2.5 | collinear pieces this close end to end are one rule | round 2 |
| `text_fam_share` | 0.05 | a font family with this share of all words is a text style, never flagged | round A |
| `object_gap_em` | 2.5 | blocks within this many body sizes of a figure/table belong to it | round A |
| `caption_max_words` | 80 | a caption block has at most this many words | round A |
| `caption_max_lines` | 6 | ...and this many lines | round A |
| `object_slack` | 1 | horizontal overlap slack between a block and an object | round A |
| `flag_band_top` | 0.12 | a set-apart block ending above this share of the page is in the margin band | round 6 |
| `italic_share` | 0.6 | a block/line this much italic is an italic block | round 0 |
| `short_lines` | 3 | a short block: at most this many lines | round 0 |
| `short_chars` | 120 | ...and this many characters | round 0 |
| `score_caps` | 2.0 | heading rank bonus for ALL CAPS | round 0 |
| `score_bold` | 0.3 | heading rank bonus for bold | round 0 |
| `front_max` | 8 | front matter: at most this many first pages | round 1 |
| `front_min` | 2 | ...at least this many | round 1 |
| `front_share` | 0.03 | ...this share of the book | round 1 |
| `body_style_share` | 0.15 | a style with this share of the main style's words is a body style | round A |
| `body_style_lines` | 3 | ...when its blocks run to this many lines | round A |
| `poem_ml` | 0.6 | poem-like style: this share of lines in multi-line blocks | round 0 |
| `poem_avgl` | 1.5 | ...this many lines per block | round 0 |
| `poem_size_lo` | 0.9 | ...size from this share of the body | round 0 |
| `poem_size_hi` | 1.3 | ...to this share | round 0 |
| `poem_ital` | 0.5 | ...this share of italic blocks | round 0 |
| `poem_sent` | 0.95 | ...fewer than this share ending a sentence | round 0 |
| `ornament_num_size` | 1.5 | a letterless style this many times the body size is an ornamental numeral | round 0 |
| `variant_blocks` | 0.2 | a body variant style with at most this share of the body's blocks stays body | round 0 |
| `style_rule_share` | 0.6 | dash/box/number styles: this share of the style's blocks | round 0 |
| `short_share` | 0.9 | ...and this share of short blocks | round 0 |
| `front_style_words` | 150 | a front-pages-only style has at most this many words | round 1 |
| `front_para_blocks` | 2 | ...unless it sets paragraphs (this many blocks | round 1 |
| `front_para_lines` | 3 | ...of this many lines | round 1 |
| `front_para_words` | 100 | ...and this many words) | round 1 |
| `front_leftover_words` | 15 | an unmatched front-page style with at most this many words is front matter | round 1 |
| `emph_size` | 0.95 | a style at least this share of the body size is emphatic enough for a heading | round 0 |
| `emph_caps` | 0.5 | ...or this share of ALL-CAPS blocks | round 0 |
| `display_size` | 1.4 | display type (this many times the body) may run longer as a heading | round 5, Characters (profiles) |
| `heading_min_size` | 0.6 | a heading is at least this share of the body size | round 1 |
| `marginal_share` | 0.5 | a small style mostly (this share) in the margin is a running head, not a heading | round 1 |
| `head_min_blocks` | 5 | a heading style has at least this many blocks | round 0 |
| `head_min_pages` | 3 | ...on this many pages | round 0 |
| `head_min_chars` | 4 | ...this many characters per block | round 0 |
| `head_short_display` | 0.8 | ...this share of short blocks for display type | round 5, Characters |
| `head_words` | 12 | ...at most this many words per block | round 0 |
| `head_words_display` | 20 | ...this many for display type | round 5, Characters |
| `head_sent` | 0.5 | ...fewer than this share ending a sentence | round 0 |
| `head_inbox` | 0.5 | ...fewer than this share inside boxes | round 0 |
| `head_min_words` | 8 | ...at least this many words in all | round 0 |
| `head_num` | 0.3 | ...fewer than this share of numbered blocks | round 0 |
| `tier_gap` | 0.5 | heading styles whose ranks differ by at most this share a level | round 0 |
| `variant_size_pt` | 0.25 | a body variant font is this close to the body size | round 5, Characters |
| `variant_pitch` | 0.05 | ...its own paragraphs at the body pitch within this share | round 5, Characters |
| `variant_multiline` | 0.2 | ...this share of its blocks multi-line | round 5, Characters |
| `variant_sent` | 0.5 | ...ending a sentence at least this share as often as body | round 5, Characters |
| `variant_flagged` | 0.8 | a run is italic/bold-flagged when this share of its glyphs are | round 5 |
| `variant_lines` | 10 | a font set inside this many body lines is a body variant | round 5, Characters |
| `variant_lines_flagged` | 3 | ...this many when flagged italic/bold | round 5 |
| `title_page_size` | 1.3 | a front page whose blocks are all this many times the body is a title page | round 5, fourth_trimester/Girl |
| `runs_para_lines` | 3 | a heading-style block longer than this many lines | round 6, Characters |
| `runs_para_words` | 30 | ...and this many words | round 6, Characters |
| `runs_body_share` | 0.15 | ...with this share of body glyphs is a paragraph, not a heading | round 6, Characters |
| `poem_one_line_words` | 8 | a one-line poem-style block this short may be a heading | round 0 |
| `epigraph_lines` | 4 | a poem-style block up to this many lines is an epigraph (style rule) | round 0 |
| `attrib_lines` | 2 | an attribution (dash line) has at most this many lines | round 0 |
| `oneoff_lines` | 2 | a one-off heading has at most this many lines | round 1 |
| `oneoff_words` | 10 | ...and this many words | round 1 |
| `oneoff_caps_size` | 0.75 | an ALL-CAPS one-off heading may be this small (share of body) | round 1 |
| `box_title_lines` | 2 | a box title has at most this many lines | round 0 |
| `net_col_em` | 2 | heading net: a heading within this many body sizes of the open block's left edge | round 6, Characters |
| `net_overlay_pt` | 1 | ...starting below it (overlap up to this many points) | round 6, fourth_trimester |
| `net_gap_em` | 0.6 | ...with less than this many body sizes of space above | round 6, Characters |
| `index_start` | 0.75 | an index sits in the last quarter of the book | round 2 |
| `index_min_lines` | 15 | an index page has this many lines | round 2 |
| `index_num_share` | 0.5 | ...this share ending in page numbers | round 2 |
| `index_entry_len` | 50 | ...median entry length at most this | round 2 |
| `index_min_alpha` | 5 | ...this many alphabetical entry starts | round 2 |
| `index_order` | 0.7 | ...this share in alphabetical order | round 2 |
| `index_grow_lines` | 3 | a neighbouring index page needs this many entry lines | round 2 |
| `column_min_lines` | 3 | a page's body column needs this many measured lines | round 7 |
| `column_fallback_l` | 0.12 | column left edge when nothing is measured (share of width) | round 7 |
| `column_fallback_r` | 0.88 | ...right edge | round 7 |
| `align_em` | 0.8 | centered/flush-right/justified alignment within this many sizes | round 7 |
| `center_min_em` | 1.5 | a centered line is indented at least this many sizes | round 7 |
| `right_min_share` | 0.3 | a flush-right line starts right of this share of the column | round 7 |
| `indent_shape_em` | 0.6 | an indented block's lines sit this many body sizes in (body lines are flush) | round 7, Characters (Angelou) |
| `first_indent_em` | 1.2 | a one-line block indented this much beyond the usual first-line indent | round 7 |
| `right_indent_em` | 2.5 | a right indent of this many body sizes alone sets a block apart | round 7 |
| `narrow` | 0.75 | a multi-line block narrower than this share of the column | round 7 |
| `ragged` | 0.2 | line-length spread (stdev/mean) above this: ragged lines | round 7 |
| `body_italic_max` | 0.3 | italic blocks set apart only when body text is less italic than this | round 7 |
| `epigraph_blocks` | 4 | an epigraph run has at most this many blocks | round 7, fourth_trimester |
| `epigraph_block_words` | 60 | ...each at most this many words | round 7, fourth_trimester |
| `epigraph_words` | 150 | ...this many in all | round 7 |
| `attrib_max_lines` | 3 | an attribution has at most this many lines | round 7 |
| `attrib_max_words` | 25 | ...and this many words | round 7 |
| `attrib_italic_words` | 6 | a few italic words after a quote ("Female, 16-17") are its source | round 7, Lonely |
| `attrib_head_lines` | 2 | a heading-style attribution has at most this many lines | round 8, Attachment |
| `attrib_head_words` | 20 | ...and this many words | round 8, Attachment |
| `attrib_edge_pt` | 1.5 | the work's title line ends within this many points of the attribution | round 7, Characters (Angelou) |
| `attrib_gap_em` | 1.5 | ...and follows within this many sizes | round 7, Characters |
| `verse_min_lines` | 3 | verse has at least this many lines | round 7 |
| `verse_short_line` | 0.9 | an epigraph line shorter than this share of the longest | round 7, fourth_trimester |
| `verse_short_share` | 0.5 | ...in this share of its lines keeps its line breaks | round 7, fourth_trimester |
| `continue_x_pt` | 1.5 | a quote/verse goes on at the same left edge within this many points | round 7, Far Calls |
| `continue_size_pt` | 0.3 | ...and this close in size | round 7, Far Calls |
| `overlap_pt` | 1 | blocks overlapping by more than this are drawn over each other | round 8, John Reveal |
| `item_order_pt` | 1 | a figure/table above a block by more than this is written before it | round 2 |
| `stray_chars` | 2 | a block or heading of this few letters is stray lettering | round 1 |
| `stray_tokens` | 6 | ...or up to this many one-letter tokens | round 1 |
| `head_wrap_em` | 2.6 | a heading continues on the next line within this many sizes | round 1 |
| `rotated_warn` | 20 | warn about turned text from this many glyphs | round 1 |

## Known limits

- Segmentation splits on blank lines only (an app decision, not the importer's).
- No OCR: a scanned PDF needs a text layer. In some OCR layers pdfium drops the last letter of most words
  (Trauma); the importer warns, it cannot repair.
- A paragraph indent drawn as a leading space glyph (Far Calls) is not seen as a new paragraph.
- Italic and bold come from font flags and names; subset fonts without either (Characters) lose their emphasis
  marks, though the text stays.
- Verse with long, even lines can come out as a block quote with its lines joined.
- Two-column layout is detected per page by side-by-side rows; more than two columns is not handled.
- Tables are recognised only when ruled; borderless tables stay text (an appendix list in Characters becomes
  short paragraphs and stray headings).
- The index is looked for in the last quarter of the book only.
- Unreachable-in-practice branches are kept as safety nets: a control-character glyph, an empty ruled grid, a
  book without styles (`_suggest`), no measurable text block (falls back to page bands), an unknown role in
  `build_markdown` (written as plain text), password-protected or empty PDFs.

## Measuring: the karne (report card)

The karne lives outside the repo (`~/palimpsest-spike/karne`) because it reads the test books, whose text must
never enter the repo (the pre-commit check `scripts/check_book_text.py` stops it).

- `run_karne.sh <label>` imports every tuning book with the repo's `pdf_import`, compares it with a PyMuPDF
  ground truth (`books/<book>/gt.json`, made once by `karne_gt.py` in the spike venv; PyMuPDF never enters the
  app) and writes `runs/<label>/<book>/{kitap.md, import.json, rapor.json, rapor.md}`.
- `karne_table.py <label_a> <label_b>` prints the a → b table (alignment, losses, leaks, splits, roles, …).
- Holdout books (`~/palimpsest-spike/karne-holdout`) are only measured, never inspected or used to set a rule.
- A change to the importer that should not change behaviour is checked byte for byte: markdown, warnings and
  `seg_meta` of every book before and after must be identical.
