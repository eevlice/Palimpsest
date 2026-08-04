#!/bin/bash
# Palimpsest launcher (Mac). Double-click this file to start the app.
cd "$(dirname "$0")"
echo "Starting Palimpsest..."
# If installed via git, pull updates automatically. --ff-only means this is a
# no-op (not a merge) the moment someone edits a tracked file locally, and
# GIT_TERMINAL_PROMPT=0 makes a missing/expired credential fail instantly
# instead of hanging the launcher on a login prompt. Either way, projects/
# and key.txt are gitignored so this never touches your books or your key.
if [ -d .git ]; then
  echo "Checking for updates..."
  GIT_TERMINAL_PROMPT=0 git pull --ff-only --quiet 2>/dev/null || true
fi
# Install libraries if missing (first run only), quietly.
python3 -c "import flask, anthropic, openai, google.genai, docx, cryptography" 2>/dev/null || {
  echo "First run: installing the required libraries..."
  pip3 install --quiet flask anthropic openai google-genai python-docx cryptography
}
python3 server.py
