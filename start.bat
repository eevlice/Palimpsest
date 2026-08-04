@echo off
REM Palimpsest launcher (Windows). Double-click this file to start the app.
cd /d "%~dp0"
echo Starting Palimpsest...
REM If installed via git, pull updates automatically. --ff-only means this is
REM a no-op (not a merge) the moment a tracked file has local edits, and
REM GIT_TERMINAL_PROMPT=0 makes a missing/expired credential fail instantly
REM instead of hanging the launcher on a login prompt. projects\ and key.txt
REM are gitignored, so this never touches your books or your key.
if exist .git (
  echo Checking for updates...
  set GIT_TERMINAL_PROMPT=0
  git pull --ff-only --quiet >NUL 2>&1
)
python -c "import flask, anthropic, openai, google.genai, docx, cryptography" 2>NUL
if errorlevel 1 (
  echo First run: installing the required libraries...
  pip install -r requirements.txt
)
python server.py
pause
