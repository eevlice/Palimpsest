"""
PDF -> markdown for the new-project screen. pypdfium2 only (BSD/Apache; no model, no OCR).

    convert(pdf_bytes, first_page=None, last_page=None) -> (markdown, warnings)
    convert_with_meta(...)                                -> (markdown, warnings, seg_meta)

The page range is 1-based, as printed by a PDF viewer. The whole document is always analysed (style statistics,
repeated text and the hyphen lexicon need many pages); only the requested pages are written out. A text layer is
required: a PDF without one raises PdfImportError.

The importer runs in stages; each section of this file is one stage, its input and output stated at its head:
  1. reading     pdfium page -> Ch glyph records (+ tinted boxes, images, rules, scan signals)
  2. lines       glyphs -> lines (rows by baseline, segments, word spaces, inline emphasis)
  3. blocks      lines -> blocks per page (paragraph breaks, two columns, figures and ruled tables cut out)
  4. margins     every page -> margin text marked "sil" (running heads, folios, slugs, watermarks), the text block
  5. objects     ruled grids -> tables; blocks around figures/tables -> their captions
  6. roles       style statistics -> heading tiers, body, lists, boxes, index, epigraph/quote/verse/attribution
  7. merges      the hyphen lexicon; line and paragraph joins (page breaks, same page, hyphens)
  8. writing     blocks -> markdown in the app's conventions + warnings + seg_meta
Stages 4-7 work on the whole document: class Doc runs them in order (see Doc.__init__).

Every tuned number lives in THRESHOLDS, with its reason and the round/book that set it. Everything is derived from
the document itself; there are no per-book constants. Design notes: docs/pdf_import.md.
"""


import re
import copy
import math
import ctypes
import statistics
import collections
import difflib
import functools
import gzip
import os
import unicodedata

import pypdfium2 as pdfium
import pypdfium2.raw as raw


# Every tuned number of the importer, with why it is what it is and the round (and test book) that set it.
# Sizes in points (pt), multiples of a font size (em), shares of a page/column/style, counts.
THRESHOLDS = {
    "rotated_b": 0.1,             # matrix b above this share of a+d: the glyph is turned (watermark, edge tab); c alone is a faux-italic shear - round 5, Characters
    "bold_weight": 600,           # font weight from which a glyph counts as bold - round 0 (first version)
    "hidden_text_pt": 3,          # glyphs under this size are hidden text (running-head variables on chapter openers) - round 8, Attachment
    "layer_repeat_pt": 0.3,       # same glyph within this distance = drawn twice (second text layer over a watermark) - round 8, John Reveal
    "layer_repeat_gap": 3,        # ...only when this far on in the stream; ligature letters (ff) follow each other - round 8, NEI/Lonely (ff)
    "tint_lum_min": 150,          # fill luminance range of a tinted box: darker is a picture - round 0
    "tint_lum_max": 248,          # ...lighter is the page itself - round 0
    "tint_min_area": 1500,        # smallest tinted box (pt^2) - round 0
    "tint_max_share": 0.9,        # a fill wider/taller than this share of the page is a background - round 0
    "tint_max_chroma": 30,        # grey-ish fills only (max-min of RGB) - round 0
    "fullpage_image_share": 0.8,  # an image covering this share of the page is a scan or background, not a figure - round 1
    "image_min_side": 25,         # smaller images are ornaments, not figures - round 2
    "rule_straight": 0.6,         # a path piece within this much of a line is a straight rule - round 2
    "hrule_min_len": 10,          # shortest horizontal rule - round 2
    "vrule_min_len": 6,           # shortest vertical rule - round 2
    "scan_invisible_ratio": 9,    # invisible text objects at least this many times the visible ones under a full-page image: a scanned page - round 1, Trauma
    "scan_page_share": 0.5,       # share of text pages that look scanned for the whole book to count as scanned - round 1, Trauma
    "textless_reject": 0.5,       # a scanned PDF with more than this share of pages without any text is refused - gaps 2
    "image_on_page": 0.5,         # a picture with less than this share of its area on the page is not on it - gaps 2, Zukav
    "fig_display_size": 1.2,      # a line inside a picture set this many times the page's body size is a title, not a label - gaps 2, Zukav
    "ocr_retry": 0.05,            # an OCR page with more unknown words than this share is read again with pdfminer - gaps 2, Trauma (0.38 pdfium, 0.03 pdfminer)
    "ocr_reject": 0.2,            # OCR text with more unknown words than this share is refused - gaps 2: digital books 0.01-0.09 (Far Calls, Latin and Greek), Trauma's broken layer 0.38
    "ornament_glyphs": 30,        # an ornament-named font with fewer glyphs in the book is decoration - round 1
    "min_chars_page": 20,         # fewer characters per selected page: no text layer - round 0
    "min_chars_total": 400,       # ...and fewer in total - round 0
    "min_md_words": 20,           # fewer words written: the pages hold only furniture - round 0
    "default_body_pt": 10.0,      # body size when a page or book has no text to measure - round 0
    "dropcap_min": 2.5,           # a letter this many times the page's median size is an initial - round 0
    "dropcap_max": 12,            # ...up to this many times (an ornamental initial can be 8x; beyond 12 it is art) - round 8, John Reveal
    "dropcap_alone_base": 0.3,    # big letters closer than this (x size) on the baseline are a title, not an initial - round 5, Girl
    "dropcap_alone_x": 2,         # ...within this many sizes sideways - round 5, Girl
    "dropcap_x_slack": 1,         # a line may start this far left of the initial - round 0
    "dropcap_top_em": 0.2,        # lines whose top is within this share of the initial's size (+ dropcap_top_pt) sit beside it - round 0
    "dropcap_top_pt": 3,          # ...plus this many points - round 0
    "dropcap_line_top": 0.5,      # the line beside the initial's top: within this share of its size - round 8, John Reveal
    "row_base_pt": 1.2,           # glyphs within this many points (or row_base_em) of a baseline share a row - round 0
    "row_base_em": 0.18,          # ...or this share of their size - round 0
    "script_size": 0.8,           # a row smaller than this share of a neighbour can be its super/subscript - round 0
    "script_slack": 1,            # horizontal slack for a script row inside its base row - round 0
    "script_reach": 0.9,          # a script row within this many base sizes of its base row folds into it - round 0
    "script_reach_tiny": 1.1,     # ...this many for rows of superscript digits only - round 0
    "ligature_box_em": 0.05,      # chars whose box edges agree within this share of the size are one ligature glyph - round 5, Girl
    "seg_gap_em": 2.5,            # a forward jump wider than this many sizes (and seg_gap_pt) splits a row into segments - round 0
    "seg_gap_pt": 18,             # ...and wider than this many points - round 0
    "seg_back_em": 0.9,           # a backward jump of this many sizes splits a row (content-stream order) - round 0
    "tracked_min_glyphs": 4,      # fewest glyphs to judge letter-spacing - round 1
    "tracked_gap_em": 0.08,       # median letter gap above this share of the size: a tracked (letter-spaced) line - round 1
    "tracked_split_em": 0.15,     # word and letter gap clusters this far apart (x size) - round 1
    "tracked_ratio": 1.8,         # ...and the word gaps this many times the letter gaps - round 1
    "tracked_floor_em": 0.02,     # floor for the letter gap in that ratio - round 1
    "fake_space_em": 0.2,         # a space glyph starting within this many sizes per ligature letter after the ligature is no word break - round 5, Girl/Lonely
    "fake_space_gap_em": 0.08,    # ...when the next letter follows the ligature within this share of the size - round 5, Girl
    "caps_share": 0.8,            # letters at least this share upper case: an ALL-CAPS line/block - round 0
    "space_caps_em": 0.5,         # inferred word space in ALL-CAPS lines with real spaces - round 0
    "space_em": 0.2,              # inferred word space otherwise - round 0
    "gen_space_em": 0.06,         # pdfium's generated space counts when the gap stands this far out of the line's usual gap - round 5, fourth_trimester
    "page_slack": 2,              # text this far outside the page box still belongs to it - round 2
    "box_slack": 1,               # slack for a line or glyph inside a box, figure or table - round 0
    "col_left": 0.4,              # two columns: a left-column line starts left of this share of the width - round 2
    "col_right": 0.5,             # ...a right-column line starts right of this share - round 2
    "col_slack": 1,               # a right-column line may end this far past the page edge - round 2
    "col_word_len": 3,            # a right-hand line needs a word this long (page numbers are no column) - round 5, Characters
    "col_base_pt": 2,             # left and right lines on the same baseline within this many points - round 2
    "col_min_rows": 4,            # fewest side-by-side rows for a two-column page - round 2
    "col_max_size": 20,           # lines from this size up (titles) are left out of the two-column test - round 2
    "gutter_lo": 0.3,             # the gutter is searched from this share of the width (to 0.7) - round 2
    "gutter_slack": 1,            # a line crossing the gutter by less than this does not cross it - round 2
    "fig_title_width": 0.35,      # a line this wide (share of the figure) along its edge is a title, not a label - round 2
    "fig_title_band": 0.15,       # ...within this share of the figure's height from its top or bottom - round 2
    "fig_label_words": 40,        # more words inside a picture is text set on a background, not labels - round 2
    "pitch_max_em": 2.2,          # line pitch is measured between lines closer than this many sizes - round 0
    "default_pitch_em": 1.3,      # line pitch when a style has none measured - round 0
    "para_gap": 1.45,             # a gap over this many line pitches starts a new block - round 0
    "overlap_em": 0.3,            # a line closer than this (x size) above-left overlaps: new block - round 0
    "item_min_chars": 3,          # a list number needs this much text after it to start an item - round 0
    "indent_em": 0.7,             # a first-line indent of this many sizes starts a paragraph - round 0
    "list_slack": 1,              # a list item's continuation may start this far left of the item - round 0
    "full_line_words": 6,         # lines with this many words define the column's right edge - round 6, Characters
    "full_line_em": 1.5,          # a line ending within this many sizes of the right edge is full - round 6, Characters
    "inline_size_pt": 0.25,       # an inline run (italic title, bold lead-in) is this close in size - round 6, fourth_trimester
    "indent_same_pt": 1,          # two lines with the same indent within this many points - round 7, Characters (Angelou)
    "indented_full_em": 3,        # an indented line reaching within this many sizes of the right edge runs on - round 7, Characters
    "body_size_pt": 0.6,          # a size within this many points of the body size counts as body size - round 1
    "body_line_words": 3,         # a body line for the text block has this many words - round 1
    "page_body_lines": 3,         # a page needs this many body lines to mark the text block - round 1
    "repeat_pages": 3,            # text recurring on this many pages is a running head / slug - round 1
    "full_measure": 0.9,          # a recurring line shorter than this share of the measure is a head, not body - round 8, Attachment
    "half_measure": 0.5,          # lines narrower than this share of the measure do not mark the top edge; wider body-size lines are body - round 5/8, Characters/Attachment
    "zone_slack": 0.5,            # a line this far outside the text block is in the margin - round 1
    "band_top": 0.09,             # top band of the page (share of its height) - round 1
    "band_bot": 0.86,             # bottom band starts here - round 1
    "pos_cell_pt": 4,             # spot (top position) grid for the band rule - round 1
    "band_min_pages": 4,          # a style at one spot on this many pages (and band_share) is a watermark/footer - round 1
    "band_share": 0.35,           # ...or this share of the pages - round 1
    "margin_size": 1.05,          # margin text is at most this many times the body size - round 1
    "folio_words": 9,             # a running head with a page number has at most this many words - round 6, Characters
    "cover_width": 0.2,           # a page this much wider or narrower than the median is a cover/spread - round 1
    "box_min_words": 15,          # a tinted shape with fewer words is a figure panel, not a box - round 1
    "title_head_pages": 2,        # a margin line repeating a heading on this many pages is a running head - round 6, Characters
    "grid_slack": 2,              # rule ends and crossings meet within this many points - round 2
    "grid_merge_pos": 3,          # rule positions closer than this are one rule - round 2
    "grid_min_rules": 3,          # a table has at least this many rules each way (2 x 2 cells) - round 2
    "grid_frame_span": 0.9,       # the outer frame must cover this share of each side - round B, NEI (badge)
    "grid_cell_pad": 1,           # a cell border is looked for this far inside the cell - round 2
    "rule_collinear": 1.2,        # rule pieces this close in position are collinear - round 2
    "rule_join": 2.5,             # collinear pieces this close end to end are one rule - round 2
    "text_fam_share": 0.05,       # a font family with this share of all words is a text style, never flagged - round A
    "object_gap_em": 2.5,         # blocks within this many body sizes of a figure/table belong to it - round A
    "caption_max_words": 80,      # a caption block has at most this many words - round A
    "caption_max_lines": 6,       # ...and this many lines - round A
    "object_slack": 1,            # horizontal overlap slack between a block and an object - round A
    "flag_band_top": 0.12,        # a set-apart block ending above this share of the page is in the margin band - round 6
    "italic_share": 0.6,          # a block/line this much italic is an italic block - round 0
    "short_lines": 3,             # a short block: at most this many lines - round 0
    "short_chars": 120,           # ...and this many characters - round 0
    "score_caps": 2.0,            # heading rank bonus for ALL CAPS - round 0
    "score_bold": 0.3,            # heading rank bonus for bold - round 0
    "front_max": 8,               # front matter: at most this many first pages - round 1
    "front_min": 2,               # ...at least this many - round 1
    "front_share": 0.03,          # ...this share of the book - round 1
    "body_style_share": 0.15,     # a style with this share of the main style's words is a body style - round A
    "body_style_lines": 3,        # ...when its blocks run to this many lines - round A
    "poem_ml": 0.6,               # poem-like style: this share of lines in multi-line blocks - round 0
    "poem_avgl": 1.5,             # ...this many lines per block - round 0
    "poem_size_lo": 0.9,          # ...size from this share of the body - round 0
    "poem_size_hi": 1.3,          # ...to this share - round 0
    "poem_ital": 0.5,             # ...this share of italic blocks - round 0
    "poem_sent": 0.95,            # ...fewer than this share ending a sentence - round 0
    "ornament_num_size": 1.5,     # a letterless style this many times the body size is an ornamental numeral - round 0
    "variant_blocks": 0.2,        # a body variant style with at most this share of the body's blocks stays body - round 0
    "style_rule_share": 0.6,      # dash/box/number styles: this share of the style's blocks - round 0
    "short_share": 0.9,           # ...and this share of short blocks - round 0
    "front_style_words": 150,     # a front-pages-only style has at most this many words - round 1
    "front_para_blocks": 2,       # ...unless it sets paragraphs (this many blocks - round 1
    "front_para_lines": 3,        # ...of this many lines - round 1
    "front_para_words": 100,      # ...and this many words) - round 1
    "front_leftover_words": 15,   # an unmatched front-page style with at most this many words is front matter - round 1
    "emph_size": 0.95,            # a style at least this share of the body size is emphatic enough for a heading - round 0
    "emph_caps": 0.5,             # ...or this share of ALL-CAPS blocks - round 0
    "display_size": 1.4,          # display type (this many times the body) may run longer as a heading - round 5, Characters (profiles)
    "heading_min_size": 0.6,      # a heading is at least this share of the body size - round 1
    "marginal_share": 0.5,        # a small style mostly (this share) in the margin is a running head, not a heading - round 1
    "head_min_blocks": 5,         # a heading style has at least this many blocks - round 0
    "head_min_pages": 3,          # ...on this many pages - round 0
    "head_min_chars": 4,          # ...this many characters per block - round 0
    "head_short_display": 0.8,    # ...this share of short blocks for display type - round 5, Characters
    "head_words": 12,             # ...at most this many words per block - round 0
    "head_words_display": 20,     # ...this many for display type - round 5, Characters
    "head_sent": 0.5,             # ...fewer than this share ending a sentence - round 0
    "head_inbox": 0.5,            # ...fewer than this share inside boxes - round 0
    "head_min_words": 8,          # ...at least this many words in all - round 0
    "head_num": 0.3,              # ...fewer than this share of numbered blocks - round 0
    "tier_gap": 0.5,              # heading styles whose ranks differ by at most this share a level - round 0
    "variant_size_pt": 0.25,      # a body variant font is this close to the body size - round 5, Characters
    "variant_pitch": 0.05,        # ...its own paragraphs at the body pitch within this share - round 5, Characters
    "variant_multiline": 0.2,     # ...this share of its blocks multi-line - round 5, Characters
    "variant_sent": 0.5,          # ...ending a sentence at least this share as often as body - round 5, Characters
    "variant_flagged": 0.8,       # a run is italic/bold-flagged when this share of its glyphs are - round 5
    "variant_lines": 10,          # a font set inside this many body lines is a body variant - round 5, Characters
    "variant_lines_flagged": 3,   # ...this many when flagged italic/bold - round 5
    "title_page_size": 1.3,       # a front page whose blocks are all this many times the body is a title page - round 5, fourth_trimester/Girl
    "kunye_density": 0.25,        # a front page whose text is this share digits and punctuation is a colophon - front round
    "front_verse_words": 8,       # kept front text whose lines all have at most this many words keeps its lines - front round
    "title_display": 2.0,         # a front line this many times the body is a title page's - front round, Attachment (dedication 1.3)
    "runs_para_lines": 3,         # a heading-style block longer than this many lines - round 6, Characters
    "runs_para_words": 30,        # ...and this many words - round 6, Characters
    "runs_body_share": 0.15,      # ...with this share of body glyphs is a paragraph, not a heading - round 6, Characters
    "poem_one_line_words": 8,     # a one-line poem-style block this short may be a heading - round 0
    "epigraph_lines": 4,          # a poem-style block up to this many lines is an epigraph (style rule) - round 0
    "attrib_lines": 2,            # an attribution (dash line) has at most this many lines - round 0
    "oneoff_lines": 2,            # a one-off heading has at most this many lines - round 1
    "oneoff_words": 10,           # ...and this many words - round 1
    "oneoff_caps_size": 0.75,     # an ALL-CAPS one-off heading may be this small (share of body) - round 1
    "box_title_lines": 2,         # a box title has at most this many lines - round 0
    "net_col_em": 2,              # heading net: a heading within this many body sizes of the open block's left edge - round 6, Characters
    "net_overlay_pt": 1,          # ...starting below it (overlap up to this many points) - round 6, fourth_trimester
    "net_gap_em": 0.6,            # ...with less than this many body sizes of space above - round 6, Characters
    "index_start": 0.75,          # an index sits in the book's last quarter - round 2
    "index_min_lines": 15,        # an index page has this many lines - round 2
    "index_num_share": 0.5,       # ...this share ending in page numbers - round 2
    "index_entry_len": 50,        # ...median entry length at most this - round 2
    "index_min_alpha": 5,         # ...this many alphabetical entry starts - round 2
    "index_order": 0.7,           # ...this share in alphabetical order - round 2
    "index_grow_lines": 3,        # a neighbouring index page needs this many entry lines - round 2
    "column_min_lines": 3,        # a page's body column needs this many measured lines - round 7
    "column_fallback_l": 0.12,    # column left edge when nothing is measured (share of width) - round 7
    "column_fallback_r": 0.88,    # ...right edge - round 7
    "align_em": 0.8,              # centered/flush-right/justified alignment within this many sizes - round 7
    "center_min_em": 1.5,         # a centered line is indented at least this many sizes - round 7
    "right_min_share": 0.3,       # a flush-right line starts right of this share of the column - round 7
    "indent_shape_em": 0.6,       # an indented block's lines sit this many body sizes in (body lines are flush) - round 7, Characters (Angelou)
    "first_indent_em": 1.2,       # a one-line block indented this much beyond the usual first-line indent - round 7
    "right_indent_em": 2.5,       # a right indent of this many body sizes alone sets a block apart - round 7
    "narrow": 0.75,               # a multi-line block narrower than this share of the column - round 7
    "ragged": 0.2,                # line-length spread (stdev/mean) above this: ragged lines - round 7
    "body_italic_max": 0.3,       # italic blocks set apart only when body text is less italic than this - round 7
    "epigraph_blocks": 4,         # an epigraph run has at most this many blocks - round 7, fourth_trimester
    "epigraph_block_words": 60,   # ...each at most this many words - round 7, fourth_trimester
    "epigraph_words": 150,        # ...this many in all - round 7
    "attrib_max_lines": 3,        # an attribution has at most this many lines - round 7
    "attrib_max_words": 25,       # ...and this many words - round 7
    "attrib_italic_words": 6,     # a few italic words after a quote ("Female, 16-17") are its source - round 7, Lonely
    "attrib_head_lines": 2,       # a heading-style attribution has at most this many lines - round 8, Attachment
    "attrib_head_words": 20,      # ...and this many words - round 8, Attachment
    "attrib_edge_pt": 1.5,        # the work's title line ends within this many points of the attribution - round 7, Characters (Angelou)
    "attrib_gap_em": 1.5,         # ...and follows within this many sizes - round 7, Characters
    "verse_min_lines": 3,         # verse has at least this many lines - round 7
    "verse_short_line": 0.9,      # an epigraph line shorter than this share of the longest - round 7, fourth_trimester
    "verse_short_share": 0.5,     # ...in this share of its lines keeps its line breaks - round 7, fourth_trimester
    "continue_x_pt": 1.5,         # a quote/verse goes on at the same left edge within this many points - round 7, Far Calls
    "continue_size_pt": 0.3,      # ...and this close in size - round 7, Far Calls
    "overlap_pt": 1,              # blocks overlapping by more than this are drawn over each other - round 8, John Reveal
    "item_order_pt": 1,           # a figure/table above a block by more than this is written before it - round 2
    "stray_chars": 2,             # a block or heading of this few letters is stray lettering - round 1
    "stray_tokens": 6,            # ...or up to this many one-letter tokens - round 1
    "head_wrap_em": 2.6,          # a heading continues on the next line within this many sizes - round 1
    "rotated_warn": 20,           # warn about turned text from this many glyphs - round 1
    "ref_size": 0.85,             # a note callout is set at most this share of the line's size - gaps 1, Far Calls (0.5)
    "ref_raise": 0.15,            # ...with its baseline raised by more than this share of the size - gaps 1, Attachment
    "ref_max_chars": 3,           # ...and at most this many digits or signs - gaps 1
    "ref_gap_em": 0.5,            # a callout row past a line's end starts within this many sizes of it - gaps 1, Far Calls
    "foot_size": 0.92,            # a page-bottom footnote is set at most this share of the body size - gaps 1
    "wrap_edge_em": 1,            # a verse line ending within this many sizes of the block's right edge reaches it (a prose wrap) - gaps 3, Characters
    "notes_label_words": 12,      # a plain line naming a group of notes has at most this many words - gaps 2
    "notes_seq_share": 0.7,       # a notes group counts when this share of its entries follow 1, 2, 3... - gaps 1
    "notes_label_head": 0.8,      # ...or, longer, when this share of its words are in one body heading - notes round, dog
    "orphan_share": 0.2,          # callouts left without a note above this share are a book-level problem: the review screen counts them, asks none - notes round
    "toc_front_pages": 30,        # a table of contents sits in the first this many pages ... - contents round
    "toc_front_share": 0.15,      # ...or this share of the book, whichever is more - contents round
    "toc_rows": 3,                # a contents page has at least this many rows ending in a page number - contents round
    "toc_row_share": 0.4,         # ...and they are at least this share of its rows (titles wrap) - contents round, dog
    "toc_order": 0.8,             # ...whose arabic numbers mostly (this share of pairs) do not go down - contents round
    "toc_page_max": 1.5,          # a page number is at most this many times the PDF's pages ("2009" is a year) - contents round
    "toc_row_em": 0.35,           # lines on baselines this close (x size) are one row - contents round
    "toc_indent_em": 0.8,         # entries starting this far apart (x size) are on different indents - contents round
    "toc_contain": 0.6,           # a title inside another counts when it is this share of its length - karne tur8
    "toc_similar": 0.82,          # ...or when the two are this similar - karne tur8
    "toc_level_share": 0.6,       # a contents level maps to the heading role most (this share) of its matched entries have - contents round
    "toc_entry_words": 25,        # a block that is a contents entry not set as a heading has at most this many words - contents round
    "orphan_min": 10,             # ...when there are at least this many; fewer are asked one by one - notes round, Trauma (1 of 3)
    "q_region_gap": 4,            # role/style questions in one chapter at most this many pages apart are one question when on one page or at one size (a reference list, a table, a page of figure text) - grouping round
    "q_attr_words": 15,           # a line under a quote, an epigraph or praise text read as its attribution has at most this many words - grouping round
    "q_short_words": 12,          # ...and short lines (at most this many words) of one style anywhere in the book are one question - grouping round
}


T = THRESHOLDS


def _front(n_pages):
    """how many first pages count as front matter: a share of the book, within bounds"""
    return min(T["front_max"], max(T["front_min"], int(T["front_share"] * n_pages)))


class PdfImportError(Exception):
    """Message is shown to the user as is (Turkish)."""


# ====================================================================================================================
# SHARED: patterns and small helpers used by several stages
# ====================================================================================================================


HEAD = {"kısım": 1, "bölüm": 2, "alt başlık 1": 3, "alt başlık 2": 4}   # internal depth
MD_LEVEL = {1: 1, 2: 1, 3: 2, 4: 3}                                    # '#' count written to the markdown
NUM = re.compile(r"^\s*\d+\.\s")
ITEM = re.compile(r"^\s*(\d+\.|[•◦▪●■])\s")      # a list item starts: numbered or bulleted
DASH = re.compile(r"^\s*[—–―]")
ORNAMENT_FONT = re.compile(r"dingbat|symbol|ornament|wingding|fleuron|picto", re.I)
FOLIO = re.compile(r"^\s*(\d{1,4}|[ivxlcdm]{1,7})\s+\S.*|.*\S\s+(\d{1,4}|[ivxlcdm]{1,7})\s*$")   # number + short text
PAGE_NO = re.compile(r"^[\s—–\-·•|]*(\d{1,4}|[ivxlcdm]{1,7})[\s—–\-·•|]*$", re.I)
OCR_PRODUCER = re.compile(r"abbyy|finereader|tesseract|omnipage|readiris|paper capture|clearscan|\bocr\b", re.I)
MINER_MISSING = (" Bu sayfaları yeniden okuyacak pdfminer.six kütüphanesi eksik: Palimpsest'i internete bağlıyken "
                 "başlatıcıyla (start.command / start.bat) yeniden aç, kütüphaneyi kurar; sonra yeniden içe aktar.")
SCANNED_WARNING = "Bu PDF taranmış görünüyor, metin güvenilir olmayabilir."
CAPTION = re.compile(r"^\s*(figure|fig\.|şekil|resim|görsel|plate|illustration|photo)\s*[\dIVX]", re.I)


def size_key(font, size):
    return f"{font}|{round(size * 2) / 2:g}"


def fam_of(style):
    f, s = style.split("|")
    return re.sub(r"-(BoldItalic|LightItalic|RegularItalic|Italic|Bold|Regular|Light|Medium|Semibold|ExtraBold|Roman|SmallCaps)$", "", f), float(s)


# a note callout in line text: REF_OPEN + the number or sign as printed + REF_CLOSE (private-use characters, never in
# a book); the writer turns it into [^label] or back into the plain number
REF_OPEN, REF_CLOSE = "\ue000", "\ue001"
REF = re.compile(REF_OPEN + "([^" + REF_CLOSE + "]*)" + REF_CLOSE)
# a note's mark is a number, a sign or a lowercase letter (a, b, ... aa); each kind is a series of its own. Signs in
# their usual order, doubled after the last (**, ††)
NOTE_SIGNS = "*†‡§‖¶"
NOTE_LETTERS = re.compile(r"([a-z])\1?")


def note_series(mark):
    """the label part of a note mark: "3" -> "3", "a" -> "a", "*" -> "s1", "‡" -> "s3", "**" -> "s7" """
    if mark.isdigit() or NOTE_LETTERS.fullmatch(mark):
        return mark
    return "s%d" % (NOTE_SIGNS.index(mark[0]) + 1 + len(NOTE_SIGNS) * (len(mark) - 1))
WORD = re.compile(r"[A-Za-z][A-Za-z’']*[A-Za-z]|[A-Za-z]")


@functools.lru_cache(maxsize=1)
def english_words():
    """SCOWL up to size 70 (data/SCOWL-LICENSE.txt): lowercase English words, inflections included"""
    with gzip.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "words_en.txt.gz"), "rt",
                   encoding="utf-8") as f:
        return frozenset(f.read().split())


def word_key(w):
    """a token as the word list has it: lowercase, straight apostrophe, no possessive 's"""
    w = w.replace("’", "'").lower()
    return w[:-2] if w.endswith("'s") else w


def unknown_words(text):
    """the words of text (two letters or more, or a/I) that are not English words"""
    E = english_words()
    return [t for t in WORD.findall(text) if (len(t) >= 2 or t in "aAI") and word_key(t) not in E]


def terminal(s):
    """a line that closes its sentence: a stop, or a closing quote or bracket (after a stop: ." .' ?” !) .")"""
    return bool(re.search(r"(?:[.!?…:;\"”’)\]»]|[.!?…][*_]*'+)[*_]*\s*$", REF.sub("", s)))


def _similar_key(text):
    """the letters of a line without its page-number-like words (numbers, words with digits, roman numerals), so a
    slug or footer that carries the page number compares equal from page to page"""
    return re.sub(r"[\W\d_]+", "", "".join(w.lower() for w in text.split()
                                         if not re.search(r"\d", w) and not re.fullmatch(r"[ivxlcdm]+\W*", w, re.I)))


def _roman(s):
    v = dict(i=1, v=5, x=10, l=50, c=100, d=500, m=1000)
    n = [v[c] for c in s.lower()]
    return sum(-a if a < b else a for a, b in zip(n, n[1:] + [0]))


# ====================================================================================================================
# 1. READING (pdfium)
#   in:  a pdfium page
#   out: [Ch] one record per glyph (top-left origin, effective size, advance box, flags);
#        tinted boxes, images, horizontal/vertical rules and scan signals of the page
# ====================================================================================================================


class Ch:
    __slots__ = ("idx", "ch", "ox", "lr", "x0", "x1", "top", "bot", "base", "font", "size", "weight",
                 "italic", "bold", "dropcap", "rot", "gsp")

    def __init__(self, ch, x0, x1, top, bot, base, font, size, weight=400, italic=False, bold=False,
                 idx=0, ox=None, lr=None, rot=False):
        self.idx, self.ch, self.x0, self.x1, self.top, self.bot, self.base = idx, ch, x0, x1, top, bot, base
        self.font, self.size, self.weight, self.italic, self.bold = font, size, weight, italic, bold
        self.ox = x0 if ox is None else ox          # glyph origin
        self.lr = x1 if lr is None else lr          # end of the advance box (loose box)
        self.dropcap = False
        self.rot = rot                              # set at an angle (diagonal watermark, vertical edge tab)
        self.gsp = False                            # pdfium generated a space right before this char


def _page_chars(page):
    """per-character records, top-left origin; effective size = FontSize * the matrix's height across the baseline
    (a slanted faux italic is no taller than its upright text)"""
    H = page.get_height()
    tpg = page.get_textpage()
    tp = tpg.raw
    n = tpg.count_chars()
    l, b, r, t = (ctypes.c_double() for _ in range(4))
    ox, oy = ctypes.c_double(), ctypes.c_double()
    m, loose = raw.FS_MATRIX(), raw.FS_RECTF()
    buf, fl = ctypes.create_string_buffer(96), ctypes.c_int()
    out, gsp = [], False
    for i in range(n):
        if raw.FPDFText_IsGenerated(tp, i):
            gsp = gsp or raw.FPDFText_GetUnicode(tp, i) == 32
            continue
        u = raw.FPDFText_GetUnicode(tp, i)
        if u in (13, 10):
            continue
        # a character beyond the BMP (an emoji) comes as two UTF-16 halves: one char on the first half's box; a
        # lone half is no text (it cannot even be written as UTF-8)
        if 0xDC00 <= u <= 0xDFFF:
            continue
        if 0xD800 <= u <= 0xDBFF:
            low = raw.FPDFText_GetUnicode(tp, i + 1) if i + 1 < n else 0
            if not 0xDC00 <= low <= 0xDFFF:
                continue
            u = 0x10000 + ((u - 0xD800) << 10) + (low - 0xDC00)
        raw.FPDFText_GetCharBox(tp, i, l, r, b, t)
        raw.FPDFText_GetCharOrigin(tp, i, ox, oy)
        raw.FPDFText_GetMatrix(tp, i, m)
        raw.FPDFText_GetLooseCharBox(tp, i, loose)
        raw.FPDFText_GetFontInfo(tp, i, buf, 96, ctypes.byref(fl))
        fn = buf.value.decode("latin-1")
        w = raw.FPDFText_GetFontWeight(tp, i)
        # turned text has b != 0; c alone is a shear (faux italic), which stays in the flow
        rot = abs(m.b) > T["rotated_b"] * (abs(m.a) + abs(m.d))
        out.append(Ch(chr(u), l.value, r.value, H - t.value, H - b.value, H - oy.value, fn,
                      raw.FPDFText_GetFontSize(tp, i) * (abs(m.a * m.d - m.b * m.c) / math.hypot(m.a, m.b) if m.a or m.b else 1.0), w,
                      bool(fl.value & 0x40) or bool(re.search(r"italic|oblique", fn, re.I)),
                      w >= T["bold_weight"] or bool(re.search(r"bold|black|heavy", fn, re.I)),
                      idx=i, ox=ox.value, lr=loose.right, rot=rot))
        out[-1].gsp, gsp = gsp, False
    return _dedupe(out)


def _dedupe(chars):
    """Some producers draw part of the text a second time on top of a watermark (word pieces at exactly the same spot,
    later in the stream); a glyph repeated at the same place in the same font and size is one glyph. The letters of a
    ligature ("ff") also share one spot, but they come one right after the other: only a repeat further on counts.
    Text set under 3 pt is hidden (running-head variables on chapter openers), never read."""
    seen, keep = collections.defaultdict(list), []           # (char, font, size, x cell, y cell) -> [(x0, base, idx)]
    for c in chars:
        if not c.ch.strip():
            keep.append(c)
            continue
        if c.size < T["hidden_text_pt"]:
            continue
        gx, gy = round(c.x0), round(c.base)
        if any(abs(x - c.x0) < T["layer_repeat_pt"] and abs(y - c.base) < T["layer_repeat_pt"] and c.idx - i > T["layer_repeat_gap"]
               for dx in (-1, 0, 1) for dy in (-1, 0, 1)
               for x, y, i in seen[(c.ch, c.font, round(c.size), gx + dx, gy + dy)]):
            continue
        seen[(c.ch, c.font, round(c.size), gx, gy)].append((c.x0, c.base, c.idx))
        keep.append(c)
    return keep


def _tint_boxes(page):
    """filled grey-ish path objects (sidebar boxes): [(x0, top, x1, bottom)]"""
    H, W = page.get_height(), page.get_width()
    res = []
    R, G, B, A = (ctypes.c_uint() for _ in range(4))
    mode, st = ctypes.c_int(), ctypes.c_int()
    for o in page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH]):
        raw.FPDFPath_GetDrawMode(o.raw, mode, st)
        if mode.value == 0:
            continue
        raw.FPDFPageObj_GetFillColor(o.raw, R, G, B, A)
        l, b, r, t = o.get_bounds()
        w, h = r - l, t - b
        lum = (R.value + G.value + B.value) / 3
        if (A.value == 255 and T["tint_lum_min"] <= lum <= T["tint_lum_max"] and w * h >= T["tint_min_area"] and w < W * T["tint_max_share"]
                and h < H * T["tint_max_share"] and max(R.value, G.value, B.value) - min(R.value, G.value, B.value) < T["tint_max_chroma"]):
            res.append((l, H - t, r, H - b))
    return res


def _page_graphics(page):
    """images (top-left boxes), horizontal and vertical rule segments, and the scanned-page signals, in one pass.
    Rules are the straight pieces of path objects (cell borders, table rules): (y, x0, x1) and (x, y0, y1)."""
    W, H = page.get_width(), page.get_height()
    images, hl, vl = [], [], []
    big, inv, vis = False, 0, 0
    m, x, y = raw.FS_MATRIX(), ctypes.c_float(), ctypes.c_float()
    for o in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE, raw.FPDF_PAGEOBJ_TEXT, raw.FPDF_PAGEOBJ_PATH]):
        if o.type == raw.FPDF_PAGEOBJ_TEXT:
            if raw.FPDFTextObj_GetTextRenderMode(o.raw) == raw.FPDF_TEXTRENDERMODE_INVISIBLE:
                inv += 1
            else:
                vis += 1
            continue
        if o.type == raw.FPDF_PAGEOBJ_IMAGE:
            l, b, r, t = o.get_bounds()
            seen = max(0, min(r, W) - max(l, 0)) * max(0, min(t, H) - max(b, 0))
            if seen < T["image_on_page"] * (r - l) * (t - b):
                continue                    # mostly off the page: the bleed of a picture across a spread (Zukav)
            if (r - l) * (t - b) >= T["fullpage_image_share"] * W * H:
                big = True                  # a scanned page or a full-page background: not a figure
            elif r - l >= T["image_min_side"] and t - b >= T["image_min_side"]:
                images.append((l, H - t, r, H - b))
            continue
        raw.FPDFPageObj_GetMatrix(o.raw, m)
        prev = None
        for i in range(raw.FPDFPath_CountSegments(o.raw)):
            s = raw.FPDFPath_GetPathSegment(o.raw, i)
            raw.FPDFPathSegment_GetPoint(s, x, y)
            pt = (m.a * x.value + m.c * y.value + m.e, H - (m.b * x.value + m.d * y.value + m.f))
            kind = raw.FPDFPathSegment_GetType(s)
            if kind == raw.FPDF_SEGMENT_LINETO and prev:
                (x0, y0), (x1, y1) = prev, pt
                if abs(y1 - y0) < T["rule_straight"] and abs(x1 - x0) >= T["hrule_min_len"]:
                    hl.append((y0, min(x0, x1), max(x0, x1)))
                elif abs(x1 - x0) < T["rule_straight"] and abs(y1 - y0) >= T["vrule_min_len"]:
                    vl.append((x0, min(y0, y1), max(y0, y1)))
            prev = pt if kind != raw.FPDF_SEGMENT_BEZIERTO else None
    return dict(images=images, hl=hl, vl=vl, signals=(big, inv, vis))


def page_kind(signals):
    """"ocr": a full-page image under (mostly) invisible text; "scan": a full-page image and no text at all;
    "digital": everything else"""
    big, inv, vis = signals
    if big and inv and inv >= T["scan_invisible_ratio"] * vis:
        return "ocr"
    return "scan" if big and not inv + vis else "digital"


def _miner_chars(pdf_bytes, wanted):
    """the glyphs of some pages read by pdfminer.six instead of pdfium ({page index: [Ch]}), for OCR layers pdfium
    reads wrong (it drops the last letter of most words in Trauma's). None when pdfminer is not installed."""
    try:
        from pdfminer.pdfparser import PDFParser
        from pdfminer.pdfdocument import PDFDocument
        from pdfminer.pdfpage import PDFPage
        from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
        from pdfminer.converter import PDFPageAggregator
        from pdfminer.layout import LTChar, LTContainer
    except ImportError:
        return None
    import io
    import logging
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    rsrc = PDFResourceManager()
    dev = PDFPageAggregator(rsrc, laparams=None)
    interp = PDFPageInterpreter(rsrc, dev)
    out = {}
    for i, page in enumerate(PDFPage.create_pages(PDFDocument(PDFParser(io.BytesIO(pdf_bytes))))):
        if i not in wanted:
            continue
        interp.process_page(page)
        top = page.mediabox[3]
        chars = []

        def walk(o):
            for c in o:
                if isinstance(c, LTChar):
                    t = c.get_text()
                    if len(t) == 1 and t not in "\r\n":
                        chars.append(Ch(t, c.x0, c.x1, top - c.y1, top - c.y0, top - c.matrix[5], c.fontname, c.size,
                                        idx=len(chars)))
                elif isinstance(c, LTContainer):
                    walk(c)
        walk(dev.get_result())
        out[i] = _dedupe(chars)
    return out


def unknown_share(chars):
    """(unknown words, words) of a page's glyphs read in content order; a word ends at a space, a gap of a quarter
    size or a new baseline"""
    out, prev = [], None
    for c in chars:
        if prev is not None and (c.gsp or abs(c.base - prev.base) > 0.5 * c.size or c.x0 - prev.x1 > 0.25 * c.size):
            out.append(" ")
        out.append("" if c.ch in "\x02\u00ad" else c.ch)
        prev = c
    words = [t for t in WORD.findall("".join(out)) if len(t) >= 2 or t in "aAI"]
    E = english_words()
    return sum(word_key(t) not in E for t in words), len(words)


def is_scanned(producer, signals):
    """A scanned book with an OCR text layer: made by OCR software, or most text-bearing pages are a full-page image
    under invisible text. The text of such a layer can be wrong in ways the importer cannot repair (pdfium drops the
    last letter of most words in some ABBYY layers)."""
    text_pages = [s for s in signals if s[1] + s[2]]
    scan_pages = [s for s in text_pages if s[0] and s[1] and s[1] >= T["scan_invisible_ratio"] * s[2]]
    return bool(OCR_PRODUCER.search(producer or "")) or (bool(text_pages) and len(scan_pages) >= T["scan_page_share"] * len(text_pages))


# ====================================================================================================================
# 2. LINES
#   in:  [Ch] of a page
#   out: line dicts (x0, x1, top, bot, base, style, size; after _prepare_line: md, plain, mix, shares)
# ====================================================================================================================


def build_lines(chars, page_no, orn_fonts=frozenset()):
    """chars -> rows (baseline clustering, content-stream order inside a row) -> segments split at big gaps /
    backward jumps. Small super/subscript rows fold into the row they overlap; drop caps attach to the
    first line they sit next to."""
    chars = [c for c in chars if c.font not in orn_fonts]
    body = statistics.median([c.size for c in chars if c.ch.strip()] or [T["default_body_pt"]])
    # (an ornamental initial may be eight times the body size; a letter beyond twelve is art, not text)
    caps = [c for c in chars if c.size >= T["dropcap_min"] * body and c.ch.isalpha() and c.size < T["dropcap_max"] * body]
    # a drop cap stands alone: big letters next to each other are a title on a page with little small text
    caps = [c for c in caps if not any(d is not c and abs(d.base - c.base) < T["dropcap_alone_base"] * c.size and abs(d.x0 - c.x0) < T["dropcap_alone_x"] * c.size
                                       for d in caps)]
    capset = {id(c) for c in caps}
    chars = [c for c in chars if id(c) not in capset]
    rows = []
    for c in sorted(chars, key=lambda c: (c.base, c.idx)):
        if rows and abs(c.base - rows[-1][0].base) <= max(T["row_base_pt"], T["row_base_em"] * c.size):
            rows[-1].append(c)
        else:
            rows.append([c])
    # a row of small callouts only ("50" mid-line, "51" at the end of the next line) splits into one row per
    # callout, so that each folds into its own line
    split = []
    for r in rows:
        ink = sorted((c for c in r if c.ch.strip()), key=lambda c: c.x0)
        if not ink or not all((c.ch.isdigit() or c.ch in NOTE_SIGNS) and c.size <= T["ref_size"] * body for c in ink):
            split.append(r)
            continue
        part = [ink[0]]
        for a, b in zip(ink, ink[1:]):
            if b.x0 - a.x1 > T["ref_gap_em"] * body:
                split.append(part)
                part = []
            part.append(b)
        split.append(part)
    rows = split

    def rsize(r):
        s = [c.size for c in r if c.ch.strip()]
        return statistics.median(s) if s else 0
    sizes = [rsize(r) for r in rows]
    merged = set()
    for i, r in enumerate(rows):
        if not sizes[i]:
            continue
        rx0, rx1 = min(c.x0 for c in r), max(c.x1 for c in r)
        tiny = all(c.ch in "¹²³⁴⁵⁶⁷⁸⁹⁰₀₁₂₃₄₅₆₇₈₉⁄/ " for c in r)
        # a note callout at the end of a line sticks out past it
        callout = 0 < len([c for c in r if c.ch.strip()]) <= T["ref_max_chars"] and (
            all(c.ch.isdigit() or c.ch in NOTE_SIGNS + " " for c in r) or NOTE_LETTERS.fullmatch("".join(c.ch for c in r).strip()))
        best = None
        for j, n in enumerate(rows):
            if j == i or j in merged or sizes[j] <= 0 or (sizes[i] > T["script_size"] * sizes[j] and not tiny):
                continue
            nx0, nx1 = min(c.x0 for c in n), max(c.x1 for c in n)
            if (nx0 - T["script_slack"] <= rx0 and (rx1 <= nx1 + T["script_slack"] or callout and rx0 <= nx1 + T["ref_gap_em"] * sizes[j])
                    and abs(r[0].base - n[0].base) <= (T["script_reach_tiny"] if tiny else T["script_reach"]) * sizes[j]):
                d = abs(r[0].base - n[0].base)
                if best is None or d < best[0]:
                    best = (d, j)
        if best:
            rows[best[1]] = rows[best[1]] + r
            merged.add(i)
    rows = [r for i, r in enumerate(rows) if i not in merged]
    segs = []
    for r in rows:
        r.sort(key=lambda c: c.idx)
        seg = [r[0]]
        for a, b in zip(r, r[1:]):
            # a ligature (Th, fi, ffl ...) comes out as several chars on ONE glyph box: the second starts where the
            # first did, which is not a jump back to an earlier part of the line
            same_box = abs(b.x0 - a.x0) < T["ligature_box_em"] * max(b.size, 1.0)
            fwd, back = b.x0 - a.x1, (0.0 if same_box else a.x1 - b.x0)
            if (fwd > max(T["seg_gap_em"] * b.size, T["seg_gap_pt"]) or back > T["seg_back_em"] * b.size) and b.ch.strip() and a.ch.strip():
                segs.append(seg)
                seg = [b]
            else:
                seg.append(b)
        segs.append(seg)
    out = []
    for seg in segs:
        if not "".join(c.ch for c in seg).strip():
            continue
        cnt = collections.Counter(size_key(c.font, c.size) for c in seg if c.ch.strip())
        key = cnt.most_common(1)[0][0]
        out.append(dict(chars=seg, x0=min(c.x0 for c in seg), x1=max(c.x1 for c in seg), top=min(c.top for c in seg),
                        bot=max(c.bot for c in seg), base=statistics.median(c.base for c in seg), style=key,
                        size=float(key.split("|")[1]), page=page_no))
    out.sort(key=lambda l: (round(l["base"]), l["x0"]))
    for dc in caps:
        dc.dropcap = True
        cands = [l for l in out if l["x0"] >= dc.x0 - T["dropcap_x_slack"] and abs(l["top"] - dc.top) <= T["dropcap_top_em"] * dc.size + T["dropcap_top_pt"]]
        if cands:
            # the line beside the initial's top (a tall initial reaches several lines; their left edges differ by
            # hairs, so the top decides)
            l = min(cands, key=lambda l: (abs(l["top"] - dc.top) > T["dropcap_line_top"] * l["size"], abs(l["top"] - dc.top), l["x0"]))
            # the initial is the first letter of the word, not a styled run of its own ("**O**ne" -> "One")
            dc.italic, dc.bold = l["chars"][0].italic, l["chars"][0].bold
            l["chars"].insert(0, dc)
            l["x0"] = min(l["x0"], dc.x0)
            l["top"] = min(l["top"], dc.top)
        else:
            out.append(dict(chars=[dc], x0=dc.x0, x1=dc.x1, top=dc.top, bot=dc.bot, base=dc.base,
                            style=size_key(dc.font, dc.size), size=dc.size, page=page_no))
    out.sort(key=lambda l: (round(l["base"]), l["x0"]))
    return out


def _two_clusters(gaps):
    """Otsu split of a list of gap widths: (threshold, mean of the small gaps, mean of the large gaps)"""
    s = sorted(gaps)
    n, tot, left, best = len(s), sum(s), 0.0, None
    for k in range(1, n):
        left += s[k - 1]
        m1, m2 = left / k, (tot - left) / (n - k)
        v = k * (n - k) * (m1 - m2) ** 2
        if best is None or v > best[0]:
            best = (v, (s[k - 1] + s[k]) / 2, m1, m2)
    return best[1:] if best else (float("inf"), 0.0, 0.0)


def line_tokens(line):
    """(char, italic, bold) tokens with inferred word spaces.
    Letter-spaced (tracked) lines: the gaps between glyphs fall into two clusters, letter gaps and word gaps; a word
    break is a gap in the large cluster, and the line's own space glyphs are ignored (some PDFs put one between every
    letter). One tracked word has a single cluster: no break.
    Other lines: real ' ' chars are honoured (unless hidden inside the previous glyph); extra spaces are inferred
    from the advance gap (0.5 em for ALL-CAPS with real spaces, 0.2 em otherwise) or from pdfium's generated spaces."""
    cs = line["chars"]
    size = line["size"]
    ink = [c for c in cs if c.ch != " "]
    gaps = [b.ox - a.lr for a, b in zip(ink, ink[1:])]
    med = sorted(gaps)[len(gaps) // 2] if gaps else 0.0
    if len(ink) >= T["tracked_min_glyphs"] and med > T["tracked_gap_em"] * size:
        thr, m1, m2 = _two_clusters(gaps)
        if not (m2 - m1 > T["tracked_split_em"] * size and m2 > T["tracked_ratio"] * max(m1, T["tracked_floor_em"] * size)):
            thr = float("inf")
        toks, prev = [], None
        for c in ink:
            if prev is not None and not prev.dropcap and c.ox - prev.lr > thr:
                toks.append((" ", prev.italic, prev.bold))
            toks.append((c.ch, c.italic, c.bold))
            prev = c
        return toks
    # some PDFs emit a space glyph after every fi/fl ligature that takes the second half of the ligature's own
    # advance (the space starts ~0.14 em per letter after the ligature's origin, the next letter right after it): no
    # word break. A real space after "ff" starts a full ligature advance later. A ligature is chars on one glyph box
    # (same left and right edge; an overhanging f only shares its neighbour's left edge).
    def fake_space(i):
        c = cs[i]
        if not (c.ch == " " and 1 < i < len(cs) - 1 and cs[i - 1].ch.isalpha()):
            return False
        k = i - 1
        while (k > 0 and abs(cs[k - 1].x0 - cs[i - 1].x0) < T["ligature_box_em"] * size
               and abs(cs[k - 1].x1 - cs[i - 1].x1) < T["ligature_box_em"] * size):
            k -= 1
        n = i - k
        return n >= 2 and c.ox - cs[i - 1].ox < T["fake_space_em"] * n * size and cs[i + 1].ox - cs[i - 1].lr < T["fake_space_gap_em"] * size
    cs = [c for i, c in enumerate(cs) if not fake_space(i)]
    has_space = any(c.ch == " " for c in cs)
    letters = [c for c in cs if c.ch.isalpha()]
    caps = sum(c.ch.isupper() for c in letters) / max(1, len(letters))
    if has_space and caps > T["caps_share"]:
        thr = T["space_caps_em"] * size
    else:
        thr = T["space_em"] * size
    # pdfium's own generated space is a second opinion where a clipped glyph box (an "f" in some fonts, kerned
    # capitals) hides the gap; it also fires inside lightly tracked titles, so the gap must still stand out from
    # the line's usual letter gap
    lgaps = sorted(b.ox - a.lr for a, b in zip(cs, cs[1:]) if not a.ch.isspace() and not b.ch.isspace())
    lmed = lgaps[len(lgaps) // 2] if lgaps else 0.0
    toks, prev = [], None
    for c in cs:
        if prev is not None and c.ch != " " and prev.ch != " " and not prev.dropcap:
            gap = c.ox - prev.lr
            gen = c.gsp and not c.ch.isspace() and not prev.ch.isspace() and gap - lmed >= T["gen_space_em"] * size
            if gap > thr or gen:
                toks.append((" ", prev.italic, prev.bold))
        toks.append((c.ch, c.italic, c.bold))
        prev = c
    return toks


def inline_md(toks, bold_ok=True):
    out, cur, buf = [], None, ""

    def flush():
        nonlocal buf
        if buf:
            core = buf.strip()
            lead, trail = buf[:len(buf) - len(buf.lstrip())], buf[len(buf.rstrip()):]
            i, b = cur
            if core:
                if b and bold_ok and i:
                    core = f"***{core}***"
                elif b and bold_ok:
                    core = f"**{core}**"
                elif i:
                    core = f"*{core}*"
            out.append(lead + core + trail)
        buf = ""
    for ch, i, b in toks:
        st = (i, b) if ch != " " else (cur or (i, b))
        if cur is None:
            cur = st
        if st != cur:
            flush()
            cur = st
        buf += ch
    flush()
    return "".join(out)


def _mark_refs(l):
    """Note callouts: digits, a note sign (*, †, ...) or a lowercase letter (a, b, aa) set smaller and raised, right
    after a word or its closing punctuation. After a digit it is an exponent (10², 6′0″) or an ordinal (1st), after a
    capital a formula or a name (B12, CO2), lowered a subscript: none is a callout. A letter may follow a capital
    ("the OECDa"), but only a letter alone or doubled (a, aa): "Mme", "Mlle" are no callouts.
    The run is wrapped in REF_OPEN/REF_CLOSE and takes the style of the word before it."""
    cs, z, base = l["chars"], l["size"], l["base"]
    raised = lambda c: ((c.ch.isdigit() or c.ch in NOTE_SIGNS or "a" <= c.ch <= "z") and c.size <= T["ref_size"] * z
                        and c.base < base - T["ref_raise"] * z)
    i = 0
    while i < len(cs):
        if not raised(cs[i]):
            i += 1
            continue
        j = i
        while j + 1 < len(cs) and raised(cs[j + 1]):
            j += 1
        run = cs[i:j + 1]
        prev = next((c for c in reversed(cs[:i]) if c.ch.strip()), None)
        same = all(c.ch.isdigit() for c in run) or all(c.ch in NOTE_SIGNS for c in run)
        letter = NOTE_LETTERS.fullmatch("".join(c.ch for c in run))
        if (prev is not None and (prev.ch[-1:].islower() or prev.ch[-1:] in ".,;:!?’”\")»" or letter and prev.ch[-1:].isupper())
                and (same and len(run) <= T["ref_max_chars"] or letter)):
            run[0].ch = REF_OPEN + run[0].ch
            run[-1].ch += REF_CLOSE       # one char may be both ends
            for c in run:
                c.italic, c.bold = prev.italic, prev.bold
        i = j + 1


def _prepare_line(l):
    # pdfium marks a line-end hyphen as \x02 itself, but not a hyphen or non-breaking hyphen (U+2010, U+2011), nor
    # any hyphen of a page read by pdfminer (OCR pages)
    ink = [c for c in l["chars"] if c.ch.strip()]
    if len(ink) >= 2 and ink[-2].ch.isalpha() and (ink[-1].ch in "\u2010\u2011" or ink[-1].ch == "-" and ink[-1].font == "OCR"):
        ink[-1].ch = "\x02"
    _mark_refs(l)
    toks = line_tokens(l)
    l["md"], l["md_nobold"] = inline_md(toks, True), inline_md(toks, False)
    # the block rules read the callout's digits as printed, as they always did ("1928.⁶³ Yet" is no list item)
    l["plain"] = REF.sub(r"\1", "".join(t[0] for t in toks))
    l["refs"] = REF.findall(l["md"])
    ne = [c for c in l["chars"] if c.ch.strip()]
    n = max(1, len(ne))
    l["italic_share"] = sum(1 for c in ne if c.italic) / n
    l["bold_share"] = sum(1 for c in ne if c.bold) / n
    l["w0"] = l["chars"][0].weight
    l["mix"] = collections.Counter(size_key(c.font, c.size) for c in ne)
    l["mix_flag"] = collections.Counter(size_key(c.font, c.size) for c in ne if c.italic or c.bold)
    del l["chars"]                      # keep memory flat on long books


# ====================================================================================================================
# 3. BLOCKS
#   in:  [Ch], tinted boxes, images and rules of a page
#   out: page dict {p, W, H, blocks, boxes, items (figures/tables), rotated, fig_words, fig_text};
#        a block: lines + style (dominant), box, list flag, geometry, text
# ====================================================================================================================


def two_column_suspect(lines, W):
    left = [l for l in lines if l["x0"] < T["col_left"] * W]
    # a right-hand line with no word in it (a contents page's page numbers, roman or arabic) is no column
    right = [l for l in lines if l["x0"] > T["col_right"] * W and l["x1"] <= W + T["col_slack"]
             and any(len(w) >= T["col_word_len"] and not re.fullmatch(r"[ivxlcdm]+", w, re.I) for w in re.findall(r"[^\W\d_]+", l["plain"]))]
    rows = sum(1 for r in right if any(abs(r["base"] - l["base"]) < T["col_base_pt"] and l["x1"] < r["x0"] for l in left))
    return rows >= T["col_min_rows"], rows


def _two_column_order(lines, W):
    """Two columns on a page: read the left column, then the right, between lines that span the gutter (a heading
    over both columns). The gutter is the x in the middle band of the page crossed by the fewest lines."""
    cands = [W * (T["gutter_lo"] + 0.01 * k) for k in range(41)]
    cross = lambda g: sum(1 for l in lines if l["x0"] < g - T["gutter_slack"] and l["x1"] > g + T["gutter_slack"])
    g = min(cands, key=lambda g: (cross(g), abs(g - W / 2)))
    out, run = [], []

    def flush():
        out.extend(sorted((l for l in run if l["x1"] <= g + T["gutter_slack"]), key=lambda l: (round(l["base"]), l["x0"])))
        out.extend(sorted((l for l in run if l["x1"] > g + T["gutter_slack"]), key=lambda l: (round(l["base"]), l["x0"])))
        run.clear()
    for l in sorted(lines, key=lambda l: (round(l["base"]), l["x0"])):
        if l["x0"] < g - T["gutter_slack"] and l["x1"] > g + T["gutter_slack"]:
            flush()
            out.append(l)
        else:
            run.append(l)
    flush()
    return out


def _in_box(l, boxes):
    cx, cy = (l["x0"] + l["x1"]) / 2, (l["top"] + l["bot"]) / 2
    for i, (a, b, c, d) in enumerate(boxes):
        if a - T["box_slack"] <= cx <= c + T["box_slack"] and b - T["box_slack"] <= cy <= d + T["box_slack"]:
            return i
    return None


def _finish_block(b):
    ls = b["lines"]
    b["top"], b["bot"] = min(l["top"] for l in ls), max(l["bot"] for l in ls)
    b["x0"], b["x1"] = min(l["x0"] for l in ls), max(l["x1"] for l in ls)
    b["size"] = ls[0]["size"]
    b["nwords"] = sum(len(l["plain"].split()) for l in ls)
    tot = max(1, sum(len(l["plain"]) for l in ls))
    b["italic_share"] = sum(l["italic_share"] * len(l["plain"]) for l in ls) / tot
    b["text"] = " ".join(l["plain"] for l in ls)


def build_blocks(page_no, W, H, chars, boxes, orn_fonts=frozenset(), images=(), hl=(), vl=()):
    # text outside the page box (facing-page copies in spreads) is not part of this page
    sl = T["page_slack"]
    chars = [c for c in chars if -sl <= (c.x0 + c.x1) / 2 <= W + sl and -sl <= (c.top + c.bot) / 2 <= H + sl]
    src_chars = sum(1 for c in chars if c.ch.strip())     # the page's text, for the review screen's loss check
    orn_chars = sum(1 for c in chars if c.ch.strip() and not c.rot and c.font in orn_fonts)   # ornaments: left out
    # text set at an angle is a watermark or an edge tab, never part of the reading flow
    rotated = sum(1 for c in chars if c.rot and c.ch.strip())
    chars = [c for c in chars if not c.rot]
    bs = T["box_slack"]
    inside = lambda c, b: b[0] - bs <= (c.x0 + c.x1) / 2 <= b[2] + bs and b[1] - bs <= (c.top + c.bot) / 2 <= b[3] + bs
    items, fig_words, fig_text = [], 0, []
    # a figure: its place in the flow, and the lettering inside it (diagram labels) belongs to the figure. Judged line
    # by line: only a line wholly inside the picture goes, and never a title or caption printed inside the picture's
    # box ("Fig. 2: ...", a wide line along its top or bottom edge): those become the figure's captions. A lot of text
    # over an image is real text set on a background: it stays.
    page_lines = build_lines(chars, page_no, orn_fonts) if images else []
    page_body = statistics.median([c.size for c in chars if c.ch.strip()] or [T["default_body_pt"]])
    for k, im in enumerate(sorted(images, key=lambda b: (round(b[1]), b[0])), 1):
        h, w = im[3] - im[1], im[2] - im[0]

        def label(l):
            """a diagram label, as opposed to a title or caption printed inside the picture's box: one that reads like a
            caption, or a wide line (35%+ of the picture) in the top or bottom 15% of it, stays in the text; so does
            display type well above the page's body size (a chapter number and title on an ornament band, Zukav)"""
            if CAPTION.match("".join(c.ch for c in l["chars"]).replace("*", "")):
                return False
            if l["size"] >= T["fig_display_size"] * page_body:
                return False
            wide = l["x1"] - l["x0"] >= T["fig_title_width"] * w
            edge = l["bot"] <= im[1] + T["fig_title_band"] * h or l["top"] >= im[3] - T["fig_title_band"] * h
            return not (wide and edge)
        own_lines = [l for l in page_lines if l["chars"] and all(inside(c, im) for c in l["chars"]) and label(l)]
        own = [c for l in own_lines for c in l["chars"]]
        words = sum(len("".join(c.ch for c in l["chars"]).split()) for l in own_lines)
        if own and words <= T["fig_label_words"]:
            ids = {id(c) for c in own}
            chars = [c for c in chars if id(c) not in ids]
            fig_words += words
            fig_text.append(" ".join("".join(c.ch for c in l["chars"]) for l in own_lines))
        items.append(dict(kind="image", page=page_no, n=k, x0=im[0], top=im[1], x1=im[2], bot=im[3]))
    # ruled tables: one segment each; a table with merged cells is marked like a figure
    for k, gr in enumerate(find_grids(hl, vl) if hl and vl else [], 1):
        box = (gr["xs"][0], gr["ys"][0], gr["xs"][-1], gr["ys"][-1])
        own = [c for c in chars if inside(c, box)]
        md = None if gr["complex"] else grid_markdown(gr, own, page_no)
        if not own or (md is None and not gr["complex"]):
            continue
        ids = {id(c) for c in own}
        chars = [c for c in chars if id(c) not in ids]
        items.append(dict(kind="table", page=page_no, n=k, x0=box[0], top=box[1], x1=box[2], bot=box[3], md=md,
                          complex=gr["complex"], words=_word_count(own, page_no),
                          text="".join(c.ch for c in sorted(own, key=lambda c: c.idx))))
    lines = build_lines(chars, page_no, orn_fonts)
    for l in lines:
        _prepare_line(l)
        l["box"] = _in_box(l, boxes)
    if two_column_suspect([l for l in lines if l["size"] < T["col_max_size"]], W)[0]:
        lines = _two_column_order(lines, W)
    margin_c = collections.defaultdict(collections.Counter)
    for l in lines:
        margin_c[(l["style"], l["box"] is not None)][round(l["x0"])] += 1
    margin = {k: min(v, key=lambda x: (-v[x], x)) for k, v in margin_c.items()}
    pitch = collections.defaultdict(list)
    for a, b in zip(lines, lines[1:]):
        if a["style"] == b["style"] and 0 < b["base"] - a["base"] < T["pitch_max_em"] * a["size"]:
            pitch[a["style"]].append(b["base"] - a["base"])
    pit = {k: statistics.median(v) for k, v in pitch.items()}
    blocks, cur = [], None

    def new(l):
        return dict(lines=[l], style=l["style"], box=l["box"], page=page_no, list=bool(ITEM.match(l["plain"])),
                    mix=collections.Counter(l["mix"]))

    full = [l["x1"] for l in lines if len(l["plain"].split()) >= T["full_line_words"]]
    right = statistics.median(full) if full else W

    def inline_run(l, a):
        """a line whose own majority style differs from the block's is still the block's text when the change is an
        inline run (an italic title, a bold lead-in, another font): the same size, the previous line runs to the
        right edge (a short line is a heading or a paragraph's end), and either one of the two lines mixes both styles
        or the line goes on with the previous line's open sentence in lowercase (a heading starts with a capital)"""
        if abs(l["size"] - a["size"]) > T["inline_size_pt"] or a["x1"] < right - T["full_line_em"] * a["size"]:
            return False
        return (cur["style"] in l["mix"] or l["style"] in a["mix"]
                or (not terminal(a["plain"]) and bool(re.match(r"[\W\d_]*[a-z]", l["plain"]))))
    def indented_run(l, a, mg):
        """an indented line under a line with the same indent is no first-line indent but an indented block (a quote,
        an epigraph) going on: the line above ran to the right edge without ending its sentence"""
        return (abs(l["x0"] - a["x0"]) < T["indent_same_pt"] and a["x0"] - mg > T["indent_em"] * l["size"]
                and a["x1"] >= right - T["indented_full_em"] * a["size"]
                and not terminal(a["plain"]))
    for l in lines:
        if cur is None:
            cur = new(l)
            continue
        a = cur["lines"][-1]
        gap = l["base"] - a["base"]
        restyled = l["style"] != cur["style"] and not (l["box"] == cur["box"] and inline_run(l, a))
        st = cur["style"] if not restyled else l["style"]
        ref = pit.get(st, pit.get(l["style"], T["default_pitch_em"] * l["size"]))
        mg = margin.get((st, l["box"] is not None), margin[(l["style"], l["box"] is not None)])
        if restyled or l["box"] != cur["box"]:
            brk = True
        elif gap > T["para_gap"] * ref or (gap < T["overlap_em"] * l["size"] and l["x0"] < a["x1"]):
            brk = True
        elif ITEM.match(l["plain"]) and len(l["plain"].strip()) > T["item_min_chars"]:
            brk = True
        elif (l["x0"] - mg > T["indent_em"] * l["size"] and not (cur["list"] and l["x0"] >= cur["lines"][0]["x0"] - T["list_slack"])
              and not indented_run(l, a, mg)):
            brk = True
        else:
            brk = False
        if brk:
            blocks.append(cur)
            cur = new(l)
        else:
            cur["lines"].append(l)
            cur["mix"].update(l["mix"])
            cur["style"] = cur["mix"].most_common(1)[0][0]     # a block's role follows its dominant style
    if cur:
        blocks.append(cur)
    for b in blocks:
        _finish_block(b)
    return dict(p=page_no, W=W, H=H, blocks=blocks, boxes=boxes, rotated=rotated, items=items, fig_words=fig_words,
                fig_text=fig_text, src_chars=src_chars, orn_chars=orn_chars)


# ====================================================================================================================
# 4. MARGINS AND FURNITURE
#   in:  every page's blocks
#   out: line['sil'], line['zone'], block['sil'], block['edge'], page['cover'],
#        page['running_removed'], Doc.text_top / text_bot; boxes too small to be boxes dropped
# ====================================================================================================================


class _Margins:
    """Stage 4 (Doc mixin): what lies outside the text block or repeats on page after page."""

    # -- margin text: running heads/feet, page numbers, slug, watermark ------------------------------
    def _mark_cover_and_repeats(self):
        """The margin is what lies above or below the book's text block: the median top of the first and bottom of
        the last body-size line over all pages (a fixed share of the page height misses books whose text block sits
        in the middle of a large page). In the margin a lone number is a page number, and a line whose letters
        (spaces, digits and punctuation ignored, so letter-spaced heads compare too) recur on 3+ pages and is not
        larger than body text is a running head/foot or slug. In the outer bands of the page (top 9%, bottom 14%), a
        style at the same spot on 35%+ of the pages is a watermark or footer when its text (digits ignored) recurs
        too; never the body style or a body-size line wider than half the text block, as the body often runs into the
        band."""
        words, swords = collections.Counter(), collections.Counter()
        for p in self.pages:
            for b in p["blocks"]:
                for l in b["lines"]:
                    words[l["size"]] += len(l["plain"].split())
                    swords[l["style"]] += len(l["plain"].split())
        bsize = max(words, key=words.get) if words else T["default_body_pt"]
        bstyle = max(swords, key=swords.get) if swords else None
        widths = [l["x1"] - l["x0"] for p in self.pages for b in p["blocks"] for l in b["lines"] if l["style"] == bstyle]
        bwidth = statistics.median(widths) if widths else 0.0
        # The text block is where body lines sit. A running head set near the body size counts as one unless it is
        # left out: lines short of the full measure whose text (page numbers ignored) recurs on 3+ pages, and, for the
        # top edge, lines narrower than half the text block (a head, a folio with a title).
        recur = collections.defaultdict(set)
        for p in self.pages:
            for b in p["blocks"]:
                for l in b["lines"]:
                    recur[_similar_key(l["plain"])].add(p["p"])
        tops, bots = [], []
        for p in self.pages:
            bl = [l for b in p["blocks"] for l in b["lines"] if abs(l["size"] - bsize) <= T["body_size_pt"]
                  and len(l["plain"].split()) >= T["body_line_words"]
                  and not (len(recur[_similar_key(l["plain"])]) >= T["repeat_pages"] and l["x1"] - l["x0"] < T["full_measure"] * bwidth)]
            # (narrow lines only say nothing about the top: at the bottom a paragraph's short last line is body text)
            wide = [l for l in bl if l["x1"] - l["x0"] >= T["half_measure"] * bwidth]
            if len(bl) >= T["page_body_lines"] and wide:
                tops.append(min(l["top"] for l in wide))
                bots.append(max(l["bot"] for l in bl))
        self.text_top = statistics.median(tops) if tops else None
        self.text_bot = statistics.median(bots) if bots else None

        def zone(p, l):
            if self.text_top is None:
                return "top" if l["top"] < T["band_top"] * p["H"] else "bot" if l["top"] > T["band_bot"] * p["H"] else None
            return ("top" if l["bot"] <= self.text_top - T["zone_slack"] else "bot" if l["top"] >= self.text_bot + T["zone_slack"]
                    else None)
        rep, pos, same = collections.defaultdict(set), collections.defaultdict(set), collections.defaultdict(set)
        for p in self.pages:
            for b in p["blocks"]:
                for l in b["lines"]:
                    l["zone"] = zone(p, l)
                    l["key"] = "".join(ch for ch in l["plain"].lower() if ch.isalpha())
                    if l["zone"] and l["key"]:
                        rep[(l["zone"], l["key"])].add(p["p"])
                    pos[(l["style"], round(l["top"] / T["pos_cell_pt"]))].add(p["p"])
                    same[(l["style"], _similar_key(l["plain"]))].add(p["p"])
        # a running head carries the page number: a margin line of number + a few words whose number is the page's
        # (the same offset from the PDF page as most such lines; roman and arabic counted apart) goes whatever its
        # text, so heads that change from chapter to chapter go too
        # a footnote starts with the number of a callout on its page: never a running foot, even when one note per page
        # keeps the same distance to the page number for a few pages (Sazima)
        calls = {p["p"]: {r for b in p["blocks"] for l in b["lines"] for r in l["refs"]} for p in self.pages}

        def folio(l, p):
            m = l["zone"] and l["size"] <= T["margin_size"] * bsize and FOLIO.match(l["plain"])
            if not m or len(l["plain"].split()) > T["folio_words"] or (m.group(1) or "") in calls[p["p"]]:
                return None
            n = m.group(1) or m.group(2)
            return ("a", int(n)) if n.isdigit() else ("r", _roman(n))
        offs = collections.Counter()
        for p in self.pages:
            for b in p["blocks"]:
                for l in b["lines"]:
                    f = folio(l, p)
                    if f and f[1]:
                        offs[(f[0], f[1] - p["p"])] += 1
        best = {k: max((o for o in offs if o[0] == k), key=offs.get, default=None) for k in ("a", "r")}
        need = max(T["band_min_pages"], T["band_share"] * self.NP)
        for p in self.pages:
            p["cover"] = bool(self.W0) and abs(p["W"] - self.W0) > T["cover_width"] * self.W0
            p["running_removed"] = 0
            for b in p["blocks"]:
                for l in b["lines"]:
                    f = folio(l, p)
                    head = bool(f and f[1] and best[f[0]] and offs[best[f[0]]] >= T["repeat_pages"] and f[1] - p["p"] == best[f[0]][1])
                    bodylike = l["style"] == bstyle or (abs(l["size"] - bsize) <= T["body_size_pt"] and l["x1"] - l["x0"] > T["half_measure"] * bwidth)
                    band = ((l["top"] < T["band_top"] * p["H"] or l["top"] > T["band_bot"] * p["H"]) and not bodylike
                            and len(same[(l["style"], _similar_key(l["plain"]))]) >= T["repeat_pages"])
                    l["sil"] = bool(
                        (l["zone"] and PAGE_NO.match(l["plain"]))
                        or (l["zone"] and l["key"] and len(rep[(l["zone"], l["key"])]) >= T["repeat_pages"] and l["size"] <= T["margin_size"] * bsize)
                        or head
                        or (band and len(pos[(l["style"], round(l["top"] / T["pos_cell_pt"]))]) >= need))
                    p["running_removed"] += l["sil"] and not PAGE_NO.match(l["plain"])
                kept = [l for l in b["lines"] if not l["sil"]]
                b["sil"] = not kept
                if kept and len(kept) < len(b["lines"]):
                    b["cut_lines"] = [l for l in b["lines"] if l["sil"]]     # for the review screen
                    b["lines"] = kept
                    _finish_block(b)
                b["edge"] = all(l["zone"] for l in b["lines"])

    def _filter_boxes(self, min_words=T["box_min_words"]):
        """tinted shapes holding fewer than min_words words are figure panels, not boxes"""
        for p in self.pages:
            wc = collections.Counter()
            for b in p["blocks"]:
                if b["box"] is not None and not b["sil"]:
                    wc[b["box"]] += b["nwords"]
            for b in p["blocks"]:
                if b["box"] is not None and wc[b["box"]] < min_words:
                    b["box"] = None

    def _recheck_two_col(self):
        for p in self.pages:
            ls = [l for b in p["blocks"] if not b["sil"] for l in b["lines"] if l["size"] < T["col_max_size"]]
            p["two_col"], p["two_rows"] = two_column_suspect(ls, p["W"])

    def _title_running_heads(self):
        """a margin line that repeats a heading of the book on two or more pages is a running head (the chapter or
        section title printed in the head or foot) even when it is not on 3+ pages or carries no page number"""
        small = lambda b: b.get("edge") and b["size"] <= T["margin_size"] * self.bsize
        heads = {_similar_key(b["text"]) for p in self.pages for b in p["blocks"] if b["role"] in HEAD and not small(b)}
        heads.discard("")
        seen = collections.defaultdict(list)
        for p in self.pages:
            for b in p["blocks"]:
                # a sub-heading at the top of a page keeps its heading role; only text not taken for a heading goes
                if small(b) and b["role"] not in HEAD and b["role"] != "sil" and _similar_key(b["text"]) in heads:
                    seen[_similar_key(b["text"])].append((p, b))
        for hits in seen.values():
            if len({p["p"] for p, _ in hits}) >= T["title_head_pages"]:
                for p, b in hits:
                    b["role"] = "sil"
                    p["running_removed"] += len(b["lines"])


# ====================================================================================================================
# 5. OBJECTS (figures, tables, groups)
#   in:  rules and glyphs of a page (grids, called by build_blocks); items + roles (groups)
#   out: table items with markdown or a placeholder; blocks around an object -> role 'altyazı'
# ====================================================================================================================


def _merge_rules(segs, tol=T["rule_collinear"], gap=T["rule_join"]):
    """collinear rule pieces -> {position: [(a, b), ...]} with touching pieces joined"""
    out = {}
    for pos, a, b in sorted(segs):
        key = next((k for k in out if abs(k - pos) <= tol), None)
        if key is None:
            out[pos] = [(a, b)]
            continue
        iv = out[key]
        if a <= iv[-1][1] + gap:
            iv[-1] = (iv[-1][0], max(iv[-1][1], b))
        else:
            iv.append((a, b))
        iv.sort()
    return out


def find_grids(hl, vl):
    """Ruled tables: horizontal and vertical rules that cross each other. A grid needs at least 3 horizontal and 3
    vertical rule positions (2 x 2 cells; a framed figure is 1 x 1). A cell border that is missing inside the grid means
    merged cells: the grid is marked complex."""
    H_ = _merge_rules(hl)
    V_ = _merge_rules(vl)
    sl = T["grid_slack"]
    covers = lambda ivs, a, b: any(s - sl <= a and b <= e + sl for s, e in ivs)
    hs = [(y, s, e) for y, ivs in H_.items() for s, e in ivs]
    vs = [(x, s, e) for x, ivs in V_.items() for s, e in ivs]
    parent = list(range(len(hs) + len(vs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, (y, x0, x1) in enumerate(hs):
        for j, (x, y0, y1) in enumerate(vs):
            if x0 - sl <= x <= x1 + sl and y0 - sl <= y <= y1 + sl:
                parent[find(i)] = find(len(hs) + j)
    groups = collections.defaultdict(lambda: ([], []))
    for i in range(len(hs)):
        groups[find(i)][0].append(hs[i])
    for j in range(len(vs)):
        groups[find(len(hs) + j)][1].append(vs[j])
    grids = []
    for gh, gv in groups.values():
        ys = sorted({round(y, 1) for y, _, _ in gh})
        xs = sorted({round(x, 1) for x, _, _ in gv})
        mp = T["grid_merge_pos"]
        ys = [y for k, y in enumerate(ys) if k == 0 or y - ys[k - 1] > mp]
        xs = [x for k, x in enumerate(xs) if k == 0 or x - xs[k - 1] > mp]
        if len(ys) < T["grid_min_rules"] or len(xs) < T["grid_min_rules"]:
            continue
        # a table has a full outer frame; a decorative frame with a tab on top (a chapter-number badge) does not
        span = lambda ivs, a, b: sum(max(0, min(e, b) - max(s, a)) for s, e in ivs) >= T["grid_frame_span"] * (b - a)
        edge_h = lambda y: [(s, e) for yy, s, e in gh if abs(yy - y) <= mp]
        edge_v = lambda x: [(s, e) for xx, s, e in gv if abs(xx - x) <= mp]
        if not (span(edge_h(ys[0]), xs[0], xs[-1]) and span(edge_h(ys[-1]), xs[0], xs[-1])
                and span(edge_v(xs[0]), ys[0], ys[-1]) and span(edge_v(xs[-1]), ys[0], ys[-1])):
            continue
        complex_ = False
        for x in xs[1:-1]:
            ivs = [(s, e) for xx, s, e in gv if abs(xx - x) <= mp]
            complex_ |= any(not covers(ivs, ys[k] + T["grid_cell_pad"], ys[k + 1] - T["grid_cell_pad"]) for k in range(len(ys) - 1))
        for y in ys[1:-1]:
            ivs = [(s, e) for yy, s, e in gh if abs(yy - y) <= mp]
            complex_ |= any(not covers(ivs, xs[k] + T["grid_cell_pad"], xs[k + 1] - T["grid_cell_pad"]) for k in range(len(xs) - 1))
        grids.append(dict(xs=xs, ys=ys, complex=complex_))
    return grids


def _word_count(chars, page_no):
    ls = build_lines(chars, page_no)
    for l in ls:
        _prepare_line(l)
    return sum(len(l["plain"].split()) for l in ls)


def _cell_text(chars, page_no):
    ls = build_lines(chars, page_no)
    for l in ls:
        _prepare_line(l)
    return join_lines([l["md"].strip() for l in ls], set(), collections.Counter()).replace("|", "\\|") if ls else ""


def grid_markdown(grid, chars, page_no):
    """the grid's text as a markdown table (first row = header), or None when it holds no text"""
    xs, ys = grid["xs"], grid["ys"]
    cells = [[[] for _ in range(len(xs) - 1)] for _ in range(len(ys) - 1)]
    for c in chars:
        cx, cy = (c.x0 + c.x1) / 2, (c.top + c.bot) / 2
        r = next((k for k in range(len(ys) - 1) if ys[k] <= cy <= ys[k + 1]), None)
        q = next((k for k in range(len(xs) - 1) if xs[k] <= cx <= xs[k + 1]), None)
        if r is not None and q is not None:
            cells[r][q].append(c)
    rows = [[_cell_text(cell, page_no) for cell in row] for row in cells]
    if not any(any(x.strip() for x in row) for row in rows):
        return None
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


class _Objects:
    """Stage 5 (Doc mixin): the captions, titles and credits that belong to a figure or table."""

    def deviates(self, b):
        """set apart from body text: a family/size the book's body styles do not use (italic and bold variants of a
        body style are body text)"""
        return fam_of(b["style"]) not in self.body_fams

    def _object_groups(self):
        """A figure or table owns the short blocks set apart from body text that sit inside its frame or right above or
        below it, block after block within 2.5 body sizes: title, caption, explanation, credit line. They are written
        as captions around the object, never as headings or paragraphs. A heading can be an object's title (above or
        inside it), never the line under it (that is the next section starting); part and chapter headings - the ones a
        table of contents lists - never belong to an object. Body-styled blocks only join when they read like a
        caption ("Figure 3", "Fig. 3", "Şekil 3"). Blocks set apart from body text that join nothing are flagged."""
        self.body_fams = set(self.body_keys)
        # the book's own secondary text styles (notes, references: 5%+ of all words) are set apart from body text but
        # are not anomalies; they are never flagged
        total = sum(s["words"] for s in self.S.values()) or 1
        fam_words = collections.Counter()
        for k, s in self.S.items():
            fam_words[fam_of(k)] += s["words"]
        self.text_fams = self.body_fams | {f for f, w in fam_words.items() if w >= T["text_fam_share"] * total}
        gap = T["object_gap_em"] * self.bsize
        front = _front(self.NP)

        def fits(b, where):
            cap = bool(CAPTION.match(b["text"].replace("*", "")))
            if b["role"] in ("kısım", "bölüm", "liste", "şiir", "epigraf", "alıntı", "atıf", "dizin") or b.get("obj"):
                return False
            if b["role"] in HEAD and where == "below" and not cap:
                return False
            if b["nwords"] > T["caption_max_words"] or len(b["lines"]) > T["caption_max_lines"]:
                return False
            return cap or b["deviates"]
        for p in self.pages:
            for b in p["blocks"]:
                b["deviates"] = self.deviates(b)
            if p["p"] <= front or p["cover"] or p["p"] in self.index_pages:
                continue                        # logos and ornaments of the front pages own nothing
            blocks = [b for b in p["blocks"] if b["role"] not in ("sil", "ön sayfa", "dizin")]
            cy = lambda b: (b["top"] + b["bot"]) / 2      # a title may overlap the frame's edge by a few points
            for it in sorted(p.get("items", []), key=lambda it: (round(it["top"]), it["x0"])):
                side = [b for b in blocks if b["x1"] > it["x0"] + T["object_slack"] and b["x0"] < it["x1"] - T["object_slack"]]
                inside = [b for b in side if it["top"] <= cy(b) <= it["bot"] and fits(b, "inside")]
                mid = (it["top"] + it["bot"]) / 2
                before = [b for b in inside if cy(b) < mid]           # printed in the top half: written before it
                after = [b for b in inside if cy(b) >= mid]
                edge = it["bot"]
                for b in sorted((b for b in side if cy(b) > it["bot"]), key=lambda b: b["top"]):
                    if b["top"] - edge > gap or not fits(b, "below"):
                        break
                    after.append(b)
                    edge = b["bot"]
                edge, above = it["top"], []
                for b in sorted((b for b in side if cy(b) < it["top"]), key=lambda b: -b["bot"]):
                    if edge - b["bot"] > gap or not fits(b, "above"):
                        break
                    above.insert(0, b)
                    edge = b["top"]
                before = above + sorted(before, key=lambda b: (b["top"], b["x0"]))
                for b in before + after:
                    b["role"], b["obj"] = "altyazı", True
                it["before"], it["after"] = before, sorted(after, key=lambda b: (b["top"], b["x0"]))
            for b in p["blocks"]:
                if b["role"] in ("gövde", "kutu") and fam_of(b["style"]) not in self.text_fams and not b.get("obj"):
                    # set apart and joining nothing: in the margin band or a bare number/folio it is page furniture
                    # and goes; anything else stays, flagged for review
                    band = b.get("edge") and (b["bot"] < T["flag_band_top"] * p["H"] or b["top"] > T["band_bot"] * p["H"])
                    if band or PAGE_NO.match(b["text"]) or re.fullmatch(r"[\W\d_]*", b["text"]):
                        b["role"], b["flag_removed"] = "sil", True
                    else:
                        b["font_dev"] = True            # a deviation reason; _shape_roles names it or flags it


# ====================================================================================================================
# 6. ROLES
#   in:  blocks with margins marked
#   out: Doc.S (style statistics), Doc.sugg (role per style), block['role'] (heading levels, gövde, kutu,
#        kutu-başlık, liste, ön sayfa, dizin, sil; epigraf, alıntı, şiir, atıf), block['flags'], block['conf']
# ====================================================================================================================


class _Styles:
    """Stage 6a (Doc mixin): style statistics and the role each style suggests."""

    def _style_stats(self):
        S = collections.OrderedDict()
        for p in self.pages:
            if p["cover"]:
                continue
            for b in p["blocks"]:
                if b["sil"]:
                    continue
                s = S.setdefault(b["style"], dict(words=0, pages=set(), first=p["p"], last=p["p"], blocks=0, lines=0,
                                                  chars=0, inbox=0, caps=0, short=0, ital=0, num=0, dash=0, letters=0,
                                                  multiline=0, mlines=0, sent=0, wts=[], edge=0))
                txt = b["text"]
                letters = [c for c in txt if c.isalpha()]
                s["words"] += b["nwords"]
                s["pages"].add(p["p"])
                s["last"] = p["p"]
                s["blocks"] += 1
                s["lines"] += len(b["lines"])
                s["chars"] += len(txt)
                s["inbox"] += b["box"] is not None
                s["caps"] += (sum(c.isupper() for c in letters) / max(1, len(letters)) > T["caps_share"])
                s["letters"] += len(letters)
                s["short"] += (len(b["lines"]) <= T["short_lines"] and len(txt) <= T["short_chars"])
                s["ital"] += b["italic_share"] > T["italic_share"]
                s["num"] += bool(NUM.match(b["lines"][0]["plain"]))
                s["dash"] += bool(DASH.match(txt))
                s["multiline"] += len(b["lines"]) >= 2
                s["mlines"] += len(b["lines"]) if len(b["lines"]) >= 2 else 0
                s["sent"] += bool(re.search(r"[.!?]\s*$", txt))
                s["edge"] += b.get("edge", False)
                s["wts"] += [l["w0"] for l in b["lines"]]
        for s in S.values():
            s["weight"] = statistics.median(s["wts"]) if s["wts"] else 400
        return S

    @staticmethod
    def score(style, s):
        """heading rank: size, +2 for ALL CAPS (a caps section title outranks a mixed-case sub-title of similar size),
        +0.3 for bold"""
        return (fam_of(style)[1] + (T["score_caps"] if s["caps"] / s["blocks"] > T["caps_share"] else 0)
                + (T["score_bold"] if s["weight"] >= T["bold_weight"] or "Bold" in style else 0))

    def variant_of_body(self, k):
        """a style of a body family that is not itself a body style (the body's italic, a size step): a method, not a
        lambda, so a Doc pickles (the review screen keeps it between renders)"""
        return fam_of(k) in getattr(self, "_variant_keys", ()) and k not in getattr(self, "_variant_not", ())

    def _suggest(self):
        S = self.S
        if not S:
            self.body_keys = set()
            return {}, {}, None, set()
        body_style = max(S, key=lambda k: S[k]["words"])
        bfam, bsize = fam_of(body_style)
        # decorative front-matter styles live on the first pages; in a short document "first pages" is just a couple
        front = _front(self.NP)
        self.bfam, self.bsize = bfam, bsize
        sugg, why = {}, {}
        body_styles = {k for k, s in S.items() if s["words"] >= T["body_style_share"] * S[body_style]["words"]
                       and s["lines"] / s["blocks"] >= T["body_style_lines"]}
        body_styles.add(body_style)         # the most-worded style is body even when its paragraphs come out short
        body_keys = {fam_of(k) for k in body_styles}
        # fonts that behave like body text are not anomalies; they do not change roles (a bold run-in font is also
        # the font of the sub-heads)
        self.body_keys = body_keys | {fam_of(k) for k in self._body_variants(body_styles)}
        self._variant_keys, self._variant_not = body_keys, set(body_styles)
        for k, s in S.items():
            n = s["blocks"]
            f, sz = fam_of(k)
            avgl = s["lines"] / n
            poemlike = (f != bfam and s["mlines"] / s["lines"] >= T["poem_ml"] and avgl >= T["poem_avgl"]
                        and T["poem_size_lo"] * bsize <= sz <= T["poem_size_hi"] * bsize
                        and s["ital"] / n >= T["poem_ital"] and s["sent"] / n < T["poem_sent"])
            if s["letters"] == 0 and sz >= T["ornament_num_size"] * bsize:
                sugg[k] = ("sil", why.setdefault(k, "harf yok, büyük boy: süs rakamı"))
                continue
            if s["letters"] == 0:
                sugg[k] = ("gövde", "harf yok, küçük boy: rakam/kesir")
                continue
            if k in body_styles:
                sugg[k] = ("gövde", "en çok kelimeli stil")
                continue
            if self.variant_of_body(k) and n <= T["variant_blocks"] * max(1, S[body_style]["blocks"]):
                sugg[k] = ("gövde", "gövde ailesi/boyutunun italik-kalın varyantı")
                continue
            if s["dash"] / n >= T["style_rule_share"]:
                sugg[k] = ("atıf", "—'yle başlıyor")
                continue
            if s["inbox"] / n >= T["style_rule_share"] and s["short"] / n >= T["short_share"]:
                sugg[k] = ("kutu-başlık", "kutu içinde, kısa")
                continue
            if s["num"] / n >= T["style_rule_share"]:
                sugg[k] = ("liste", "numarayla başlıyor")
                continue
            if poemlike:
                sugg[k] = ("şiir/epigraf", "farklı aileden, çok satırlı, kısa satırlar")
                continue
            if s["last"] <= front and s["words"] <= T["front_style_words"] and not (
                    n >= T["front_para_blocks"] and avgl >= T["front_para_lines"] and s["words"] >= T["front_para_words"]):
                sugg[k] = ("ön sayfa", "yalnızca ilk sayfalarda")
                continue
        emph = lambda k: (fam_of(k)[1] > bsize * T["emph_size"] or S[k]["caps"] / S[k]["blocks"] > T["emph_caps"]
                          or S[k]["weight"] >= T["bold_weight"] or "Bold" in k)
        # a heading is at least 0.6 of the body size (small bold sub-heads exist; 4.5pt lettering above the text block
        # is a running head); a small style that mostly sits in the margin is a running head that varied too much to
        # be removed, not a heading. Display type (1.4x body and up) may run longer: a profile or chapter title that
        # names a work
        display = lambda k: fam_of(k)[1] >= T["display_size"] * bsize
        big_enough = lambda k: fam_of(k)[1] >= T["heading_min_size"] * bsize
        marginal = lambda k: S[k]["edge"] / S[k]["blocks"] >= T["marginal_share"] and fam_of(k)[1] <= T["margin_size"] * bsize
        cands = [k for k, s in S.items() if k not in sugg and s["blocks"] >= T["head_min_blocks"] and len(s["pages"]) >= T["head_min_pages"]
                 and s["chars"] / s["blocks"] >= T["head_min_chars"]
                 and s["short"] / s["blocks"] >= (T["head_short_display"] if display(k) else T["short_share"])
                 and s["words"] / s["blocks"] <= (T["head_words_display"] if display(k) else T["head_words"])
                 and s["sent"] / s["blocks"] < T["head_sent"] and emph(k) and s["inbox"] / s["blocks"] < T["head_inbox"]
                 and s["words"] >= T["head_min_words"] and s["num"] / s["blocks"] < T["head_num"] and big_enough(k)
                 and not marginal(k)]
        cands.sort(key=lambda k: -self.score(k, S[k]))
        tiers = []
        for k in cands:
            if tiers and abs(self.score(tiers[-1][0], S[tiers[-1][0]]) - self.score(k, S[k])) <= T["tier_gap"]:
                tiers[-1].append(k)
            else:
                tiers.append([k])
        for ti, tier in enumerate(tiers):
            role = ["kısım", "bölüm", "alt başlık 1", "alt başlık 1", "alt başlık 2"][ti] if ti < 5 else "alt başlık 2"
            for k in tier:
                sugg[k] = (role, f"başlık kademesi {ti + 1}/{len(tiers)}")
        for k, s in S.items():
            if k not in sugg:
                sugg[k] = ("ön sayfa" if (s["words"] <= T["front_leftover_words"] and s["last"] <= front) else "gövde", "kural eşleşmedi")
        return sugg, why, body_style, body_styles

    def _body_variants(self, body_styles):
        """Fonts are matched by behaviour, not by name (subset names like AdvP6F00/AdvP6F09 say nothing, and such
        fonts often carry no italic/bold flag either). A body variant is
        - a font set inside body lines, next to the body font at the body's size (ten such lines; three when the font
          carries the italic/bold flag), or
        - a font at the body's size and line pitch that sets paragraphs of its own: a fifth of its blocks run to
          several lines and they end a sentence at least half as often as body paragraphs do (emphasised
          statements, an italic body)."""
        pitch = collections.defaultdict(list)
        for p in self.pages:
            for b in p["blocks"]:
                if not b["sil"]:
                    pitch[b["style"]] += [y["base"] - x["base"] for x, y in zip(b["lines"], b["lines"][1:])]
        main = max(body_styles, key=lambda k: self.S[k]["words"]) if body_styles else None
        bp = statistics.median(pitch[main] or [0]) if main else 0
        bsz = {fam_of(k)[1] for k in body_styles}
        own = {k for k, s in self.S.items() if k not in body_styles and bp and pitch[k]
               and any(abs(fam_of(k)[1] - z) <= T["variant_size_pt"] for z in bsz)
               and abs(statistics.median(pitch[k]) - bp) <= T["variant_pitch"] * bp
               and s["multiline"] >= T["variant_multiline"] * s["blocks"]
               and s["sent"] / s["blocks"] >= T["variant_sent"] * self.S[main]["sent"] / self.S[main]["blocks"]}
        inside, flagged = collections.Counter(), collections.Counter()
        for p in self.pages:
            for b in p["blocks"]:
                if b["sil"]:
                    continue
                for l in b["lines"]:
                    if l["style"] not in body_styles:
                        continue
                    mix = l.get("mix", {})
                    for k in mix:
                        if k != l["style"] and abs(fam_of(k)[1] - fam_of(l["style"])[1]) <= T["variant_size_pt"]:
                            inside[k] += 1
                            flagged[k] += l["mix_flag"][k] >= T["variant_flagged"] * mix[k]
        return own | {k for k, n in inside.items()
                      if n >= T["variant_lines"] or (n >= T["variant_lines_flagged"] and flagged[k] >= T["variant_flagged"] * n)}


class _Roles:
    """Stage 6b (Doc mixin): block roles from the style suggestions, one-off headings, the index."""

    def _heading_for(self, block):
        """role for a one-off heading-like block, taken from the heading style whose rank is closest"""
        S = self.S
        letters = [c for c in block["text"] if c.isalpha()]
        caps = sum(c.isupper() for c in letters) / max(1, len(letters)) > T["caps_share"]
        w = block["lines"][0]["w0"]
        sc = block["size"] + (T["score_caps"] if caps else 0) + (T["score_bold"] if w >= T["bold_weight"] else 0)
        best = None
        for k, (r, _) in self.sugg.items():
            if r in HEAD and S[k]["blocks"] >= T["head_min_blocks"]:
                d = abs(self.score(k, S[k]) - sc)
                if best is None or d < best[0]:
                    best = (d, r)
        return best[1] if best else None

    def _block_roles(self):
        sugg = self.sugg
        front = _front(self.NP)
        titles = set()
        for pi, p in enumerate(self.pages):
            prev = None
            real = [b for b in p["blocks"] if not b["sil"]]
            # a front page set in display type only (title page, author's name) is no chapter heading, and neither is
            # a later half title that repeats it
            title_page = False
            if real and all(b["size"] >= T["title_page_size"] * self.bsize for b in real):
                key = "".join(c for b in real for c in b["text"].lower() if c.isalpha())
                title_page = pi < front or key in titles
                if pi < front:
                    titles.add(key)
            for b in p["blocks"]:
                r = "sil" if b["sil"] else sugg.get(b["style"], ("gövde",))[0]
                if p["cover"] or (title_page and r != "sil"):
                    r = "ön sayfa"
                # a paragraph whose dominant style is a heading style only through its runs (long italic titles
                # among body text) is no heading; a long title set wholly in its style still is
                mix = b.get("mix") or {}
                if (r in HEAD and len(b["lines"]) > T["runs_para_lines"] and b["nwords"] > T["runs_para_words"]
                        and sum(n for k, n in mix.items() if k in self.body_styles) >= T["runs_body_share"] * sum(mix.values())):
                    r = "gövde"
                txt = b["text"]
                if r not in ("sil", "ön sayfa"):
                    if r == "şiir/epigraf":
                        if len(b["lines"]) == 1 and b["nwords"] <= T["poem_one_line_words"]:
                            r = self._heading_for(b) or "gövde"
                        else:
                            r = "epigraf" if len(b["lines"]) <= T["epigraph_lines"] else "şiir"
                    if r != "kutu-başlık" and DASH.match(txt) and len(b["lines"]) <= T["attrib_lines"] and prev in ("şiir", "epigraf"):
                        r = "atıf"
                    if NUM.match(b["lines"][0]["plain"]) and r in ("gövde", "liste"):
                        r = "liste"
                    if b["box"] is not None and r == "gövde":
                        r = "kutu"
                b["role"] = r
                if r != "sil":
                    prev = r
            # one-off headings: a short single line in a non-body style, between body paragraphs
            for i, b in enumerate(real):
                if b["role"] != "gövde" or b["box"] is not None or p["cover"]:
                    continue
                st = b["style"]
                if (st in self.body_styles or self.variant_of_body(st) or len(b["lines"]) > T["oneoff_lines"]
                        or b["nwords"] > T["oneoff_words"]):
                    continue
                txt = b["text"].strip()
                if not txt or re.search(r"[.,;!?]\s*$", txt) or NUM.match(txt):
                    continue
                letters = [c for c in txt if c.isalpha()]
                caps = sum(c.isupper() for c in letters) / max(1, len(letters)) > T["caps_share"]
                if not (b["size"] >= T["emph_size"] * self.bsize or (caps and b["size"] >= T["oneoff_caps_size"] * self.bsize)):
                    continue
                if i > 0 and real[i - 1]["style"] == st:
                    continue
                if i + 1 >= len(real) or real[i + 1]["role"] not in ("gövde", "kutu", "şiir", "epigraf", "liste"):
                    continue
                role = self._heading_for(b)
                if role:
                    b["role"] = role
            # first block of a box whose style differs from the box majority (and short) is its title
            byb = collections.defaultdict(list)
            for b in p["blocks"]:
                if b["box"] is not None and b["role"] in ("kutu", "gövde", "kutu-başlık"):
                    byb[b["box"]].append(b)
            for bl in byb.values():
                maj = collections.Counter(b["style"] for b in bl).most_common(1)[0][0]
                if bl[0]["style"] != maj and len(bl[0]["lines"]) <= T["box_title_lines"]:
                    bl[0]["role"] = "kutu-başlık"
            # a heading follows a block that ended: one in the same column, size and flow (below it, not overlaid) after
            # a sentence left open with no extra space above, or starting in lowercase, is the paragraph going on
            prev = None
            for b in real:
                if (b["role"] in HEAD and prev is not None and prev["role"] in ("gövde", "kutu", "liste")
                        and not terminal(prev["text"]) and prev["box"] == b["box"] and abs(b["x0"] - prev["x0"]) < T["net_col_em"] * self.bsize
                        and abs(b["size"] - prev["size"]) <= T["inline_size_pt"] and b["top"] >= prev["bot"] - T["net_overlay_pt"]
                        and (b["top"] - prev["bot"] < T["net_gap_em"] * self.bsize or re.match(r"[*_\W]*[a-z]", b["text"]))):
                    b["role"], b["cont"] = prev["role"] if prev["role"] != "liste" else "gövde", True
                if b["role"] != "sil":
                    prev = b

    def _index_pages(self):
        """A back-of-book index: pages in the book's last quarter whose lines are mostly short entries ending in
        page numbers, in alphabetical order. Its blocks (not its heading) leave the flow."""
        self.index_pages = []
        for p in self.pages:
            if p["p"] < T["index_start"] * self.NP:
                continue
            ls = [l["plain"].strip() for b in p["blocks"] if not b["sil"] for l in b["lines"]]
            ls = [x for x in ls if x]
            if len(ls) < T["index_min_lines"]:
                continue
            num = sum(1 for x in ls if re.search(r"\d+(?:[–-]\d+)?[.,;)]?$", x) or re.search(r",\s*\d", x))
            first = [x[0].lower() for x in ls if x[:1].isalpha() and x[:1].isupper()]
            order = sum(1 for a, b in zip(first, first[1:]) if a <= b) / max(1, len(first) - 1)
            if (num >= T["index_num_share"] * len(ls) and statistics.median(len(x) for x in ls) <= T["index_entry_len"]
                    and len(first) >= T["index_min_alpha"] and order >= T["index_order"]):
                self.index_pages.append(p["p"])
        # the first and last index page can be short: grow the run while the neighbours still look like entries
        def entries(p):
            ls = [l["plain"].strip() for b in p["blocks"] if not b["sil"] for l in b["lines"]]
            ls = [x for x in ls if x]
            return len(ls) >= T["index_grow_lines"] and statistics.median(len(x) for x in ls) <= T["index_entry_len"] and sum(
                1 for x in ls if re.search(r"\d+(?:[–-]\d+)?[.,;)]?$", x) or re.search(r",\s*\d", x)) >= T["index_num_share"] * len(ls)
        if self.index_pages:
            lo, hi = self.index_pages[0], self.index_pages[-1]
            while lo > 1 and entries(self.pages[lo - 2]):
                lo -= 1
            while hi < self.NP and entries(self.pages[hi]):
                hi += 1
            self.index_pages = list(range(lo, hi + 1))
        for p in self.pages:
            if p["p"] in self.index_pages:
                for b in p["blocks"]:
                    if b["role"] not in HEAD and b["role"] != "sil":
                        b["role"] = "dizin"


class _ShapeRoles:
    """Stage 6c (Doc mixin): named block roles from shape: epigraph, block quote, verse, attribution."""

    # -- named block roles from shape: epigraph, block quote, verse, attribution, box -------------------------------
    def _columns(self):
        """the body text column of every page: (left, right) from its body paragraphs (first lines may be indented,
        last lines short, so those are left out); pages without enough body take the median of pages of the same
        parity (facing pages mirror their margins)"""
        col, par = {}, {0: ([], []), 1: ([], [])}
        for p in self.pages:
            xs, rs = [], []
            for b in p["blocks"]:
                if b["role"] == "gövde" and b["style"] in self.body_styles and b["box"] is None and len(b["lines"]) >= 2:
                    xs += [l["x0"] for l in b["lines"][1:]]
                    rs += [l["x1"] for l in b["lines"][:-1]]
            if len(xs) >= T["column_min_lines"]:
                col[p["p"]] = (statistics.median(xs), statistics.median(rs))
                par[p["p"] % 2][0].append(col[p["p"]][0])
                par[p["p"] % 2][1].append(col[p["p"]][1])
        every = par[0][0] + par[1][0], par[0][1] + par[1][1]
        for p in self.pages:
            if p["p"] not in col:
                L, R = par[p["p"] % 2] if par[p["p"] % 2][0] else every
                col[p["p"]] = (statistics.median(L), statistics.median(R)) if L else (T["column_fallback_l"] * p["W"], T["column_fallback_r"] * p["W"])
        return col

    def _geometry(self, b, L, R):
        """indents against the body column, width, alignment and line-length spread of one block"""
        ls, z = b["lines"], b["size"]
        x0s, x1s = [l["x0"] for l in ls], [l["x1"] for l in ls]
        n, colw = len(ls), max(1.0, R - L)
        g = dict(n=n, first=x0s[0] - L, left=(statistics.median(x0s[1:]) if n > 1 else x0s[0]) - L,
                 right=R - (statistics.median(x1s[:-1]) if n > 1 else x1s[0]), width=max(l["x1"] - l["x0"] for l in ls) / colw)
        g["centered"] = all(abs((l["x0"] - L) - (R - l["x1"])) < T["align_em"] * z and l["x0"] - L > T["center_min_em"] * z for l in ls)
        g["flush_right"] = all(abs(R - l["x1"]) < T["align_em"] * z for l in ls) and x0s[0] - L > T["right_min_share"] * colw
        widths = [l["x1"] - l["x0"] for l in ls]
        top = max(x1s)
        g["justified"] = n >= 2 and all(abs(top - x) < T["align_em"] * z for x in x1s[:-1])
        # spread of the line lengths, a paragraph's short last line left out
        g["spread"] = statistics.pstdev(widths[:-1]) / statistics.mean(widths[:-1]) if n >= 3 else 0.0
        g["hyphenated"] = any(re.search(r"[A-Za-z][\x02-]$", l["plain"].rstrip()) for l in ls[:-1])
        return g

    def _reasons(self, b, g):
        """what sets a block apart from the book's normal paragraph (calibrated on the test books: a body paragraph
        sits flush with the column within 1.2 body sizes, its first line indented by the book's usual amount)"""
        z, pr = self.bsize, self.profile
        r = []
        if b.get("font_dev"):
            r.append("font")
        if g["centered"]:
            r.append("centered")
        elif g["flush_right"]:
            r.append("right")
        else:
            # a body paragraph's lines after the first sit flush (within a point or two); an indented block's do not
            if (g["left"] > T["indent_shape_em"] * z) if g["n"] > 1 else (g["first"] - pr["first"] > T["first_indent_em"] * z):
                r.append("indent")
            if g["n"] > 1 and (g["right"] > T["right_indent_em"] * z
                                or (g["right"] > T["indent_shape_em"] * z and g["left"] > T["indent_shape_em"] * z)):
                r.append("indent-right")
        if g["n"] >= 2 and g["width"] < T["narrow"]:
            r.append("narrow")
        if g["n"] >= 3 and g["spread"] > T["ragged"] and not g["justified"]:
            r.append("ragged")
        if b["italic_share"] > T["italic_share"] and pr["italic"] < T["body_italic_max"]:
            r.append("italic-block")
        if b["size"] < z - T["body_size_pt"]:
            r.append("smaller")
        if g["n"] > 1 and g["left"] - g["first"] > T["indent_shape_em"] * z:
            r.append("hanging")             # a reference or list entry: first line out, the rest indented
        return r

    @staticmethod
    def _nameable(b):
        """hanging-indent entries, list items, references and lists of web addresses are set apart, but never a quote
        or verse"""
        return ("hanging" not in b["why"] and not ITEM.match(b["text"]) and not re.search(r"://|www\.", b["text"])
                and not re.search(r"\(\d{4}[a-z]?\)|\bet al\.|\bdoi\b|\bpp\. \d", b["text"]))

    def _shape_roles(self):
        """Every body-like block is measured against the book's normal paragraph; what deviates is named by rule:
        - attribution: a short line that starts with a dash or sits flush right, right after an epigraph, quote or
          verse (a second flush-right line, the work's title, joins it);
        - epigraph: set apart, right after a heading (chapter or part start), body text following;
        - verse: three or more short lines of irregular length, set apart, not justified;
        - block quote: indented (one or both sides) inside the body flow, body text on both sides;
        - box: the tinted/framed box logic.
        Each named block keeps its reasons in flags and a confidence; a deviation no rule explains keeps the review
        flag ("stil-farkli")."""
        col = self._columns()
        body = [b for p in self.pages for b in p["blocks"] if b["role"] == "gövde" and b["style"] in self.body_styles
                and b["box"] is None]
        firsts = [b["lines"][0]["x0"] - col[b["page"]][0] for b in body if len(b["lines"]) >= 2]
        self.profile = dict(first=statistics.median(firsts) if firsts else 0.0,
                            italic=statistics.median([b["italic_share"] for b in body]) if body else 0.0)
        flow = [(p, b) for p in self.pages for b in p["blocks"]
                if b["role"] not in ("sil", "ön sayfa", "dizin", "altyazı") and not b.get("obj")]
        for p, b in flow:
            if b["role"] in ("gövde", "kutu", "şiir", "epigraf", "atıf"):
                b["geo"] = self._geometry(b, *col[p["p"]])
                b["why"] = self._reasons(b, b["geo"])
        named = ("epigraf", "alıntı", "şiir")
        front = {p["p"] for p in self.pages[:_front(self.NP)]}
        # epigraphs first: right after a heading of the top two levels (a sub-section's own title starts none), a run
        # of at most four short set-apart blocks (60 words each, 150 in all), an attribution possibly among them, and
        # then the body resumes with a normal paragraph or a sub-heading
        for k, (p, b) in enumerate(flow):
            if b["role"] not in ("kısım", "bölüm", "alt başlık 1"):
                continue
            run, words, j = [], 0, k + 1
            while (j < len(flow) and len(run) < T["epigraph_blocks"] and flow[j][1].get("why") and flow[j][1]["role"] not in HEAD
                   and flow[j][1]["nwords"] <= T["epigraph_block_words"] and self._nameable(flow[j][1])):
                run.append(flow[j][1])
                words += flow[j][1]["nwords"]
                j += 1
            nb = flow[j][1] if j < len(flow) else None
            if run and words <= T["epigraph_words"] and nb is not None and ((nb["role"] == "gövde" and nb.get("why") == []) or nb["role"] in HEAD):
                for x in run:
                    if (x is not run[0] and len(x["lines"]) <= T["attrib_max_lines"] and x["nwords"] <= T["attrib_max_words"]
                            and (DASH.match(x["text"]) or "right" in x["why"])):
                        x["role"] = "atıf"      # named in the main pass below, with its title line
                    else:
                        x["role"], x["conf"], x["start"] = "epigraf", 0.9 if {"indent", "italic-block"} & set(x["why"]) else 0.7, True
                        # an epigraph set in lines (a poem): most lines stop well short of the longest one
                        ws = [l["x1"] - l["x0"] for l in x["lines"]]
                        x["breaks"] = (len(ws) >= T["verse_min_lines"]
                                       and sum(w < T["verse_short_line"] * max(ws) for w in ws[:-1]) >= T["verse_short_share"] * (len(ws) - 1))
        k = 0
        while k < len(flow):
            p, b = flow[k]
            prev = flow[k - 1][1] if k else None
            nxt = flow[k + 1][1] if k + 1 < len(flow) else None
            k += 1
            if b["role"] in ("kutu", "kutu-başlık"):
                b["conf"] = 0.9
                continue
            # a source line set in a heading style ("—A NAME (1988, p. 140)" in small caps) is still the attribution
            if (b["role"] in HEAD and prev is not None and prev["role"] in named and DASH.match(b["text"].strip("* "))
                    and len(b["lines"]) <= T["attrib_head_lines"] and b["nwords"] <= T["attrib_head_words"]):
                b["role"], b["conf"] = "atıf", 0.8
                continue
            if "why" not in b:
                continue
            why, g = b["why"], b["geo"]
            shape = set(why) - {"ragged"}
            short = len(b["lines"]) <= T["attrib_max_lines"] and b["nwords"] <= T["attrib_max_words"]
            # a source line: dash, flush right, a name in parentheses, or a few italic words ("Female, 16-17")
            source = (DASH.match(b["text"]) or "right" in why or re.fullmatch(r"\(.{2,80}\)", b["text"].strip("* "))
                      or (b["nwords"] <= T["attrib_italic_words"] and "italic-block" in why))
            if prev is not None and prev["role"] in named and short and source:
                b["role"], b["conf"] = "atıf", 0.9 if DASH.match(b["text"]) else 0.7
                # the work's title on its own line, right edge on the attribution's, belongs to the attribution
                while (nxt is not None and abs(nxt["x1"] - b["x1"]) < T["attrib_edge_pt"] and len(nxt["lines"]) <= T["attrib_lines"]
                       and nxt["size"] <= b["size"] + T["body_size_pt"] and nxt["page"] == b["page"]
                       and 0 <= nxt["top"] - b["bot"] < T["attrib_gap_em"] * b["size"]):
                    b["lines"] += nxt["lines"]
                    _finish_block(b)
                    p["blocks"].remove(nxt)
                    flow.pop(k)
                    nxt = flow[k][1] if k < len(flow) else None
                continue
            if b["role"] == "atıf" and prev is not None and prev["role"] in named:
                b["conf"] = 0.8
                continue
            # a chapter or part start: a heading of the top two levels (a sub-section's own title starts no epigraph),
            # or an epigraph that began there
            if b.get("start"):
                b["flags"] = why
                continue
            # a quote or poem cut by a page break (or by a note number between its parts) goes on at the same left edge
            # and size
            if (prev is not None and prev["role"] in ("alıntı", "şiir") and abs(b["x0"] - prev["x0"]) < T["continue_x_pt"]
                    and abs(b["size"] - prev["size"]) < T["continue_size_pt"]
                    and (prev["page"] != b["page"] or (not terminal(prev["text"]) and re.match(r"[*_]*[a-z]", b["text"])))):
                b["role"], b["conf"], b["flags"] = prev["role"], prev.get("conf"), why
                continue
            if not self._nameable(b):
                b["flags"] = why + ["stil-farkli"] if b.get("font_dev") else why
                continue
            body_next = nxt is not None and nxt["role"] in ("gövde", "kutu", "liste", "atıf") + tuple(HEAD)
            # a block drawn over another block's lines (stray pieces of a second text layer) is no quote or verse
            ov = T["overlap_pt"]
            over = any(o is not b and o["role"] != "sil" and o["top"] < b["bot"] - ov and o["bot"] > b["top"] + ov
                       and o["x0"] < b["x1"] - ov and o["x1"] > b["x0"] + ov for o in p["blocks"])
            layout = shape & {"indent", "indent-right", "narrow", "centered", "italic-block"}
            # verse: short irregular lines of words set apart by layout (a list of web addresses is not verse)
            # (prose broken over lines splits words at line ends; verse lines end with whole words)
            verse = (len(b["lines"]) >= T["verse_min_lines"] and "ragged" in why and not g["justified"] and bool(layout) and not g["hyphenated"]
                     and not re.search(r"://|www\.", b["text"]))
            if not over and (verse or (b["role"] in ("şiir", "epigraf") and shape and not g["hyphenated"])):
                # (the style rule names short-lined multi-line blocks şiir/epigraf; away from a chapter start: verse)
                b["role"], b["conf"] = "şiir", 0.9 if b["role"] == "şiir" else 0.7
            elif (not over and ("indent" in why or "indent-right" in why) and "centered" not in why and prev is not None
                  and prev["role"] in ("gövde", "liste", "alıntı") + tuple(HEAD) and body_next):
                b["role"], b["conf"] = "alıntı", 0.8 if "indent" in why and "indent-right" in why else 0.6
            elif b["role"] in ("şiir", "epigraf"):
                b["conf"] = 0.5                 # named by its style only
            elif why:
                # italics or a smaller size alone are emphasis or notes, not a layout to review; nor is the layout of
                # the front pages (title, copyright, dedication, contents are set centered or narrow)
                b["flags"] = why + ["stil-farkli"] if shape - {"smaller", "italic-block"} and p["p"] not in front else why
                continue
            b["flags"] = why


FOOT_START = re.compile(r"^\s*[*_]*(\d{1,3}|[" + NOTE_SIGNS + r"]{1,3}|([a-z])\2?(?![a-z]))[.)]?[*_]*\s*(?=\S)")


class _Notes:
    """Stage 6d (Doc mixin): page-bottom footnotes. Below everything set at body size, smaller, starting with a number
    (or sign) that a callout on the same page carries; the lines up to the next such start belong to it, also when
    its hanging-indented lines came out as a block of their own (same style). The bottom-edge blocks the role stage
    removed as stray margin text are read too, and a "caption" under a figure that starts with a callout's number;
    margin lines proper (folios, running feet) are not. Small lines at the
    foot of the next page before any number go on with the last note when it ended mid-sentence.
    Sets role "dipnot" and b["foot"] = [(number, or None for a continuation, lines)]; the review flags go, a footnote
    is explained."""

    def _footnotes(self):
        if not self.body_style:
            return
        bsize = fam_of(self.body_style)[1]
        small = lambda b: b["size"] <= T["foot_size"] * bsize
        last = None                     # (style, text) of the last note, while it may go on overleaf
        for p in self.pages:
            live = [b for b in p["blocks"] if b["role"] not in ("sil", "ön sayfa", "dizin", "altyazı")
                    or (b["role"] == "sil" and b.get("flag_removed") and not b.get("sil"))]
            floor = max((b["bot"] for b in live if not small(b)), default=None)
            if floor is None:
                last = None
                continue
            refs = {r for b in live if not small(b) for l in b["lines"] for r in l["refs"]}
            # a note under a figure was taken for its caption: it starts with a callout's number
            live += [b for b in p["blocks"] if b["role"] == "altyazı" and FOOT_START.match(b["lines"][0]["plain"])
                     and FOOT_START.match(b["lines"][0]["plain"]).group(1) in refs]
            seen, carry, open_ = set(), last, None      # open_: style of the note still open on this page
            last = None
            for b in sorted((b for b in live if small(b) and b["top"] >= floor and b["box"] is None), key=lambda b: b["top"]):
                notes = []
                for l in b["lines"]:
                    m = FOOT_START.match(l["plain"])
                    if m and m.group(1) in refs and m.group(1) not in seen:
                        seen.add(m.group(1))
                        notes.append((m.group(1), [l]))
                    elif notes:
                        notes[-1][1].append(l)
                    elif open_ == b["style"] or (not seen and carry and carry[0] == b["style"] and not terminal(carry[1])):
                        notes.append((None, [l]))
                    else:
                        break
                else:
                    if notes:
                        b["role"], b["foot"], b["flags"], b["conf"] = "dipnot", notes, [], None
                        b.pop("flag_removed", None)
                        for it in p.get("items", []):
                            for k in ("before", "after"):
                                if k in it:
                                    it[k] = [x for x in it[k] if x is not b]
                        last, open_ = (b["style"], notes[-1][1][-1]["plain"]), b["style"]
                        continue
                last = open_ = None


# a contents page's own title; the page itself is found by its rows, with or without it
TOC_TITLE = re.compile(r"^[*_\s]*(?:table of )?(?:contents|i̇çindekiler|içindekiler|sommaire|inhalt|indice|índice)[*_.:\s]*$", re.I)
ROMAN = re.compile(r"^(?=[ivxlc])m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})$", re.I)
# a row ending in its page number, after a space or a dot leader ("Title ....... 12", "Title 12", "Title  xii")
TOC_ROW = re.compile(r"^(?P<t>.*?\S)(?:\s*[.·…_]{2,}\s*|\s+)(?P<n>[^\s.·…_]{1,6})$")
# a chapter's label on a line of its own above its title ("Chapter One"): part of that entry; a part's ("Part II")
# is an entry of its own
TOC_LABEL = re.compile(r"^(?:chapter|section|bölüm)\s+\S+$", re.I)
TOC_PART = re.compile(r"^(?:part|book|kısım)\s+\S+$", re.I)
TOC_PART_START = re.compile(r"^(?:part|book|kısım)\s+(?:[0-9]+|[ivxlc]+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.I)


def toc_norm(t):
    """a title as compared with a heading: no "Chapter 3:" or leading number, letters and digits only"""
    whole = t = t.replace("*", "")
    t = unicodedata.normalize("NFKC", t.replace("*", "")).lower()
    t = re.sub(r"^\s*(?:chapter|part|section|book|bölüm|kısım)\s+(?:[0-9]+|[ivxlcdm]+|[a-z]+)\s*[:.\-–—]?\s*", "", t)
    t = re.sub(r"^\s*(?:[0-9]+|[ivxlcdm]+)[.):\t ]+\s*", "", t)
    return re.sub(r"[\W_]+", "", t) or re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", whole).lower())   # "Part II" alone


def toc_same(a, b):
    """two normalised titles name the same heading: one inside the other (most of it), or nearly equal"""
    if not a or not b:
        return False
    if len(a) >= 8 and len(b) >= 8 and (a in b or b in a) and min(len(a), len(b)) >= T["toc_contain"] * max(len(a), len(b)):
        return True
    sm = difflib.SequenceMatcher(None, a, b)
    return sm.real_quick_ratio() >= T["toc_similar"] and sm.quick_ratio() >= T["toc_similar"] and sm.ratio() >= T["toc_similar"]


class _Contents:
    """Stage 6e (Doc mixin): the table of contents. Found by structure on the first pages of the book: consecutive
    pages most of whose rows end in a page number (after a space or a dot leader), the numbers not going down, or in
    one short mark repeated at one right edge (a proof's "xx"); a "Contents" title is not needed. Its blocks get the
    role "içindekiler" and stay out of the text (the user can bring them back); the style statistics never see them.
    Its entries (title, printed page, level by indent or, when centred, by size) are matched to the body's headings
    near the PDF page the printed number maps to (the folios found in the margins); the levels of the matched
    headings follow the contents: a level whose entries are each followed by entries of the next is the parts
    (kısım, then bölüm), any other level takes the heading role most of its entries have."""

    def _toc_rows(self, p):
        ls = [l for b in p["blocks"] for l in b["lines"] + b.get("cut_lines", []) if not l.get("sil")]
        # a page number at the foot of the list was taken for the folio: it goes back when a row stands beside it
        ls += [l for b in p["blocks"] for l in b["lines"] + b.get("cut_lines", []) if l.get("sil") and PAGE_NO.match(l["plain"])
               and any(abs(l["base"] - x["base"]) <= T["toc_row_em"] * x["size"] and x["x1"] < l["x0"] for x in ls)]
        ls.sort(key=lambda l: (l["base"], l["x0"]))
        rows = []
        for l in ls:
            if rows and abs(l["base"] - rows[-1][-1]["base"]) <= T["toc_row_em"] * l["size"]:
                rows[-1].append(l)
            else:
                rows.append([l])
        out = []
        for r in rows:
            r.sort(key=lambda l: l["x0"])
            text = " ".join(l["plain"].replace("\x02", "-").strip() for l in r).strip()
            if not text:
                continue
            # a page number set a little lower than its title's last line goes with it
            if out and re.fullmatch(r"\d{1,4}|[ivxlcdm]{1,7}", text, re.I) and r[0]["x0"] > out[-1]["x1"] \
                    and not TOC_ROW.match(out[-1]["text"]):
                out[-1]["text"] += " " + text
                out[-1]["lines"] += r
                out[-1]["x1"], out[-1]["bot"] = r[-1]["x1"], max(out[-1]["bot"], max(l["bot"] for l in r))
                continue
            out.append(dict(lines=r, text=text, x0=r[0]["x0"], x1=r[-1]["x1"], top=min(l["top"] for l in r),
                            bot=max(l["bot"] for l in r), size=r[0]["size"], style=r[0]["style"], page=p["p"]))
        # a proof's placeholder for the page numbers ("xx", "000"): one short mark, no word (justified prose ends many
        # lines in "the" at one edge), ending many rows at one right edge
        ends = collections.defaultdict(list)
        for row in out:
            m = TOC_ROW.match(row["text"])
            if m and len(m.group("n")) <= 3:
                ends[m.group("n")].append(row["x1"])
        marks = {k for k, xs in ends.items() if len(xs) >= T["toc_rows"] and re.fullmatch(r"(.)\1+|[^\w]+", k)
                 and max(xs) - min(xs) <= 0.5 * statistics.median(r["size"] for r in out)}
        for row in out:
            row["title"], row["num"] = row["text"], False
            m = TOC_ROW.match(row["text"])
            if not m or TOC_LABEL.match(row["text"]) or TOC_PART.match(row["text"]):
                continue
            n = m.group("n")
            if n in marks:
                row["title"], row["num"] = m.group("t"), None
            elif n.isdigit() and 0 < int(n) <= T["toc_page_max"] * self.NP:
                row["title"], row["num"] = m.group("t"), n
            elif ROMAN.match(n) and _roman(n) <= 60:
                row["title"], row["num"] = m.group("t"), n.lower()
        return out

    def _toc_page(self, rows):
        """a contents page: enough rows ending in a page number, their numbers not going down"""
        rows = [r for r in rows if not TOC_TITLE.match(r["text"]) and not TOC_LABEL.match(r["text"])]
        numbered = [r for r in rows if r["num"] is not False]
        arabic = [int(r["num"]) for r in numbered if r["num"] and r["num"].isdigit()]
        up = sum(b >= a for a, b in zip(arabic, arabic[1:])) >= T["toc_order"] * (len(arabic) - 1) if len(arabic) >= 2 else True
        return len(numbered) >= T["toc_rows"] and len(numbered) >= T["toc_row_share"] * len(rows) and up

    def _contents(self):
        self.toc = None
        front = max(T["toc_front_pages"], int(T["toc_front_share"] * self.NP))
        run, rows_of = [], {}
        for p in self.pages[:front]:
            if p["cover"]:
                continue
            rows = rows_of[p["p"]] = self._toc_rows(p)
            titled = any(TOC_TITLE.match(r["text"]) for r in rows)
            if self._toc_page(rows) or (titled and not run and any(r["num"] is not False for r in rows)):
                if run and p["p"] != run[-1] + 1:
                    break
                run.append(p["p"])
            elif run:
                break
        if not run:
            return
        # the entries: rows without a number go on into the next numbered row (a wrapped title, a "Chapter One"
        # label); one set in another style is an entry of its own (a part's title)
        entries, acc = [], []
        same_style = lambda a, b: (abs(a["size"] - b["size"]) <= 0.5 and ("Bold" in a["style"]) == ("Bold" in b["style"])
                                   and fam_of(a["style"])[0] == fam_of(b["style"])[0])

        def entry(rs, num, last, own=False):
            # own: set apart from the numbered entries (another style, no number, or "Part II ..."): a part's title.
            # Its place on the line is its title's first row, not its label's
            first = next((r for r in rs if not label(r)), rs[0])
            title = " ".join(r["title"] if r is last else r["text"] for r in rs)
            return dict(title=title, num=num, x0=first["x0"], own=own, part=bool(TOC_PART_START.match(title)), _last=last,
                        center=(first["x0"] + first["x1"]) / 2,
                        size=last["size"], style=last["style"], toc_page=rs[0]["page"],
                        box=[rs[0]["page"], round(min(r["x0"] for r in rs), 1), round(rs[0]["top"], 1),
                             round(max(r["x1"] for r in rs), 1), round(last["bot"], 1)])
        # a chapter label's style ("Chapter 6" set apart): other rows in it are labels too ("Epilogue", "Appendix")
        label_styles = {r["style"] for pg in run for r in rows_of[pg] if TOC_LABEL.match(r["text"])}
        label = lambda r: bool(TOC_LABEL.match(r["text"]) or r["style"] in label_styles or r["text"].endswith(":"))
        titled = False
        for pg in run:
            for r in rows_of[pg]:
                if TOC_TITLE.match(r["text"]):
                    titled = True
                    continue
                caps = lambda x: bool(re.search(r"[A-Z]{2,}[\W\d]*$", x["title"])) and x["title"].upper() == x["title"] or bool(
                    re.search(r"\b[A-Z]{2,}(?:\s+[A-Z]{2,})+[\W\d]*$", x["title"]))
                if r["num"] is False and not acc and entries and same_style(entries[-1]["_last"], r) and (
                        LOWER_START.match(r["text"]) or re.search(r"[,:;–—-]\s*$", entries[-1]["title"])
                        or caps(r) and caps(entries[-1]["_last"])):
                    # the number stood on the entry's first line: this row is its title going on
                    entries[-1]["title"] += " " + r["text"]
                    entries[-1]["box"][3], entries[-1]["box"][4] = max(entries[-1]["box"][3], round(r["x1"], 1)), round(r["bot"], 1)
                    entries[-1]["_last"] = r
                    continue
                if r["num"] is False:
                    # back at a left edge after a hanging wrap: the entry before had no page number
                    if acc and r["x0"] < acc[-1]["x0"] - T["toc_indent_em"] * r["size"] and len(acc) >= 2 \
                            and acc[-1]["x0"] > acc[0]["x0"] + T["toc_indent_em"] * r["size"]:
                        entries.append(entry(acc, None, acc[-1]))
                        acc = []
                    acc.append(r)
                    continue
                k = len(acc)
                while k and (same_style(acc[k - 1], r) or label(acc[k - 1])) and not TOC_PART.match(acc[k - 1]["text"]):
                    k -= 1                              # the rows that go with this entry: its wrap, its label
                group = []
                for x in acc[:k] + [None]:              # a part's title is an entry of its own; rows between, one
                    if x is None or TOC_PART.match(x["text"]):
                        if group:
                            entries.append(entry(group, None, group[-1], own=True))
                        group = [x] if x else []
                        if x:
                            entries.append(entry(group, None, x, own=True))
                            group = []
                    else:
                        group.append(x)
                entries.append(entry(acc[k:] + [r], r["num"], r))
                acc = []
        entries += [entry([r], None, r, own=True) for r in acc]
        # "Part II ..." with a page number is a part's title when it is set apart (no number, another style)
        common = collections.Counter(e["style"] for e in entries).most_common(1)[0][0] if entries else None
        for e in entries:
            if e.pop("part") and (e["num"] is None or e["style"] != common):
                e["own"] = True
        # levels: a part's title over the numbered entries; these by indent when they line up on a few left edges
        # (per page: pages are offset), else (centred) by size
        own = [e for e in entries if e["own"]]
        entries_n = [e for e in entries if not e["own"]] or entries
        for e in entries_n:
            e["rel"] = e["x0"] - min(x["x0"] for x in entries_n if x["toc_page"] == e["toc_page"])
        xs = sorted(e["rel"] for e in entries_n)
        size = statistics.median(e["size"] for e in entries_n)
        edges = []
        for x in xs:
            if not edges or x - edges[-1][-1] > T["toc_indent_em"] * size:
                edges.append([x])
            else:
                edges[-1].append(x)
        big = sorted(edges, key=len, reverse=True)[:3]
        cs = [e["center"] - min(x["x0"] for x in entries_n if x["toc_page"] == e["toc_page"]) for e in entries_n]
        centred = max(xs) - min(xs) > 2 * T["toc_indent_em"] * size and max(cs) - min(cs) < 0.5 * (max(xs) - min(xs))
        if not centred and (len(edges) <= 3 or sum(len(e) for e in big) >= 0.9 * len(entries_n)):
            starts = sorted(e[0] for e in big)
            for e in entries_n:
                e["level"] = 1 + max(k for k, x in enumerate(starts) if e["rel"] >= x - 0.01) if e["rel"] >= starts[0] - 0.01 else 1
        else:
            sizes = sorted({round(e["size"] * 2) / 2 for e in entries_n}, reverse=True)
            for e in entries_n:
                e["level"] = 1 + sizes.index(round(e["size"] * 2) / 2)
        if own and len(own) < len(entries):
            for e in entries_n:
                e["level"] += 1
            for e in own:
                e["level"] = 1
        # printed page -> PDF page: the usual offset of the folios in the margins, else a folio seen (roman ones)
        folio, offs = {}, collections.Counter()
        for p in self.pages:
            if p["p"] in run:
                continue
            for b in p["blocks"]:
                for l in b["lines"] + b.get("cut_lines", []):
                    if not l.get("zone"):
                        continue
                    m = PAGE_NO.match(l["plain"]) or FOLIO.match(l["plain"])
                    n = m and next((g for g in m.groups() if g), None)
                    if not n:
                        continue
                    key = n if n.isdigit() else n.lower()
                    folio.setdefault(key, p["p"])
                    if n.isdigit():
                        offs[p["p"] - int(n)] += 1
        off = offs.most_common(1)[0] if offs else None
        for e in entries:
            n = e["num"]
            if n and n.isdigit() and off and off[1] >= T["repeat_pages"]:
                e["pdf"] = int(n) + off[0]          # the usual offset first: one odd margin number misleads
            else:
                e["pdf"] = folio.get(n) if n else None
        for pg in run:
            for b in self.pages[pg - 1]["blocks"]:
                if not b["sil"]:
                    b["toc"], b["sil"] = True, True
        for e in entries:
            e.pop("_last", None)
        self.toc = dict(pages=run, titled=titled, entries=entries)

    def _contents_headings(self):
        toc = self.toc
        if not toc:
            return
        for pg in toc["pages"]:
            for b in self.pages[pg - 1]["blocks"]:
                if b.get("toc"):
                    b["role"], b["flags"], b["conf"] = "içindekiler", [], None
        # the body's headings; a title set over several heading blocks on one page ("Walk 1" over "The Toaster",
        # a long title line by line) is tried as one, the closest such run counts
        heads = [(p, b) for p in self.pages if p["p"] not in toc["pages"] for b in p["blocks"] if b["role"] in HEAD]
        cands = []
        for k, (p, b) in enumerate(heads):
            run = [b]
            for q, c in heads[k + 1:k + 6]:
                if q is not p:
                    break
                run.append(c)
            cands.append([(run[:j], p["p"], k, toc_norm(" ".join(x["text"] for x in run[:j]))) for j in range(1, len(run) + 1)])
        last, used = -1, set()
        for e in toc["entries"]:
            t = toc_norm(e["title"])

            def best(runs):
                ok = [c for c in runs if toc_same(t, c[3])]
                return max(ok, key=lambda c: difflib.SequenceMatcher(None, t, c[3]).ratio()) if ok else None
            found = [best([c for c in runs if not any(id(b) in used for b in c[0])]) for runs in cands]
            near = [c for c in found if c and e["pdf"] and e["pdf"] - 1 <= c[1] <= e["pdf"] + 2]
            hit = near[0] if near else next((c for c in found if c and c[2] > last), None) or next(
                (c for c in found if c), None)          # listed out of order
            e["_match"], e["_cand"] = [], None
            if hit:
                e["_match"], e["role"] = hit[0], hit[0][0]["role"]
                used.update(id(b) for b in hit[0])
                for b in hit[0][1:]:
                    b["toc_join"] = True                # one entry, one heading: "CHAPTER 1" over its title
                last = max(last, hit[2])
        # an entry not set as a heading: a short block with its words near its page; with no page, between the pages
        # of the entries around it ("Part I" between the last chapter before and the first after)
        es = toc["entries"]
        page_of = {id(b): p["p"] for p in self.pages for b in p["blocks"]}
        for k, e in enumerate(es):
            if e["_match"]:
                continue
            if e["pdf"]:
                lo, hi = e["pdf"] - 1, e["pdf"] + 1
            else:
                before = [x["pdf"] or page_of[id(x["_match"][0])] for x in es[:k] if x["pdf"] or x["_match"]]
                after = [x["pdf"] or page_of[id(x["_match"][0])] for x in es[k + 1:] if x["pdf"] or x["_match"]]
                if not before or not after:
                    continue
                lo, hi = before[-1], after[0]
            t = toc_norm(e["title"])
            e["_cand"] = next((b for p in self.pages[max(0, lo - 1):hi] for b in p["blocks"]
                               if b["role"] not in HEAD and b["role"] not in ("sil", "içindekiler") and
                               b["nwords"] <= T["toc_entry_words"] and toc_same(t, toc_norm(b["text"]))), None)
        # the levels: parts over chapters, else what most entries of a level are
        lv = [e["level"] for e in es]
        firsts = [k for k, x in enumerate(lv) if x == 1]
        parts = (len(firsts) >= 2 and max(lv) >= 2 and 2 * len(firsts) <= lv.count(2)
                 and sum(k + 1 < len(lv) and lv[k + 1] == 2 for k in firsts) >= T["toc_level_share"] * len(firsts))
        want = {}
        for level in set(lv):
            roles = collections.Counter(e["role"] for e in es if e["_match"] and e["level"] == level)
            if roles:
                r, n = roles.most_common(1)[0]
                if n >= T["toc_level_share"] * sum(roles.values()) and n >= 2:
                    want[level] = r
        # the top level is the chapters (the parts when it holds them); a lower level keeps its majority, below those
        want = {lv: r for lv, r in want.items() if lv > 1 and HEAD[r] > HEAD["bölüm" if not parts or lv > 2 else "kısım"]}
        want.update({1: "kısım", 2: "bölüm"} if parts else {1: "bölüm"})
        toc["changed"] = []
        moved = {}                                  # heading block id -> markdown levels it moves (- up, + down)
        for e in es:
            r = want.get(e["level"])
            if e["_match"] and r and e["role"] != r:
                for b in e["_match"]:
                    moved[id(b)] = MD_LEVEL[HEAD[r]] - MD_LEVEL[HEAD[b["role"]]]
                    b["role"] = r
                    b["flags"] = sorted(set(b.get("flags") or ()) | {"icindekiler-seviye"})
                toc["changed"].append((e["role"], r))
        # the headings under a moved one, up to the next listed heading, move with it (the contents name the frame,
        # the sub-headings keep their place in it)
        listed = {id(b) for e in es for b in e["_match"]}
        shift = 0
        for p, b in heads:
            if id(b) in listed:
                shift = moved.get(id(b), 0)
            elif shift and b["role"] in ("alt başlık 1", "alt başlık 2"):
                md = min(3, max(2, MD_LEVEL[HEAD[b["role"]]] + shift))
                r = next(k for k in ("alt başlık 1", "alt başlık 2") if MD_LEVEL[HEAD[k]] == md)
                if r != b["role"]:
                    b["role"] = r
                    b["flags"] = sorted(set(b.get("flags") or ()) | {"icindekiler-seviye"})
        toc["parts"] = parts
        matched = {id(b) for e in es for b in e["_match"]}
        toc["unlisted"] = collections.Counter(b["role"] for p, b in heads if id(b) not in matched)

    def toc_ids(self):
        """the matched blocks by their ids, once the blocks have them (after the stages)"""
        for e in (self.toc or {}).get("entries", []):
            e["match"] = [b["bid"] for b in e.pop("_match", [])]
            c = e.pop("_cand", None)
            e["cand"] = c["bid"] if c else None


class _Front:
    """Stage 6f (Doc mixin): front matter. Its blocks (role "ön sayfa") stay out of the text only on a colophon page
    (©, an ISBN, or text mostly digits and punctuation: strong marks only, short lines alone are a poem as much as a
    list of credits) or a title page (a line in display type and no sentence: the book's title, its author, the
    imprint). Any other front text - a dedication, a note on the author, praise, an epigraph - is kept (flag
    "on-sayfa"; the review screen asks once per page whether it is translated): as a paragraph, its lines kept when
    they are all short (a dedication set as verse, an epigraph), a line opening with a dash as the attribution. A title page: a line in display type (twice the
    body), or the book's title as some front page sets it large (a half title repeats it), and no sentence. Marks,
    not words: no language's phrases."""

    def _front_kept(self):
        self.front_kept = []
        key = lambda t: re.sub(r"[\W\d_]+", "", t.lower())
        seen = collections.defaultdict(set)         # the book's title: set large on one front page, found on two
        for p in self.pages:
            for b in p["blocks"]:
                if b["role"] == "ön sayfa" and len(key(b["text"])) >= 4:
                    seen[key(b["text"])].add((p["p"], b["size"] >= T["title_page_size"] * self.bsize))
        titles = {k for k, v in seen.items() if len({pg for pg, _ in v}) >= 2 and any(big for _, big in v)}
        for p in self.pages:
            front = [b for b in p["blocks"] if b["role"] == "ön sayfa"]
            if not front or p["cover"]:
                continue
            text = " ".join(b["text"] for b in p["blocks"] if b["role"] != "sil")
            ink = re.sub(r"\s", "", text)
            marks = "©" in text or re.search(r"\bI\s?S\s?B\s?N\b", text, re.I) or (
                ink and sum(not c.isalpha() for c in ink) / len(ink) >= T["kunye_density"])
            sentence = any(re.search(r"[a-z]", b["text"]) and len(b["text"].split()) >= 3 and terminal(b["text"]) for b in front)
            display = any(b["size"] >= T["title_display"] * self.bsize or key(b["text"]) in titles for b in front)
            if marks or (display and not sentence):
                continue
            for b in front:
                verse = len(b["lines"]) >= 2 and all(len(l["plain"].split()) <= T["front_verse_words"] for l in b["lines"])
                b["role"] = "atıf" if DASH.match(b["text"]) else "epigraf" if verse else "gövde"
                b["breaks"], b["front"] = verse, True
                b["flags"] = sorted(set(b.get("flags") or ()) | {"on-sayfa"})
            self.front_kept.append(p["p"])


# ====================================================================================================================
# 7. MERGES
#   in:  every line of the book (lexicon); line texts of a block or two paragraph parts (joins)
#   out: Doc.lex / Doc.words; joined text (hyphens kept only when the book writes the compound)
# ====================================================================================================================


class _Merges:
    """Stage 7 (Doc mixin): the hyphen lexicon of the book."""

    def _hyphen_lexicon(self):
        """compounds the book writes with a hyphen mid-line; and how often each word occurs, for counting the
        line-end joins that have no evidence either way"""
        lex, words = set(), collections.Counter()
        cut_before = False                      # the previous line, in any block or page, ended in a split word
        for p in self.pages:
            for b in p["blocks"]:
                for l in b["lines"]:
                    t = l["plain"].rstrip()
                    for m in re.finditer(r"[A-Za-z]+(?:-[A-Za-z]+)+", t):
                        lex.add(m.group().lower())
                    toks = re.findall(r"[^\W\d_]+", t)
                    cut = t.endswith("\x02")
                    if cut and toks:
                        toks = toks[:-1]        # "ele" of "ele-/mentary": a piece, not a word
                    if cut_before and toks:
                        toks = toks[1:]         # "mentary"
                    words.update(x.lower() for x in toks)
                    cut_before = cut
        self.words = words
        return lex

    def hyphen_real(self, left, right):
        """A line-end hyphen before a lowercase word is real when the book writes that compound with a hyphen
        elsewhere ("half-" + "moon-shaped" counts "half-moon" too), or when both pieces are English words and the
        joined form is neither one nor a word of the book, an -s/-es/-ed/-ing form included ("self-" + "esteem";
        "counter-" + "transference" joins in a book that writes "countertransference", "over-" + "hearer" in one that
        writes "overhearers"); otherwise the word is joined ("some-" + "thing")."""
        a, b = left.lower(), right.split("-")[0].lower()
        if (left + "-" + right).lower() in self.lex or (a + "-" + b) in self.lex:
            return True
        E = english_words()
        j = a + b
        in_book = any(f in self.words for f in (j, j + "s", j + "es", j + "ed", j + "ing", j[:-1] if j.endswith("s") else j))
        return a in E and b in E and j not in E and not in_book

    def hyphen_ambiguous(self, left, right):
        """A join with no evidence in the book: the joined form never occurs, yet both pieces do as words of their own
        ("stress" + "free"). Only counted, for a later review list; the join itself does not change."""
        a, b = left.lower(), right.split("-")[0].lower()
        return (a + b) not in self.words and a in self.words and b in self.words


def join_hyphen(left, right, hy, stats, mark="\x02", amb=None):
    """left ends with a line-end hyphen (`mark`, possibly followed by emphasis markers): the joined text, or None.
    A real hyphen stays, a soft one goes. Emphasis that closes before the hyphen and reopens after it is one
    emphasised word ("*Geogra-*" + "*phy*" -> "*Geography*"), so both markers go."""
    m = re.search(r"([A-Za-z]+)" + re.escape(mark) + r"([*_]*)$", left)
    if not m:
        return None
    real_fn = hy if callable(hy) else (lambda a, b: (a + "-" + b).lower() in hy)
    r = re.match(r"([*_]*)([A-Za-z]+(?:-[A-Za-z]+)*)?", right)
    opener, rw = r.group(1), r.group(2) or ""
    first = right[len(opener):][:1]
    real = real_fn(m.group(1), rw) if (rw and first.islower()) else (first.isupper() or first.isdigit())
    stats["hyphen_real" if real else "hyphen_soft"] += 1
    if not real and amb and rw and first.islower() and amb(m.group(1), rw):
        stats["hyphen_ambiguous"] += 1
    closer = m.group(2)
    if closer and closer == opener:
        return left[:m.end(1)] + ("-" if real else "") + right[len(opener):]
    return left[:m.end(1)] + ("-" if real else "") + closer + right


def join_lines(texts, hy, stats, amb=None):
    """Line breaks inside a block. Hyphenation chars arrive as U+0002 (the PDF cannot say soft from real): join,
    keeping the hyphen only when it is a real one. `hy` is Doc.hyphen_real, or a set of hyphenated compounds."""
    out = texts[0]
    for nxt in texts[1:]:
        j = join_hyphen(out, nxt, hy, stats, amb=amb)
        out = j if j is not None else out + " " + nxt
    return out.replace("\x02", "-")


# a speaker's name opening a line of dialogue: "Charlie:", "Dr. Bruner:", "Susanna Kaysen:"
SPEAKER = re.compile(r"^[*_]*(?:(?:Dr|Mr|Mrs|Ms|Prof|Sr|St)\.\s+)?[A-Z][\w’'-]*(?:\s+[A-Z][\w’'-]*){0,2}[*_]*:[*_]*\s+(\S)")


def speaker(t, dialogue=False):
    """t opens with a speaker's name: followed by a quote, or anywhere in a dialogue (two such lines or more)"""
    m = SPEAKER.match(t)
    return bool(m) and (dialogue or m.group(1) in "“\"‘'")


def dialogue_lines(texts, hy, stats, amb=None):
    """an epigraph or quote set as prose, where a line opening with a speaker's name starts a line of its own"""
    two = sum(bool(SPEAKER.match(t)) for t in texts) >= 2
    runs = [[texts[0]]]
    for t in texts[1:]:
        if speaker(t, two):
            runs.append([t])
        else:
            runs[-1].append(t)
    return "\\\n".join(join_lines(r, hy, stats, amb) for r in runs)


# ====================================================================================================================
# THE DOCUMENT: stages 4-7 in order
#   in:  page dicts from build_blocks
#   out: the same pages, every block with its role; Doc keeps the book-level findings
# ====================================================================================================================


class Doc(_Margins, _Objects, _Styles, _Roles, _ShapeRoles, _Notes, _Contents, _Front, _Merges):
    """The book-level stages over every page, in this order. One step runs out of its section's order: running heads
    that repeat a heading (stage 4) need the heading roles of stage 6 first."""
    def __init__(self, pages):
        self.pages = pages
        self.NP = len(pages)
        widths = [p["W"] for p in pages]
        self.W0 = statistics.median(widths) if widths else 0
        # 4. margins: text block, running heads/feet, folios, slugs, watermarks; box and two-column checks
        self._mark_cover_and_repeats()
        self._filter_boxes()
        self._recheck_two_col()
        # 6e. the table of contents, found before the styles are counted (its entries are no headings)
        self._contents()
        # 6a. style statistics and the role each style suggests
        self.S = self._style_stats()
        self.sugg, self.why, self.body_style, self.body_styles = self._suggest()
        # 6b. block roles; then the margin lines that repeat a heading (4), the index (6b)
        self._block_roles()
        self._title_running_heads()
        self._index_pages()
        # 5. what belongs to figures and tables
        self._object_groups()
        # 6c. epigraph, block quote, verse, attribution, box from shape
        self._shape_roles()
        # 6d. page-bottom footnotes
        self._footnotes()
        # 6e. the contents' entries matched to the headings, heading levels set by them
        self._contents_headings()
        # 6f. front matter that is text (a dedication, a note on the author, praise) is kept
        self._front_kept()
        # 7. the hyphen lexicon for the joins written in stage 8
        self.lex = self._hyphen_lexicon()


# ====================================================================================================================
# 8. WRITING: markdown, warnings, seg_meta
#   in:  Doc and the requested page range
#   out: markdown in the app's conventions, warnings (Turkish), seg_meta (one entry per segment)
# ====================================================================================================================


CAPTION_OPEN, CAPTION_CLOSE = '::: {custom-style="Caption"}', ":::"


# named block roles -> the segment type segtypes derives from the markdown, and its Word style (Pandoc custom-style)
ROLE_TYPE = {"epigraf": "epigraph", "alıntı": "blockquote", "şiir": "verse", "atıf": "attribution", "kutu": "box",
             "kutu-başlık": "box"}


FENCE = {"epigraf": "Epigraph", "şiir": "Verse", "atıf": "Attribution", "kutu": "Box", "kutu-başlık": "Box"}


FENCE_OPEN = '::: {custom-style="%s"}'


def fenced(role, text):
    return (FENCE_OPEN + '\n%s\n:::') % (FENCE[role], text)


def verse_lines(texts):
    """lines kept as written: a Pandoc hard break (backslash at the line end) after every line but the last"""
    return "\\\n".join(t.replace("\x02", "-") for t in texts)


def poem_lines(ls, texts, hy, stats, amb=None):
    """a verse or a verse-like epigraph: a hard break where a line of the poem ends, not where prose merely wraps. A
    line ending in a split word joins the next (the hyphen rule decides the hyphen); a line that runs to the block's
    right edge joins a lowercase next line when the block is set like prose (two lines or more reach that edge), so
    the longest line of a real poem is never taken for a wrap."""
    if not texts:
        return ""
    edge = max(l["x1"] for l in ls)
    full = [abs(l["x1"] - edge) <= T["wrap_edge_em"] * l["size"] for l in ls]
    prose = sum(full) >= 2
    out = texts[0]
    for k in range(1, len(texts)):
        t = texts[k]
        j = join_hyphen(out, t, hy, stats, amb=amb) if out.rstrip().endswith("\x02") else None
        if j is not None:
            out = j
        elif prose and full[k - 1] and re.match(r"[*_“\"‘(]*[a-z]", t):
            out += " " + t
            stats["verse_wraps_joined"] += 1
        else:
            out += "\\\n" + t
    return out.replace("\x02", "-")


def _named_join(md, role, text, meta, b, p, lex, stats, amb):
    """an epigraph or a poem cut into two blocks (a drop cap's indent, a page turn): the block goes on in the segment
    before when it is the same role, that one does not close its sentence and this one starts in lowercase (or the
    word was split). An epigraph joins as prose, a poem keeps the break between its lines. True when joined."""
    last = md[-1] if md else None
    if not last or last[0] != "raw" or b.get("nojoin") or not last[1].startswith(FENCE_OPEN % FENCE[role]):
        return False
    if not ({p, p - 1} & last[2]["pages"]):
        return False
    inner = last[1][len(FENCE_OPEN % FENCE[role]) + 1:-len("\n:::")]
    tail = inner.split("\\\n")[-1]
    split_word = tail.rstrip().endswith("-")
    # a speech whose quote is still open goes on ("savants." / "There's ..."); a new speaker starts a segment
    speech = tail.count("“") > tail.count("”") and not speaker(text, True)
    if not split_word and not speech and (terminal(tail) or not re.match(r"[*_“\"‘(]*[a-z]", text)):
        return False
    if split_word:
        j = join_hyphen(inner, text, lex, stats, mark="-", amb=amb)
        inner = j if j is not None else inner + " " + text
    else:
        inner += ("\\\n" if role == "şiir" else " ") + text
    md[-1] = ("raw", fenced(role, inner), last[2])
    _meta_add(last[2], meta)
    stats["named_joins"] += 1
    return True


def _style_name(style):
    f, s = style.split("|")
    return f"{f} {s}"


def _meta_of(b=None, page=None, box=None):
    """where a segment comes from: pages, boxes [page, x0, y0, x1, y1] (PDF points, top-left origin), style, flags"""
    if b is not None:
        m = dict(pages={b["page_no"]}, boxes=[[b["page_no"]] + [round(v, 1) for v in (b["x0"], b["top"], b["x1"], b["bot"])]],
                 style=_style_name(b["style"]), flags=set(b.get("flags", ())),
                 role=ROLE_TYPE.get(b["role"]) if b.get("conf") else None, conf=b.get("conf"))
        m["_bids"] = [b["bid"]] if b.get("bid") else []      # private: which blocks a segment is made of
        if b.get("user_join"):
            m["_join"] = True                                # private: the user joined it to the segment before
        return m
    return dict(pages={page}, boxes=[[page] + [round(v, 1) for v in box]], style=None, flags=set(), role=None, conf=None)


def _meta_add(m, other):
    m.setdefault("_bids", []).extend(other.get("_bids", ()))
    m["pages"] |= other["pages"]
    m["boxes"] += other["boxes"]
    m["flags"] |= other["flags"]
    m["style"] = m["style"] or other["style"]
    m["role"], m["conf"] = m.get("role") or other.get("role"), m.get("conf") or other.get("conf")


# a notes section's name: Notes, Note, Endnotes, End Notes, Notes and Sources, Notes to Chapter 3, ...
NOTES_HEAD = re.compile(r"^[*_]*(?:end\s*)?notes?(?:\s+(?:and|&)\s+(?:sources|references|further reading))?"
                        r"(?:\s+(?:to|for|on)\s+(?:the\s+)?(?:chapter|ch\.|part)\s+[\w.]+)?[*_.:]*$", re.I)
# a group label inside a notes section, as a heading or a plain line: "Chapter 3", "3", "III", or a chapter's title
GROUP_NUMBER = re.compile(r"^[*_]*(?:chapter|ch\.|part)?\s*(\d{1,3}|[ivxlc]{1,7})[*_.:]*$", re.I)
# sections that end a notes section and are never notes themselves
NOT_NOTES = re.compile(r"\b(bibliography|references|works cited|sources|further reading|suggested readings?|"
                       r"recommended readings?|index|appendix|glossary|acknowledgm?ents?|about the authors?|permissions|"
                       r"credits)\b", re.I)
NOTE_ENTRY = re.compile(r"^[*_]*(\d{1,3})[.)]?[*_]*\s+(?=\S)")
# a number inside a note that is not the next entry starting: "Vol. 2", "pp. 3", "1, 2"
NOT_ENTRY_BEFORE = re.compile(r"(?:\b(?:chapter|ch|vol|volume|no|nos|p|pp|page|pages|part|fig|figure|table|section|issue|"
                              r"ed|edition)\.?|(?:^|\s)[\d,–-]+)$", re.I)
LOWER_START = re.compile(r"[*_“\"‘(]*[a-z]")


def _txt(it):
    return it[1]["text"] if it[0] in ("p", "head", "note") else it[1]


def _set_txt(md, i, t):
    if md[i][0] in ("p", "head", "note"):
        md[i][1]["text"] = t
    else:
        md[i] = (md[i][0], t, md[i][2])


def _size_of(m):
    return (m.get("style") or "").rsplit(" ", 1)[-1] or None


def _notes_sections(md):
    """Endnote sections: a "Notes" heading, then numbered entries, in groups under headings or plain lines (a
    chapter's title, as long as it is, or "Chapter N") or right under it, over as many pages as they run. An
    unnumbered paragraph goes on with the entry before it when it starts in lowercase or keeps the entry's size;
    entries set one after another in one block are split at the next number. The section ends at a heading with no
    entry under it, at a bibliography, index and the like, or at text that does not go on. A group counts when it starts at 1 and its numbers mostly
    follow each other (page-keyed notes, "2 Take John ...", do not). -> [dict(head, groups=[dict(head, entries)])]"""
    def entry_at(k):
        """the number of the first entry from k on (figures skipped), or None"""
        while k < len(md) and md[k][0] in ("p", "raw") and _txt(md[k]).startswith("!"):
            k += 1
        if k < len(md) and md[k][0] in ("p", "raw", "quote", "note"):
            e = NOTE_ENTRY.match(_txt(md[k]).strip())
            return int(e.group(1)) if e else None
        return None

    words = lambda t: set(re.findall(r"[^\W_]+", t.lower()))
    # the body's headings, and up to three in a row as one ("Part Two" over its title, kept apart as part and chapter)
    hs = [i for i, it in enumerate(md) if it[0] == "head"]
    heads = [set().union(*(words(_txt(md[k])) for k in range(i, j + 1)))
             for n, i in enumerate(hs) for j in hs[n:n + 3] if all(md[k][0] == "head" for k in range(i, j + 1))]

    def label(t, k):
        """a plain line naming the next group: short and no sentence, or a body heading's words (a chapter's long
        title), and the group's entry 1 follows"""
        w = words(t)
        return ((len(t.split()) <= T["notes_label_words"] and (GROUP_NUMBER.match(t) or not terminal(t)))
                or len(w) >= 2 and any(len(w & h) >= T["notes_label_head"] * len(w) for h in heads)) \
            and not NOTE_ENTRY.match(t) and entry_at(k + 1) == 1

    def split_after(j, n):
        """entries set on one after another end up in one block ("... Press. 4 In a letter ..."): the next entry,
        n + 1, starts an item of its own after j"""
        t = _txt(md[j])
        for m in re.finditer(r"(?<=\S) " + str(n + 1) + r"(?= (?:[^\s\da-z.,;:%)\]]|https?:|www\.))", t):
            if not NOT_ENTRY_BEFORE.search(t[:m.start()]):
                kind, v, meta = md[j]
                tail = t[m.start() + 1:]
                _set_txt(md, j, t[:m.start()].rstrip())
                md.insert(j + 1, (kind, dict(v, text=tail) if kind in ("p", "head", "note") else tail, copy.deepcopy(meta)))
                return

    out, i = [], 0
    while i < len(md):
        it = md[i]
        if not (it[0] in ("head", "p", "raw") and NOTES_HEAD.match(_txt(it).strip())):
            i += 1
            continue
        sec, j, grp = dict(head=i, groups=[]), i + 1, None
        while j < len(md):
            kind, v, m = md[j]
            t = _txt(md[j]).strip() if kind in ("p", "head", "note", "quote", "raw") else ""
            if kind != "head" and NOTES_HEAD.match(t):
                break
            if kind != "head" and t and label(t, j) and (grp is None or grp["entries"]):
                grp = dict(head=j, entries=[])
                sec["groups"].append(grp)
            elif kind == "head":
                nxt = next((k for k in range(j + 1, len(md)) if md[k][0] != "head"), None)
                if NOTES_HEAD.match(t) or NOT_NOTES.search(t) or nxt is None or not NOTE_ENTRY.match(_txt(md[nxt]).strip()):
                    break
                grp = dict(head=j, entries=[])
                sec["groups"].append(grp)
            elif NOTE_ENTRY.match(t):
                if grp is None:
                    grp = dict(head=None, entries=[])
                    sec["groups"].append(grp)
                grp["entries"].append(dict(n=int(NOTE_ENTRY.match(t).group(1)), i=j, cont=[]))
                split_after(j, grp["entries"][-1]["n"])
            elif t and grp and grp["entries"] and not t.startswith(("!", ":::", "|")) and (
                    LOWER_START.match(t) or _size_of(m) == _size_of(md[grp["entries"][-1]["i"]][2])):
                grp["entries"][-1]["cont"].append(j)
                split_after(j, grp["entries"][-1]["n"])
            elif t.startswith("!"):
                pass                                        # a figure or table placeholder inside the notes
            else:
                break
            j += 1
        # with no label between them, numbering that starts again at 1 starts a new group (a chapter's notes)
        split = []
        for g in sec["groups"]:
            split.append(dict(head=g["head"], entries=[]))
            for e in g["entries"]:
                if e["n"] == 1 and split[-1]["entries"]:
                    split.append(dict(head=None, entries=[]))
                split[-1]["entries"].append(e)
        sec["groups"] = split
        for g in sec["groups"]:
            # a number out of sequence that comes again later is a line of the note before ("17.541-50, / 18.158")
            es = g["entries"]
            for k in range(len(es) - 1, 0, -1):
                if es[k]["n"] != es[k - 1]["n"] + 1 and any(e["n"] == es[k]["n"] for e in es[k + 1:]):
                    es[k - 1]["cont"] += [es[k]["i"]] + es[k]["cont"]
                    del es[k]
            ns = [e["n"] for e in g["entries"]]
            g["ok"] = bool(ns) and ns[0] == 1 and sum(b == a + 1 for a, b in zip(ns, ns[1:])) >= T["notes_seq_share"] * (len(ns) - 1)
        if any(g["ok"] for g in sec["groups"]):
            out.append(sec)
        i = max(j, i + 1)
    return out


def _align(runs, groups):
    """callout runs (numbering restarts) to note groups, in order, as many shared numbers as possible -> {run: group}"""
    R, G = len(runs), len(groups)
    best = [[0] * (G + 1) for _ in range(R + 1)]
    for a in range(R - 1, -1, -1):
        for g in range(G - 1, -1, -1):
            best[a][g] = max(best[a + 1][g], best[a][g + 1],
                             len({n for n in runs[a]} & groups[g]) + best[a + 1][g + 1])
    pairs, a, g = {}, 0, 0
    while a < R and g < G:
        share = len({n for n in runs[a]} & groups[g])
        if share and best[a][g] == share + best[a + 1][g + 1]:
            pairs[a] = g
            a, g = a + 1, g + 1
        elif best[a][g] == best[a + 1][g]:
            a += 1
        else:
            g += 1
    return pairs


def resolve_notes(md, stats, drop_bids=frozenset()):
    """Callouts to their notes. Page-bottom footnotes match a callout on their page by number or sign. Endnotes:
    callouts split into runs where the numbering starts again, the runs aligned with the note groups in order, then
    matched by number. Every note gets a label unique in the book, [^b<group or chapter>-<n>]: the callout becomes
    [^label] in the text, the note its own segment "[^label]: text" (the importer's reorder puts it after the first
    paragraph citing it). A notes heading whose entries all found their callouts goes; one left with unmatched
    entries stays. Unmatched callouts are written as the number and flagged "atif-eslesmedi", unmatched notes stay
    as they were and are flagged "not-eslesmedi". Removed items are set to None."""
    sections = _notes_sections(md)
    in_notes = {k for s in sections for k in range(s["head"], max([s["head"]] + [e["i"] for g in s["groups"] for e in g["entries"]]
                                                                     + [c for g in s["groups"] for e in g["entries"] for c in e["cont"]]) + 1)}
    groups = [g for s in sections for g in s["groups"] if g["ok"]]
    calls, chapter, chap_of = [], 0, {}             # calls: [item, nth callout in it, value]
    for i, it in enumerate(md):
        if it[0] == "head" and it[1]["lvl"] <= 2:
            chapter += 1
        chap_of[i] = chapter
        if i in in_notes or it[0] == "note" or it[0] not in ("p", "head", "quote", "raw"):
            continue
        calls += [[i, k, v] for k, v in enumerate(REF.findall(_txt(it)))]
    used, label_of = set(), {}                      # label_of: (item, nth) -> label

    def unique(label, page):
        if label in used:
            label = f"{label}-{page}"
        while label in used:
            label += "x"
        used.add(label)
        return label
    # page-bottom footnotes
    feet = [i for i, it in enumerate(md) if it[0] == "note"]
    foot_label = {}
    for c in calls:
        pages = md[c[0]][2]["pages"]
        cand = [f for f in feet if md[f][1]["num"] == c[2] and md[f][1]["page"] in pages]
        f = next((f for f in cand if f not in foot_label), cand[0] if cand else None)
        if f is None:
            continue
        if f not in foot_label:
            foot_label[f] = unique(f"b{max(1, chap_of[f])}-{note_series(c[2])}", md[f][1]["page"])
        label_of[(c[0], c[1])] = foot_label[f]
    # endnotes
    runs, prev = [], None
    for c in calls:
        if (c[0], c[1]) in label_of or not c[2].isdigit():
            continue
        n = int(c[2])
        if prev is None or n <= prev:
            runs.append([])
        runs[-1].append(c)
        prev = n
    gsets = [{e["n"] for e in g["entries"]} for g in groups]
    entry_label = {}
    for a, g in _align([[int(c[2]) for c in r] for r in runs], gsets).items():
        for c in runs[a]:
            e = next((e for e in groups[g]["entries"] if e["n"] == int(c[2])), None)
            if e is None:
                continue
            if e["i"] not in entry_label:
                entry_label[e["i"]] = unique(f"b{g + 1}-{e['n']}", min(md[e["i"]][2]["pages"]))
            label_of[(c[0], c[1])] = entry_label[e["i"]]
    # notes the user marked, to a callout still without one: the one the user named, else the only one with the
    # note's mark (a footnote: on its page or the next one either side; an endnote: anywhere)
    for f in feet:
        v = md[f][1]
        if not v.get("user") or f in foot_label:
            continue
        free = [c for c in calls if (c[0], c[1]) not in label_of]
        if v.get("ref"):
            cand = [c for c in free if c[2] == v["ref"].get("mark") and v["ref"].get("bid") in md[c[0]][2].get("_bids", ())]
        elif v["num"]:
            cand = [c for c in free if c[2] == v["num"] and (v["user"] == "son not" or any(
                abs(pg - v["page"]) <= 1 for pg in md[c[0]][2]["pages"]))]
        else:
            cand = []
        if len(cand[:2]) == 1:
            c = cand[0]
            foot_label[f] = unique(f"b{max(1, chap_of[c[0]])}-{note_series(c[2])}", v["page"])
            label_of[(c[0], c[1])] = foot_label[f]
    # write: callouts
    ref_items = {c[0] for c in calls}
    for i, it in enumerate(md):
        if it is None or REF_OPEN not in _txt(it):
            continue
        k = iter(range(10 ** 6))

        def sub(m, i=i, k=k):
            label = label_of.get((i, next(k))) if i in ref_items else None
            if label:
                return f"[^{label}]"
            if i in ref_items and not NOTE_LETTERS.fullmatch(m.group(1)):    # a raised letter with no note is no callout
                md[i][2]["flags"].add("atif-eslesmedi")
                if drop_bids & set(md[i][2].get("_bids", ())):
                    return ""                               # the user: the stray number goes
            return m.group(1)
        _set_txt(md, i, re.sub(r"[ \t]*" + REF.pattern, sub, _txt(it)))
    # write: notes
    for f in feet:
        if f in foot_label:
            body = FOOT_START.sub("", md[f][1]["text"], count=1) if md[f][1]["num"] else md[f][1]["text"]
            body = re.sub(r"^" + re.escape(md[f][1]["num"]) + r"\s+", "", body)      # "21 21 It turns out": printed twice
            md[f] = ("raw", f"[^{foot_label[f]}]: " + body, md[f][2])
        else:
            md[f][2]["flags"].add("not-eslesmedi")
    removed = 0
    for s in sections:
        whole = True
        for g in s["groups"]:
            done = g["ok"] and all(e["i"] in entry_label for e in g["entries"])
            for e in g["entries"]:
                if e["i"] not in entry_label:
                    if g["ok"]:
                        md[e["i"]][2]["flags"].add("not-eslesmedi")
                    continue
                body = [NOTE_ENTRY.sub("", _txt(md[e["i"]]).strip(), count=1)] + [_txt(md[c]).strip() for c in e["cont"]]
                for c in e["cont"]:
                    _meta_add(md[e["i"]][2], md[c][2])
                    md[c] = None
                md[e["i"]] = ("raw", f"[^{entry_label[e['i']]}]: " + " ".join(body), md[e["i"]][2])
            if done and g["head"] is not None:
                md[g["head"]] = None
                removed += 1
            whole &= done
        if whole:
            md[s["head"]] = None
            removed += 1
    matched_feet = len(foot_label)
    stray = [c for c in calls if (c[0], c[1]) not in label_of and NOTE_LETTERS.fullmatch(c[2])]
    calls = [c for c in calls if c not in stray]
    stats["note_refs"] += len(calls)
    stats["note_bodies"] += len(feet) + sum(len(g["entries"]) for g in groups)
    stats["note_refs_matched"] += len(label_of)
    stats["note_bodies_matched"] += matched_feet + len(entry_label)
    stats["note_refs_unmatched"] += len(calls) - len(label_of)
    stats["note_bodies_unmatched"] += len(feet) - matched_feet + sum(len(g["entries"]) for g in groups) - len(entry_label)
    stats["notes_heads_removed"] += removed
    for s in sections:
        if not any(e["i"] in entry_label for g in s["groups"] for e in g["entries"]):
            stats.setdefault("notes_unmatched_pages", []).extend(sorted(md[s["head"]][2]["pages"])[:1])
    # for the review screen: how many callouts found their note, the ones left without (the blocks of their segment,
    # the mark), and the user's notes left without a callout
    return dict(refs=len(calls), matched=len([c for c in calls if (c[0], c[1]) in label_of]),
                orphans=[dict(bids=list(md[c[0]][2].get("_bids", ())), mark=c[2], pages=sorted(md[c[0]][2]["pages"]))
                         for c in calls if (c[0], c[1]) not in label_of],
                loose=[dict(bids=list(md[f][2].get("_bids", ())), mark=md[f][1]["num"], page=md[f][1]["page"], role=md[f][1]["user"])
                       for f in feet if f not in foot_label and md[f][1].get("user")])


PAGE_REF = re.compile(r"\b(?:pp?\.|pages?)\s?(\d{1,4})(?:\s?[–—-]\s?(\d{1,4}))?\b(?!\s?(?:%|percent))")
NUMBER_WORDS = "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty"
CHAPTER_REF = re.compile(r"(?:\b(?:see|cf\.)\s+(?:also\s+)?(?:the\s+)?|\(\s*)chapters?\s+(\d{1,2}|[IVXL]{1,5}\b|(?:" + NUMBER_WORDS + r")\b)", re.I)
AROUND_REF = re.compile(r"\b(?:see|discussed|described|mentioned|noted|shown|outlined|explained|cited|quoted|listed)\s+(?:\w+\s+)?(above|below)\b", re.I)
# what makes a page number a citation of another work: a year, et al., ed./trans./vol., ibid., a title in italics or
# quotes, "Name, p." right before
CITATION = re.compile(r"\b(1[5-9]|20)\d\d[a-z]?\b|\bet al\b|\b(?i:eds?|trans|vol|vols|ibid|op\. cit|cit)\.|\*\w|_\w|“|"
                      r"\b[A-Z][\w’'-]+,\s*$")
# a sentence ends at . ! ? before a capital, not after an initial or one of these abbreviations
SENTENCE_END = re.compile(r"(\w*)[.!?][”’\"')\]]*\s+(?=[*_“\"‘(]*[A-Z])")
NOT_NAMES = {"There", "Here", "This", "That", "These", "Those", "It", "See", "Above", "Below", "Earlier", "Later", "Again",
             "Chapter", "Part", "Section", "Page"}
ABBREV = {"p", "pp", "ed", "eds", "no", "cf", "ch", "vol", "vols", "esp", "rev", "cit", "ff", "ibid", "trans", "ms", "mr",
          "mrs", "dr", "st", "vs", "e.g", "i.e"}


def _citation(text, a, b):
    """text[a:b] is a page or chapter number: is it another work's? Read the clause it stands in: inside an open
    parenthesis, from it; else from the sentence start, at most 100 characters back; and on to the clause's end."""
    before = text[max(0, a - 100):a]
    if re.search(r"[,.]”\s*$|”,\s*$", before):
        return True                                 # '“Title,” p. 4'
    op = before.rfind("(")
    if op >= 0 and ")" not in before[op:]:
        # "(p. 42)" after a quotation, a quoted passage, a name ("Joly (p. 5)"), or in a sentence that quotes three
        # words or more or names a year: the page of the quoted work
        lead = before[:op]
        ends = [m.end() for m in SENTENCE_END.finditer(lead)
                if m.group(1).lower() not in ABBREV and not (len(m.group(1)) == 1 and m.group(1).isupper())]
        lead = lead[ends[-1]:] if ends else lead
        if not re.match(r"\s*(see|cf\.)", before[op + 1:], re.I) and (
                re.search(r"[”\"’.!?)*_]\s*$", lead)
                or re.search(r"\b[A-Z][\w’'-]+\s*$", lead) and lead.split()[-1] not in NOT_NAMES
                or re.search(r"“[^”]*\s[^”]*\s[^”]*”|\b(1[5-9]|20)\d\d[a-z]?\b", lead)):
            return True
        before = before[op + 1:]
    else:
        ends = [m.end() for m in SENTENCE_END.finditer(before)
                if m.group(1).lower() not in ABBREV and not (len(m.group(1)) == 1 and m.group(1).isupper())]
        before = before[ends[-1]:] if ends else before
    after = re.split(r"[).;!?]", text[b:b + 60], maxsplit=1)[0]
    return bool(CITATION.search(before) or CITATION.search(after))


def page_refs(md, stats):
    """References to a page of this book ("see p. 42", "pp. 12–14", "see chapter 3", "discussed above"), not
    citations of other works: the segment is flagged "sayfa-gonderme" with its targets in meta page_refs. Notes
    sections are read too, bibliography and index sections are not. The text is left alone."""
    skip_lvl = None
    for it in md:
        kind, v, m = it
        if kind == "head":
            if skip_lvl is not None and v["lvl"] <= skip_lvl:
                skip_lvl = None
            if NOT_NOTES.search(v["text"]) and not re.search(r"\bnotes?\b", v["text"], re.I):
                skip_lvl = v["lvl"]
            continue
        if skip_lvl is not None or kind not in ("p", "quote", "raw") or _txt(it).startswith(("!", "|")):
            continue
        text, found = _txt(it), []
        for rx, fmt in ((PAGE_REF, lambda g: "s." + g.group(1) + ("–" + g.group(2) if g.group(2) else "")),
                        (CHAPTER_REF, lambda g: "bölüm " + g.group(1)),
                        (AROUND_REF, lambda g: g.group(1).lower())):
            for g in rx.finditer(text):
                if not _citation(text, g.start(), g.end()):
                    found.append(fmt(g))
        if found:
            m["flags"].add("sayfa-gonderme")
            m["page_refs"] = list(dict.fromkeys(found))
            stats["page_refs"] += 1


FENCE_KIND = {"Epigraph": "epigraph", "Verse": "verse", "Box": "box", "Attribution": "attribution"}


def _units(kinds, meta):
    """seg_meta "unit": the paragraphs of one epigraph, quote, poem or box, each its own segment, are one unit -
    consecutive segments of one kind and one style; an attribution right after one closes it. Only units of two or
    more segments get an id ("u1", "u2", ...); the export writes a unit in one wrapper, the editor in one frame."""
    n, i = 0, 0
    while i < len(kinds):
        k, j = kinds[i], i
        if k in ("epigraph", "blockquote", "verse", "box"):
            while j + 1 < len(kinds) and kinds[j + 1] == k and meta[j + 1]["style"] == meta[i]["style"]:
                j += 1
            if j + 1 < len(kinds) and kinds[j + 1] == "attribution":
                j += 1
        if j > i:
            n += 1
            for x in range(i, j + 1):
                meta[x]["unit"] = f"u{n}"
        i = j + 1


def user_joins(md, lex, stats, amb):
    """segments the user joined to the one before ("öncekiyle birleştir"): whatever their kinds, the text goes on in
    the segment before, hyphen-joined where the line breaks a word"""
    for i in range(1, len(md)):
        if md[i] is None or not md[i][2].get("_join"):
            continue
        k = i - 1
        while k >= 0 and md[k] is None:
            k -= 1
        if k < 0:
            continue
        a, b = _txt(md[k]), _txt(md[i])
        j = join_hyphen(a, b, lex, stats, mark="-", amb=amb)
        _set_txt(md, k, j if j is not None else a + " " + b)
        _meta_add(md[k][2], md[i][2])
        md[i] = None


# roles the user can give a block (the review screen's and the editor's choices); "sil" leaves it out
USER_ROLES = ("gövde", "kısım", "bölüm", "alt başlık 1", "alt başlık 2", "epigraf", "alıntı", "şiir", "atıf", "altyazı", "kutu",
              "dipnot", "son not", "sil")


def apply_overrides(doc, decisions):
    """The user's decisions on a Doc, in order. Each names a block by its id ("page.index") or a page:
    {"bid", "role"} a role from USER_ROLES; a note's ("dipnot", "son not") may carry "ref": {"bid", "mark"}, the callout
    it belongs to, and {"bid", "note_ref": {"bid", "mark"}} gives it one later (a note is matched in resolve_notes);
    {"bid", "join": True} goes on in the segment before; {"bid", "split": k}
    a new segment from the block's line k (0: from the block itself, which then joins nothing before it); {"bid", "drop_refs": True} unmatched callout numbers go;
    {"bid", "line": k, "cut": bool, "keep": bool} a margin line kept or dropped (k in the lines the margin stage cut,
    or in the block's lines); {"page", "order": "rows"} a page read top to bottom, not as two columns; {"contents": "keep"}
    the table of contents written as text again.
    Every decision is noted in the block's flags ("kullanici:..."), so seg_meta tells who decided."""
    blocks = {b.get("bid"): (p, b) for p in doc.pages for b in p["blocks"]}
    doc.toc_keep = False

    def note(b, what):
        b["flags"] = sorted(set(b.get("flags") or ()) | {"kullanici:" + what})

    def detach(p, b):                               # no longer one of a figure's captions
        for it in p.get("items", []):
            for k in ("before", "after"):
                if k in it:
                    it[k] = [x for x in it[k] if x is not b]
        b.pop("obj", None)
    for dec in decisions:
        if "contents" in dec:                       # {"contents": "keep"}: the contents back in the text
            doc.toc_keep = dec["contents"] == "keep"
            continue
        if "page" in dec:
            p = doc.pages[int(dec["page"]) - 1]
            if dec.get("order") == "rows":
                p["blocks"].sort(key=lambda b: (round(b["top"]), b["x0"]))
                p["two_col"] = False
            continue
        p, b = blocks.get(dec.get("bid"), (None, None))
        if b is None:
            continue
        if "line" in dec:
            src = b.get("cut_lines", []) if dec.get("cut") else b["lines"]
            k = int(dec["line"])
            if not 0 <= k < len(src):
                continue
            if dec.get("keep") and dec.get("cut"):          # a cut margin line goes back into its block
                b["lines"] = sorted(b["lines"] + [src.pop(k)], key=lambda l: l["top"])
                _finish_block(b)
                note(b, "tut")
            elif not dec.get("keep") and not dec.get("cut"):
                if len(b["lines"]) == 1:
                    b["role"] = "sil"
                else:
                    b.setdefault("cut_lines", []).append(b["lines"].pop(k))
                    _finish_block(b)
                note(b, "at")
            continue
        if dec.get("note_ref"):
            b["note_ref"] = dec["note_ref"]
        if dec.get("role") in USER_ROLES:
            r = dec["role"]
            detach(p, b)
            b["role"], b["user"], b["sil"] = r, True, False
            b.pop("user_note", None)
            if r in ("dipnot", "son not"):                  # the block is a note: its first line's mark, if any
                m = FOOT_START.match(b["lines"][0]["plain"]) if b["lines"] else None
                b["role"], b["user_note"], b["foot"] = "dipnot", r, [(m.group(1) if m else "", b["lines"])]
                b["note_ref"] = dec.get("ref") or b.get("note_ref")
            if r in ROLE_TYPE:
                b["conf"] = 1.0
            if b.get("cut_lines") and r != "sil" and not b["lines"]:
                b["lines"] = b.pop("cut_lines")
                _finish_block(b)
            note(b, "sil" if r == "sil" else r)
        if dec.get("join"):
            b["user_join"] = True
            note(b, "birlestir")
        if dec.get("drop_refs"):
            b["drop_refs"] = True
            note(b, "atif-sil")
        if "split" in dec:
            k = int(dec["split"])
            if k == 0:                                      # at the block's start: a new segment, nothing joins it
                b["nojoin"], b["user_join"] = True, False
                note(b, "bol")
            elif 0 < k < len(b["lines"]):
                tail = dict(b, lines=b["lines"][k:], bid=b["bid"] + "+", nojoin=True, user_join=False)
                b["lines"] = b["lines"][:k]
                _finish_block(b)
                _finish_block(tail)
                p["blocks"].insert(p["blocks"].index(b) + 1, tail)
                blocks[tail["bid"]] = (p, tail)
                note(b, "bol")
                note(tail, "bol")


def build_markdown(doc, first_idx, last_idx):
    """pages[first_idx..last_idx] (0-based, inclusive) -> markdown, stats. The source of every segment (pages, boxes,
    style, flags) is left in doc.seg_meta, one entry per blank-line-separated segment of the markdown."""
    md, cur = [], None                  # md: (kind, value, meta)
    stats = collections.Counter()
    lex, amb = doc.hyphen_real, doc.hyphen_ambiguous
    pending = []                        # figures/tables met while a paragraph is still open: written after it
    feet = []                           # footnotes met while a paragraph is still open: written after it
    last_foot = None                    # the last footnote, which may go on overleaf
    index_done = False

    def captions(blocks):
        """one caption per run of blocks that reads on (no closing punctuation, lowercase start)"""
        runs = []
        for b in blocks:
            text = join_lines([l["md"].strip() for l in b["lines"]], lex, stats, amb)
            if runs and not terminal(runs[-1][0]) and re.match(r"[*_]*[a-z]", text):
                j = join_hyphen(runs[-1][0], text, doc.hyphen_real, stats, mark="-", amb=amb)
                runs[-1][0] = j if j is not None else runs[-1][0] + " " + text
                _meta_add(runs[-1][1], _meta_of(b))
            else:
                runs.append([text, _meta_of(b)])
        for text, m in runs:
            stats["captions"] += 1
            md.append(("raw", CAPTION_OPEN + "\n" + text + "\n" + CAPTION_CLOSE, m))

    def write_item(it):
        p = it["page"]
        captions(it.get("before", []))
        m = _meta_of(page=p, box=(it["x0"], it["top"], it["x1"], it["bot"]))
        if it["kind"] == "image":
            stats["images"] += 1
            md.append(("raw", f"![Görsel · s.{p}](gorsel-{p}-{it['n']})", m))
        elif it["complex"]:
            stats["tables_complex"] += 1
            stats["table_words_out"] += it["words"]
            doc.complex_table_pages.append(p)
            md.append(("raw", f"![Tablo · s.{p}](tablo-{p}-{it['n']})", m))
        else:
            stats["tables"] += 1
            md.append(("raw", it["md"], m))
        captions(it.get("after", []))

    def place(it):
        if cur:
            pending.append(it)
        else:
            write_item(it)

    def flush():
        nonlocal cur
        if cur:
            md.append(("p", cur, cur["meta"]))
            cur = None
        while pending:
            write_item(pending.pop(0))
        md.extend(feet)
        feet.clear()
    doc.complex_table_pages = []
    doc.object_merge_log = []
    for p in doc.pages:
        for b in p["blocks"]:
            b["page_no"] = p["p"]           # a caption above a figure is written before the loop reaches it
    for p in doc.pages[first_idx:last_idx + 1]:
        items = [] if p["p"] in doc.index_pages else sorted(p.get("items", []), key=lambda it: (round(it["top"]), it["x0"]))
        for b in p["blocks"]:
            while items and items[0]["top"] < b["top"] - T["item_order_pt"]:
                place(items.pop(0))
            role, ls = b["role"], b["lines"]
            # a block the user decided on is never dropped by an automatic rule below (stray letters, stray heading):
            # the decision is the last word; "sil" is the user's own drop
            auto_drop = not any(f.startswith("kullanici:") for f in b.get("flags") or ())
            if role == "altyazı" and not b.get("user"):
                continue                                    # written with its figure or table
            if role == "dipnot":                            # a paragraph running on overleaf stays open
                for num, nl in b["foot"]:
                    # a note of several paragraphs keeps them as hard breaks (a segment holds no blank line): a line
                    # after a closing one (not the note's first) that starts at another left edge; also when the
                    # new paragraph's indent made a block of its own
                    prev = (last_foot[1]["last"], last_foot[1]["first"]) if num is None and last_foot else (None, None)
                    text = ""
                    for l in nl:
                        a, first = prev
                        new = (a is not None and a is not first and a["page"] == l["page"] and terminal(a["plain"])
                               and abs(l["x0"] - a["x0"]) > T["indent_em"] * l["size"])
                        t = l["md"].strip()
                        if not text:
                            text = ("\\\n" if new else "") + t
                        elif new:
                            text += "\\\n" + t
                        else:
                            text = join_lines([text, t], lex, stats, amb)
                        prev = (l, first if first is not None else l)
                    if num is None and last_foot:
                        if text.startswith("\\\n"):
                            last_foot[1]["text"] += text
                        else:
                            j = join_hyphen(last_foot[1]["text"], text, doc.hyphen_real, stats, mark="-", amb=amb)
                            last_foot[1]["text"] = j if j is not None else last_foot[1]["text"] + " " + text
                        last_foot[1]["last"] = prev[0]
                        _meta_add(last_foot[2], _meta_of(b))
                    elif num is not None:
                        last_foot = ("note", dict(page=p["p"], num=num, text=text, last=prev[0], first=prev[1],
                                                  user=b.get("user_note"), ref=b.get("note_ref")), _meta_of(b))
                        (feet if cur else md).append(last_foot)
                continue
            if role == "içindekiler":
                # out of the text (it served to check the headings), unless the user brought it back: then as it
                # stood, its title a heading, each block its lines
                if not getattr(doc, "toc_keep", False):
                    stats["toc_words"] += b["nwords"]
                    stats["toc_blocks"] += 1
                    continue
                flush()
                lines = [l["md"].strip() for l in b["lines"] if l["md"].strip()]
                if len(lines) == 1 and TOC_TITLE.match(b["lines"][0]["plain"]):
                    md.append(("head", dict(lvl=2, page=p["p"], base=b["lines"][-1]["base"], size=b["size"], toc=True,
                                            text=b["lines"][0]["md_nobold"].strip()), _meta_of(b)))
                elif lines:
                    md.append(("raw", "\\\n".join(lines), _meta_of(b)))
                continue
            if role == "dizin":
                stats["index_words"] += b["nwords"]
                if not index_done:
                    flush()
                    pages = [n for n in doc.index_pages if first_idx < n <= last_idx + 1]
                    md.append(("raw", f"![Dizin · s.{pages[0]}–{pages[-1]}](dizin-{pages[0]}-{pages[-1]})",
                               dict(pages=set(pages), boxes=[], style=None, flags=set())))
                    index_done = True
                continue
            if role in ("sil", "ön sayfa"):
                stats["dropped_words"] += b["nwords"]
                stats["flag_removed"] += bool(b.get("flag_removed"))
                continue
            toks_ = [t.strip("*") for t in b["text"].split()]
            if auto_drop and role in ("gövde", "kutu") and (len(re.sub(r"[\s*]", "", b["text"])) <= T["stray_chars"]
                                              or (len(toks_) <= T["stray_tokens"] and all(len(t) == 1 for t in toks_))):
                stats["dropped_words"] += b["nwords"]       # single stray letters: ornate lettering, not text
                continue
            if "stil-farkli" in b.get("flags", ()):
                stats["style_flags"] += 1
            if b.get("conf") and role in ROLE_TYPE:
                stats["role_" + ROLE_TYPE[role]] += 1
            texts = [(l["md_nobold"] if role in HEAD else l["md"]).strip() for l in ls]
            box_here = b["box"] is not None
            if role in ("gövde", "kutu"):
                text = join_lines(texts, lex, stats, amb)
                last = md[-1] if md and cur is None else None
                # a quote cut by a page break whose rest did not keep the quote's shape (one line, no indent)
                if (last and last[0] == "quote" and not box_here and not terminal(last[1]) and not b.get("nojoin")
                        and re.match(r"[*_]*[a-z]", text)):
                    j = join_hyphen(last[1], text, doc.hyphen_real, stats, mark="-", amb=amb)
                    md[-1] = ("quote", j if j is not None else last[1] + " " + text, last[2])
                    _meta_add(last[2], _meta_of(b))
                    continue
                # a paragraph cut by a page break, or split into two blocks on the same page (around a drop cap, a
                # removed running head, a line set mostly in italics): same family and size (italic/bold variants
                # included), same box, no closing punctuation, lowercase start. With a figure or table in between
                # (pending), the continuation may start with a capital ("... Even among" / "CEOs, the top ...").
                # A heading demoted to the paragraph going on (cont) joins whatever its font.
                if (cur and not b.get("nojoin") and cur["page"] <= p["p"] and (fam_of(cur["style"]) == fam_of(b["style"]) or b.get("cont"))
                        and cur["box"] == box_here and not terminal(cur["text"])
                        and (re.match(r"[*_]*[a-z]", text) or pending or b.get("cont"))):
                    prev_text = cur["text"]
                    j = join_hyphen(cur["text"], text, doc.hyphen_real, stats, mark="-", amb=amb)
                    cur["text"] = j if j is not None else cur["text"] + " " + text
                    stats["page_merges" if cur["page"] < p["p"] else "inpage_merges"] += 1
                    if pending and not cur.get("joined_over_object"):
                        stats["object_merges"] += 1
                        cur["joined_over_object"] = True
                        doc.object_merge_log.append(dict(page=p["p"], before=prev_text, after=text))   # for review
                    cur["page"] = p["p"]
                    _meta_add(cur["meta"], _meta_of(b))
                    continue
                flush()
                cur = dict(page=p["p"], style=b["style"], text=text, box=box_here, meta=_meta_of(b))
                continue
            flush()
            if role in HEAD:
                t = join_lines([re.sub(r"(?<!\*)\*\*(?!\*)", "", x) for x in texts], lex, stats, amb)
                if auto_drop and len(re.sub(r"[\s*]", "", t)) <= T["stray_chars"]:
                    stats["dropped_words"] += b["nwords"]
                    continue
                lvl = HEAD[role]
                prev = md[-1][1] if md and md[-1][0] == "head" else None
                near = prev is not None and prev["page"] == p["p"] and (0 < b["lines"][0]["base"] - prev["base"] < T["head_wrap_em"] * max(b["size"], prev["size"])
                                                                         or b.get("toc_join"))
                if near and b.get("toc_join") and prev["lvl"] < lvl:    # one contents entry: one heading, its level
                    prev["text"] += " " + t
                    prev["base"] = b["lines"][-1]["base"]
                    _meta_add(md[-1][2], _meta_of(b))
                elif near and prev["lvl"] == lvl:                     # a heading that wrapped
                    prev["text"] += " " + t
                    prev["base"] = b["lines"][-1]["base"]
                    _meta_add(md[-1][2], _meta_of(b))
                elif near and prev["lvl"] > lvl:                      # "Part One" above "Preparing ...": one heading
                    prev.update(lvl=lvl, text=prev["text"] + " " + t, base=b["lines"][-1]["base"], size=max(prev["size"], b["size"]))
                    _meta_add(md[-1][2], _meta_of(b))
                else:
                    md.append(("head", dict(lvl=lvl, page=p["p"], base=b["lines"][-1]["base"], size=b["size"], text=t), _meta_of(b)))
            elif role == "kutu-başlık":
                md.append(("raw", fenced(role, "**" + re.sub(r"\*+", "", join_lines(texts, lex, stats, amb)) + "**"), _meta_of(b)))
            elif role == "şiir" or (role == "epigraf" and b.get("breaks")):
                # front text set as verse (a dedication) keeps every line as set
                t = verse_lines(texts) if b.get("front") else poem_lines(ls, texts, lex, stats, amb)
                if not _named_join(md, role, t, _meta_of(b), b, p["p"], lex, stats, amb):
                    md.append(("raw", fenced(role, t), _meta_of(b)))
            elif role == "epigraf":
                t = dialogue_lines(texts, lex, stats, amb)
                if not _named_join(md, role, t, _meta_of(b), b, p["p"], lex, stats, amb):
                    md.append(("raw", fenced(role, t), _meta_of(b)))
            elif role == "atıf":
                md.append(("raw", fenced(role, join_lines(texts, lex, stats, amb)), _meta_of(b)))
            elif role == "alıntı":
                text = dialogue_lines(texts, lex, stats, amb)
                last = md[-1] if md else None
                # a quote cut by a page break goes on in the next page's quote
                if last and last[0] == "quote" and not terminal(last[1]) and re.match(r"[*_]*[a-z]", text):
                    j = join_hyphen(last[1], text, doc.hyphen_real, stats, mark="-", amb=amb)
                    md[-1] = ("quote", j if j is not None else last[1] + " " + text, last[2])
                    _meta_add(last[2], _meta_of(b))
                else:
                    md.append(("quote", text, _meta_of(b)))
            elif role == "altyazı":                          # the user's: a caption standing on its own
                md.append(("raw", CAPTION_OPEN + "\n" + join_lines(texts, lex, stats, amb) + "\n" + CAPTION_CLOSE, _meta_of(b)))
            elif role == "liste":
                t = join_lines(texts, lex, stats, amb)
                # the number goes first, outside any emphasis: "**1. Lead-in.** text" -> "1. **Lead-in.** text"
                m = re.match(r"\s*(\**)(\d+\.)\s*(.*)", t, re.S)
                if m:
                    mk, num, rest = m.groups()
                    rest = rest[len(mk):].lstrip() if mk and rest.startswith(mk) else mk + rest
                    t = num + " " + rest
                md.append(("raw", t, _meta_of(b)))
            else:
                md.append(("raw", join_lines(texts, lex, stats, amb), _meta_of(b)))
        for it in items:
            place(it)
    flush()
    user_joins(md, lex, stats, amb)
    md = [it for it in md if it is not None]
    doc.notes = resolve_notes(md, stats, {b["bid"] for p in doc.pages for b in p["blocks"] if b.get("drop_refs")})
    md = [it for it in md if it is not None]
    page_refs(md, stats)
    out, meta, doc.seg_bids, kinds = [], [], [], []
    # a book whose contents show parts over chapters keeps the two apart: "# Part One {.kisim}", "# Chapter {.bolum}"
    parts = bool(getattr(doc, "toc", None) and doc.toc.get("parts"))
    attr = {1: " {.kisim}", 2: " {.bolum}"} if parts else {}
    for kind, v, m in md:
        text = (v["text"] if not v.get("box") else fenced("kutu", v["text"])) if kind == "p" else (
            "#" * MD_LEVEL[v["lvl"]] + " " + v["text"] + ("" if v.get("toc") else attr.get(v["lvl"], "")) if kind == "head" else "> " + v if kind == "quote" else
            v["text"] if kind == "note" else v)
        if not text.strip():
            continue
        out.append(text)
        meta.append(dict(pages=sorted(m["pages"]), boxes=m["boxes"], style=m["style"], flags=sorted(m["flags"]),
                         role=m.get("role"), conf=m.get("conf")))
        if m.get("page_refs"):
            meta[-1]["page_refs"] = m["page_refs"]
        doc.seg_bids.append(m.get("_bids", []))
        kinds.append("blockquote" if kind == "quote" else "box" if kind == "p" and v.get("box") else
                     FENCE_KIND.get(re.match(r'::: \{custom-style="([^"]+)"\}', text).group(1)) if kind == "raw" and text.startswith(":::")
                     else None)
    _units(kinds, meta)
    doc.seg_meta = meta
    return "\n\n".join(out) + "\n", stats


def _flag_suspects(md, meta, ocr_pages):
    """OCR segments: the words the word list does not know go to seg_meta "suspect" (flag "ocr-supheli"); a
    capitalised word met three times or more is taken for a name. Returns the number of suspect words."""
    segs = [x for x in re.split(r"\n\s*\n", md) if x.strip()]
    plain = lambda t: re.sub(r"\[\^[^\]\s]+\]:?|:::.*|[*_`>#|]", " ", t)
    caps = collections.Counter(w for t in segs for w in unknown_words(plain(t)) if w[:1].isupper())
    n = 0
    for t, m in zip(segs, meta):
        if not ocr_pages & set(m["pages"]):
            continue
        sus = [w for w in unknown_words(plain(t)) if not (w[:1].isupper() and caps[w] >= 3)]
        if sus:
            m["suspect"] = list(dict.fromkeys(sus))
            m["flags"] = sorted(set(m["flags"]) | {"ocr-supheli"})
            n += len(sus)
    return n


def _page_ranges(first_page, last_page, n):
    first = 1 if first_page in (None, "") else int(first_page)
    last = n if last_page in (None, "") else int(last_page)
    if first < 1 or last < first or first > n:
        raise PdfImportError(f"Sayfa aralığı geçersiz: {first}–{last} (PDF {n} sayfa).")
    return first, min(last, n)


def _fmt_pages(nums):
    """[3,4,5,9] -> '3–5, 9'"""
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}–{nums[j]}")
        i = j + 1
    return ", ".join(out)


def _analyse(pdf_bytes, first_page=None, last_page=None):
    """convert() with its internals: dict(md, warnings, doc, stats, tint, orn, info)"""
    return render(analyse(pdf_bytes, first_page, last_page))


def analyse(pdf_bytes, first_page=None, last_page=None):
    """The heavy half of an import: reading, lines, blocks and every book-level stage (Doc). -> state for render():
    dict(doc, first, last, ctx). The state pickles, so a review screen can render it again with the user's
    decisions without reading the PDF a second time."""
    try:
        doc = pdfium.PdfDocument(pdf_bytes)
    except pdfium.PdfiumError as e:
        msg = str(e)
        if "password" in msg.lower():
            raise PdfImportError("Bu PDF parola korumalı. Parolayı kaldırıp (ya da parolasız bir kopya kaydedip) tekrar dene.")
        raise PdfImportError(f"PDF açılamadı: {msg}")
    n = len(doc)
    if n == 0:
        raise PdfImportError("PDF'te hiç sayfa yok.")
    first, last = _page_ranges(first_page, last_page, n)
    # pass 1: characters of every page; ornament fonts (a handful of dingbat glyphs in the whole book) are known only now
    raw_pages = []
    font_count = collections.Counter()
    signals = []
    for i in range(n):
        page = doc[i]
        chars = _page_chars(page)
        gfx = _page_graphics(page)
        raw_pages.append((page.get_width(), page.get_height(), chars, _tint_boxes(page), gfx))
        signals.append(gfx["signals"])
    kinds = [page_kind(sg) for sg in signals]
    sel = range(first - 1, last)
    textless = [i + 1 for i in sel if kinds[i] == "scan"]
    if len(textless) > T["textless_reject"] * len(sel):
        raise PdfImportError(f"Bu PDF taranmış ve sayfalarının çoğunda ({len(textless)}/{len(sel)}) metin katmanı yok; "
                             "çevrilecek metin okunamıyor. Önce bir OCR programıyla metin katmanı ekle (ör. Adobe Acrobat "
                             "'Metni Tanı', ABBYY FineReader ya da ücretsiz OCRmyPDF), sonra yeniden içe aktar.")
    # OCR pages: an unreadable layer is read again with pdfminer, the cleaner reading of each page is kept; fonts and
    # their italic/bold say nothing in an OCR layer (often one invisible font), so size and position alone remain
    ocr = [i for i in range(n) if kinds[i] == "ocr"]
    unk = {i: unknown_share(raw_pages[i][2]) for i in ocr}
    retry = {i for i in ocr if unk[i][0] > T["ocr_retry"] * max(1, unk[i][1])}
    miner = _miner_chars(pdf_bytes, retry) if retry else {}
    no_miner = miner is None
    miner = miner or {}
    for i, chars in list(miner.items()):
        u = unknown_share(chars)
        if u[0] / max(1, u[1]) < unk[i][0] / max(1, unk[i][1]):
            raw_pages[i] = raw_pages[i][:2] + (chars,) + raw_pages[i][3:]
            unk[i] = u
        else:
            del miner[i]
    for i in ocr:
        for c in raw_pages[i][2]:
            c.font, c.italic, c.bold, c.weight = "OCR", False, False, 400
    sel_ocr = [i for i in ocr if first - 1 <= i < last]
    bad, words = sum(unk[i][0] for i in sel_ocr), sum(unk[i][1] for i in sel_ocr)
    if words and bad > T["ocr_reject"] * words:
        raise PdfImportError(f"Bu PDF taranmış ve OCR metni çok bozuk: sözcüklerin %{round(100 * bad / words)} kadarı "
                             f"sözlükte yok (sınır %{round(100 * T['ocr_reject'])}). Metin katmanını daha iyi bir OCR ile "
                             "yeniden oluştur (ör. Adobe Acrobat 'Metni Tanı', ABBYY FineReader ya da ücretsiz OCRmyPDF), "
                             "sonra yeniden içe aktar." + (MINER_MISSING if no_miner else ""))
    for rp in raw_pages:
        for c in rp[2]:
            if c.ch.strip():
                font_count[c.font] += 1
    total_chars = sum(len(rp[2]) for rp in raw_pages[first - 1:last])
    if total_chars < T["min_chars_page"] * (last - first + 1) and total_chars < T["min_chars_total"]:
        raise PdfImportError("Bu PDF'te (seçilen sayfalarda) metin katmanı yok ya da çok az. Taranmış bir PDF olabilir; "
                             "önce OCR ile metin katmanı eklenmeli.")
    orn = frozenset(f for f, c in font_count.items() if c < T["ornament_glyphs"] and ORNAMENT_FONT.search(f))
    tint = {i + 1: rp[3] for i, rp in enumerate(raw_pages)}
    pages = []
    for i, (W, H, chars, boxes, gfx) in enumerate(raw_pages):
        pages.append(build_blocks(i + 1, W, H, chars, boxes, orn, gfx["images"], gfx["hl"], gfx["vl"]))
    raw_pages = None
    d = Doc(pages)
    info = doc.get_metadata_dict()
    producer = " ".join(v for v in (info.get("Producer"), info.get("Creator")) if v)
    outline = []                                    # the PDF's own bookmarks, for headings the contents do not list
    try:
        for x in doc.get_toc():
            dest = x.get_dest()
            outline.append(dict(title=x.get_title() or "", level=x.level,
                                page=dest.get_index() + 1 if dest is not None and dest.get_index() is not None else None))
    except Exception:
        outline = []                                # a broken outline is no outline
    for pg in d.pages:
        for k, b in enumerate(pg["blocks"]):
            b["bid"] = f"{pg['p']}.{k}"            # stable through renders: the review screen's decisions name blocks
    d.toc_ids()
    return dict(doc=d, first=first, last=last,
                ctx=dict(signals=signals, ocr=ocr, sel_ocr=sel_ocr, miner=sorted(miner), retry=sorted(retry),
                         no_miner=no_miner, textless=textless, producer=producer, tint=tint, orn=orn, outline=outline))


def render(state, overrides=None):
    """The light half: the user's decisions (apply_overrides; none = the importer's own) on the Doc, then markdown,
    warnings and seg_meta, aligned one entry per segment. dict(md, warnings, doc, stats, tint, orn, meta, info)"""
    d, first, last, c = state["doc"], state["first"], state["last"], state["ctx"]
    if overrides:
        apply_overrides(d, overrides)
    signals, ocr, sel_ocr, retry, no_miner, textless, producer, tint, orn = (
        c["signals"], c["ocr"], c["sel_ocr"], set(c["retry"]), c["no_miner"], c["textless"], c["producer"], c["tint"], c["orn"])
    miner = c["miner"]
    md, stats = build_markdown(d, first - 1, last - 1)
    if len(md.split()) < T["min_md_words"]:
        raise PdfImportError("Seçilen sayfalardan okunabilir metin çıkmadı (yalnızca filigran/üst-alt bilgi olabilir). "
                             "Sayfa aralığını kontrol et.")
    scanned = is_scanned(producer, signals)
    rotated = sum(p.get("rotated", 0) for p in d.pages[first - 1:last])
    warnings = [SCANNED_WARNING] if scanned else []
    if sel_ocr:
        suspects = _flag_suspects(md, d.seg_meta, {i + 1 for i in sel_ocr})
        warnings.append(f"Taranmış: italik/kalın bilgisi yok, {suspects} şüpheli sözcük.")
        if no_miner:
            warnings.append("OCR katmanı bozuk okunan " + str(len([i for i in retry if first - 1 <= i < last]))
                            + " sayfa yeniden okunamadı." + MINER_MISSING)
        used = sorted(i + 1 for i in miner if first - 1 <= i < last)
        if used:
            warnings.append(f"OCR katmanı {len(used)} sayfada yedek okuyucuyla (pdfminer) okundu: " + _fmt_pages(used) + ".")
    if textless and ocr:
        warnings.append("Metin katmanı olmayan taranmış sayfalar: " + _fmt_pages(textless) + " (çevrilmeyecek; gerekirse "
                        "bu sayfalara OCR uygulanmalı).")
    two = [p["p"] for p in d.pages[first - 1:last] if p["two_col"] and p["p"] not in d.index_pages]
    if two:
        warnings.append("İki sütunlu görünen sayfalar: " + _fmt_pages(two) + " (önce sol, sonra sağ sütun yazıldı; metni kontrol et).")
    if stats["page_merges"]:
        warnings.append(f"{stats['page_merges']} paragraf sayfa geçişinde birleştirildi.")
    if stats["hyphen_real"]:
        warnings.append(f"{stats['hyphen_real']} satır sonu tiresi gerçek tire sayıldı (kitapta tireli geçiyor ya da iki parçası sözcük, birleşik hali değil); "
                        f"{stats['hyphen_soft']} tanesi hece bölme sayılıp birleştirildi.")
    if stats["hyphen_ambiguous"]:
        warnings.append(f"{stats['hyphen_ambiguous']} belirsiz tire birleştirildi.")
    if stats["images"]:
        fig_words = sum(p.get("fig_words", 0) for p in d.pages[first - 1:last])
        warnings.append(f"{stats['images']} görsel yer tutucuyla işaretlendi"
                        + (f"; görsellerin içindeki {fig_words} sözcük metne alınmadı." if fig_words else "."))
    if stats["captions"]:
        warnings.append(f"Görsel ve tablolara {stats['captions']} başlık/altyazı/açıklama satırı bağlandı (altyazı olarak yazıldı).")
    if stats["object_merges"]:
        warnings.append(f"{stats['object_merges']} paragraf araya giren görsel/tablo yüzünden bölünmüştü; birleştirildi, "
                        "görsel/tablo paragrafın ardına alındı.")
    if stats["tables"]:
        warnings.append(f"{stats['tables']} tablo markdown tablosu olarak yazıldı; çeviride satır ve sütunlar korunmalı.")
    if stats["tables_complex"]:
        warnings.append(f"{stats['tables_complex']} karmaşık (birleşik hücreli) tablo görsel gibi işaretlendi, çevrilmeyecek: s. "
                        + _fmt_pages(sorted(set(d.complex_table_pages))) + ".")
    idx = [n for n in d.index_pages if first <= n <= last]
    if idx:
        warnings.append(f"Dizin (s. {idx[0]}–{idx[-1]}) çeviriye dahil edilmedi.")
    running = sum(p.get("running_removed", 0) for p in d.pages[first - 1:last])
    if running:
        warnings.append(f"{running} üst/alt bilgi satırı (sayfa üstü/altı başlık, künye, filigran) metinden çıkarıldı.")
    if stats["flag_removed"]:
        warnings.append(f"Kenarda duran ya da yalnızca numara olan {stats['flag_removed']} farklı stilli blok çıkarıldı.")
    if stats["style_flags"]:
        warnings.append(f"{stats['style_flags']} blok gövdeden farklı stilde ama hiçbir başlığa ya da görsel/tabloya "
                        "bağlanamadı; \"stil-farkli\" olarak gözden geçirmeye işaretlendi.")
    if stats["note_refs"] or stats["note_bodies"]:
        w = (f"Notlar: {stats['note_refs']} atıf, {stats['note_bodies']} not gövdesi; {stats['note_refs_matched']} atıf "
             f"{stats['note_bodies_matched']} nota bağlandı, notlar ilk atıf yapan paragrafın ardına taşındı.")
        if stats["note_refs_unmatched"] or stats["note_bodies_unmatched"]:
            w += (f" Eşleşmeyen: {stats['note_refs_unmatched']} atıf (\"atif-eslesmedi\"), "
                  f"{stats['note_bodies_unmatched']} not gövdesi (\"not-eslesmedi\").")
        if stats["notes_heads_removed"]:
            w += f" Boşalan {stats['notes_heads_removed']} not başlığı kaldırıldı."
        warnings.append(w)
    if stats.get("notes_unmatched_pages"):
        warnings.append("Not bölümü bulundu (s. " + _fmt_pages(sorted(set(stats["notes_unmatched_pages"]))) + ") ama notları "
                        "metindeki atıflara bağlanamadı; olduğu gibi bırakıldı, kontrol et.")
    if stats["page_refs"]:
        warnings.append(f"{stats['page_refs']} paragrafta kitap içi sayfa göndermesi var (\"sayfa-gonderme\"): Türkçe "
                        "baskıda sayfa numaraları değişecek.")
    if rotated >= T["rotated_warn"]:
        warnings.append(f"Eğik ya da dikey yazılmış {rotated} karakter (filigran, kenar yazısı) metne alınmadı.")
    skipped = [p["p"] for p in d.pages[first - 1:last] if p["cover"]]
    if skipped:
        warnings.append("Kapak/açık yayılım sayfası atlandı: " + _fmt_pages(skipped) + ".")
    return dict(md=md, warnings=warnings, doc=d, stats=stats, tint=tint, orn=orn, meta=d.seg_meta,
                info=dict(scanned=scanned, producer=producer, rotated_chars=rotated,
                          scan_pages=sum(1 for s in signals if s[0] and s[1] and s[1] >= T["scan_invisible_ratio"] * s[2])))


def convert(pdf_bytes, first_page=None, last_page=None):
    a = _analyse(pdf_bytes, first_page, last_page)
    return a["md"], a["warnings"]


def convert_with_meta(pdf_bytes, first_page=None, last_page=None):
    """convert() plus the source of every segment (see build_markdown): (markdown, warnings, seg_meta)"""
    a = _analyse(pdf_bytes, first_page, last_page)
    return a["md"], a["warnings"], a["meta"]
