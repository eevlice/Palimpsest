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
# Palimpsest keeps its libraries in its own environment, .venv in this folder (gitignored, so updates never touch
# it): no installing into the system Python, which Homebrew's Python refuses anyway (PEP 668). It is made once, with
# the newest Python 3.10+ found; libraries installed into the system Python by older versions are left alone.
if [ ! -x .venv/bin/python ]; then
  PY=""
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      PY="$c"; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "Palimpsest needs Python 3.10 or newer: https://www.python.org/downloads/"
    read -r -p "Press Enter to close."
    exit 1
  fi
  echo "Setting up Palimpsest's own Python environment (.venv) with $("$PY" --version), once."
  "$PY" -m venv .venv || { echo "Could not create .venv."; read -r -p "Press Enter to close."; exit 1; }
fi
# Libraries: installed again whenever requirements.txt changes (its SHA-256 is kept in .venv after a good install).
WANT=$(.venv/bin/python -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' requirements.txt)
# If installing fails: a .venv that was fully installed once opens as it was (warned); a new one does not open.
if [ "$WANT" != "$(cat .venv/requirements.sha256 2>/dev/null)" ]; then
  echo "Installing the required libraries..."
  if .venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt; then
    echo "$WANT" > .venv/requirements.sha256
  elif [ -f .venv/requirements.sha256 ]; then
    echo "Yeni kütüphaneler kurulamadı; Palimpsest eski kurulumla açılıyor, bazı özellikler çalışmayabilir. Bir sonraki açılışta yeniden denenecek. / New libraries could not be installed; opening with the previous setup."
  else
    echo "Kütüphaneler kurulamadı, internet bağlantını kontrol edip yeniden aç. / Could not install the libraries; check your internet connection and open again."
    read -r -p "Kapatmak için Enter'a bas. / Press Enter to close."
    exit 1
  fi
fi
exec .venv/bin/python server.py
