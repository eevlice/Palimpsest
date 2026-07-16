#!/bin/bash
# Palimpsest launcher (Mac). Double-click this file to start the app.
cd "$(dirname "$0")"
echo "Starting Palimpsest..."
# Install libraries if missing (first run only), quietly.
python3 -c "import flask, anthropic, openai, google.genai, docx, cryptography" 2>/dev/null || {
  echo "First run: installing the required libraries..."
  pip3 install --quiet flask anthropic openai google-genai python-docx cryptography
}
python3 server.py
