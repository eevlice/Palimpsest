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
REM Palimpsest keeps its libraries in its own environment, .venv in this folder (gitignored, so updates never
REM touch it), made once with Python 3.10+; libraries installed into the system Python by older versions are left
REM alone.
if not exist ".venv\Scripts\python.exe" (
  set "PY="
  py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >NUL 2>&1 && set "PY=py -3"
  if not defined PY python -c "import sys; sys.exit(sys.version_info < (3, 10))" >NUL 2>&1 && set "PY=python"
)
if not exist ".venv\Scripts\python.exe" if not defined PY (
  echo Palimpsest needs Python 3.10 or newer: https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Setting up Palimpsest's own Python environment ^(.venv^), once.
  %PY% -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo Could not create .venv.
  pause
  exit /b 1
)
REM Libraries: installed again whenever requirements.txt changes (its SHA-256 is kept in .venv after a good install).
set "WANT="
set "HAVE="
for /f "usebackq delims=" %%h in (`".venv\Scripts\python.exe" -c "import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest())" requirements.txt`) do set "WANT=%%h"
if exist ".venv\requirements.sha256" set /p HAVE=<".venv\requirements.sha256"
if not "%WANT%"=="%HAVE%" (
  echo Installing the required libraries...
  ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt && (>".venv\requirements.sha256" echo %WANT%)
)
".venv\Scripts\python.exe" server.py
pause
