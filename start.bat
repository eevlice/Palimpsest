@echo off
REM Palimpsest launcher (Windows). Double-click this file to start the app.
cd /d "%~dp0"
echo Starting Palimpsest...
python -c "import flask, anthropic, openai, google.genai, docx, cryptography" 2>NUL
if errorlevel 1 (
  echo First run: installing the required libraries...
  pip install flask anthropic openai google-genai python-docx cryptography
)
python server.py
pause
