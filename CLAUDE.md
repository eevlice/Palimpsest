# Palimpsest

Local, single-user book translation tool. Flask backend + one vanilla-JS file frontend. The user writes in Turkish; reply to them in Turkish. Code, comments and commit messages are in English.

## Layout
- server.py: Flask routes (port 5001 is hardcoded), segmentation, setup pass, /translate_simple
- segtypes.py: segment type (heading/footnote/paragraph) derived from the source markdown, marker/ref stripping for the model, footnote ⟦n⟧ placeholders, footnote reorder at import. index.html's segType() mirrors it; tests/ checks both against one fixture.
- pdf_import.py: PDF → markdown in stages, all tuned numbers in THRESHOLDS; stages, roles, thresholds, known limits and how to run the karne: docs/pdf_import.md
- providers.py: Anthropic/OpenAI/Google calls, model catalog + prices, cost_level (1-5, log scale across ALL models)
- keys.py (Fernet-encrypted keys), prefs.py, spending.py (JSONL log): all store under ~/.palimpsest/
- index.html: entire UI (CSS + JS inline). Served from disk on every request, so UI edits need no restart.
- Python changes DO need a server restart.
- start.command / start.bat: launchers (git pull --ff-only on launch if .git exists)
- scripts/release-homebrew.sh: tag + formula bump. Needs an SSH clone of the tap at ~/homebrew-palimpsest. Versions: semver, MINOR for new features, PATCH for fixes. Homebrew users stay pinned to the last tag.
- scripts/check_book_text.py + scripts/hooks/pre-commit: stops a commit whose files share 6 words in a row with a test book (tuning and holdout books under ~/palimpsest-spike/karne*, read only locally; warns and passes without them). Enable once per clone: `git config core.hooksPath scripts/hooks`. By hand: `python3 scripts/check_book_text.py FILE...`.
  Exceptions only by exact path in scripts/book_text_allowlist.txt, one per line with its reason (no globs); only for text that must stay verbatim (a licence).

## Data (protect it)
- Books: ~/.palimpsest/projects/*.json (legacy ./projects/ is migrated once at startup). These are the user's real work.
- Testlerde ve fixture'larda yalnızca sentetik metin; kitap metni asla. (Synthetic text only in tests and fixtures; never book text. The pre-commit check enforces it.)
- NEVER read-modify-write real projects or call paid APIs while testing without asking. For isolated tests run a copy of the app with HOME pointed at a temp dir and a different port.
- Project JSON has no schema validation. Frontend normalizeProject() backfills missing fields, so old files must keep loading.

## Gotchas learned the hard way
- Model refs are "provider::model_id". Models without thinking_style:"adaptive" use the old budget_tokens path, which 400s on Opus 5/4.8/4.7 and is deprecated on Sonnet 4.6. Tag every new Claude model in providers.py.
- Anthropic cache is 1h TTL; cache_write prices in providers.py are 2x input to match. Keep them in sync if TTL changes.
- Segmentation splits on blank lines only. Text without them becomes one giant segment (known limitation, user decided not to change it).
- Glossary terms (P.terms, "src = tgt" lines) are matched on the SOURCE side only, word-boundary gated (short terms like "IP" must not match "skipped"). parseTerms() strips wrapping backticks/quotes the model adds.
- Textareas cannot hold inline highlights; that is why the translation side uses a tag icon instead of word marks.
- CSS alignment: glossary rows use flex:1 1 0 for src/tgt plus a fixed-width .glist-actions zone. The add row and entry rows must share the same padding/gap or dividers drift.

## Working agreements
- Verify every UI change in a real browser (screenshot + console errors) before calling it done.
- Ask before: commit, push, restarting the user's running server, spending API money, touching real projects.
- Commit messages explain WHY. Never commit .claude/.
- UI taste: quiet and distraction-proof. Paper/serif look, monospace uppercase labels, small line icons, no filled loud highlights, no extra buttons when position can say it. Prefer removing text over adding it.
- Do not add features, abstractions or config that were not asked for.
- Do not add Co-Authored-By trailers to commit messages.
