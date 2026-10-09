@echo off
setlocal

rem ---------------------------------------------------------------------------
rem Launcher for the chat downloader.
rem   - Runs from this file's folder so relative paths / sys.path work.
rem   - Uses the project's .venv interpreter.
rem   - Output goes straight to the console (no piping) so TQDM bars render.
rem   - Window stays open after the script exits or crashes.
rem
rem Usage: double-click, or "Launch.bat [script.py] [args...]"
rem        (defaults to Main.py, e.g. "Launch.bat Subtitles.py")
rem ---------------------------------------------------------------------------

cd /d "%~dp0"

set "SCRIPT=%~1"
if "%SCRIPT%"=="" set "SCRIPT=Main.py"

set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [Launcher] Could not find %PYTHON% - is the virtual environment set up?
    goto :end
)

rem UTF-8 console + Python I/O so chat messages/emoji print without crashing.
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
rem Unbuffered so the last lines before a crash are actually on screen.
set "PYTHONUNBUFFERED=1"

title Chonkers Bot - %SCRIPT%
echo [Launcher] Started %SCRIPT% at %DATE% %TIME%
echo.

if "%~1"=="" (
    "%PYTHON%" "%SCRIPT%"
) else (
    "%PYTHON%" %*
)
set "EXITCODE=%ERRORLEVEL%"

echo.
if "%EXITCODE%"=="0" (
    echo [Launcher] %SCRIPT% finished normally at %DATE% %TIME%
) else (
    echo [Launcher] %SCRIPT% EXITED WITH ERROR CODE %EXITCODE% at %DATE% %TIME%
    title Chonkers Bot - %SCRIPT% - CRASHED ^(code %EXITCODE%^)
)

:end
echo.
echo Press any key to close this window . . .
pause >nul
endlocal
