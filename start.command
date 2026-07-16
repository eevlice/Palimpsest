#!/bin/bash
# Palimpsest launcher (Mac). Double-click this file to start the app.
cd "$(dirname "$0")"
echo "Starting Palimpsest..."
# Install libraries if missing (first run only), quietly.
python3 -c "import flask, anthropic, docx" 2>/dev/null || {
  echo "First run: installing the three required libraries..."
  pip3 install --quiet flask anthropic python-docx
}
python3 server.py
