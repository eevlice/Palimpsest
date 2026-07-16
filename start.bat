@echo off
REM Palimpsest launcher (Windows). Double-click this file to start the app.
cd /d "%~dp0"
echo Starting Palimpsest...
python -c "import flask, anthropic, docx" 2>NUL
if errorlevel 1 (
  echo First run: installing the three required libraries...
  pip install flask anthropic python-docx
)
python server.py
pause
