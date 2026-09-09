@echo off
REM RTL-SDR V4 Diagnostic & Spectrum Monitor - Windows launcher
setlocal
cd /d "%~dp0"

REM Use a local virtual environment if one exists, otherwise the system Python.
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=py -3.12"
    where py >nul 2>nul || set "PY=python"
)

REM If rtlsdr.dll was dropped into .\dll, make it findable.
if exist "dll\rtlsdr.dll" set "RTLSDR_DLL_DIR=%CD%\dll"
if exist "dll" set "PATH=%CD%\dll;%PATH%"

echo Starting RTL-SDR V4 Diagnostic ^& Spectrum Monitor...
%PY% main.py %*
if errorlevel 1 (
    echo.
    echo The application exited with an error.
    echo If modules are missing, run:  pip install -r requirements.txt
    echo To test the interface without hardware, run:  run.bat --simulate
    echo.
    pause
)
endlocal
