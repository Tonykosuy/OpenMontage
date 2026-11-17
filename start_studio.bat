@echo off
cd /d "%~dp0"
set PATH=%~dp0.venv\Scripts;%PATH%
echo ===================================================
echo   OpenMontage Studio - Interactive Local AI Studio
echo   Studio UI: http://127.0.0.1:4760/studio
echo   Backlot:   http://127.0.0.1:4760/
echo ===================================================
start "" http://127.0.0.1:4760/studio
"%~dp0.venv\Scripts\python.exe" -m uvicorn studio.server:app --host 127.0.0.1 --port 4760
