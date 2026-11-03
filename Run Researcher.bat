@echo off
REM Double-click launcher for the researcher window.
REM pythonw.exe runs the GUI with no console window behind it.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" "gui.py"
