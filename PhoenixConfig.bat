@echo off
rem Starts PhoenixConfig. Needs Python 3.11+ and: pip install -r requirements.txt
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python mk2_panel.py %*
) else (
    py -3 mk2_panel.py %*
)
if errorlevel 1 pause
