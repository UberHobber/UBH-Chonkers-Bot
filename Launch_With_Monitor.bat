@echo off
setlocal

rem ---------------------------------------------------------------------------
rem Starts the bot and Monitor.py side by side, each in its own console window.
rem   - Each window runs through Launch.bat, so each one stays open after its
rem     script exits or crashes (with the exit code in the title) until a key
rem     is pressed. Either can fail or be closed without affecting the other.
rem   - Monitor.py shows every status_*.json, so it also picks up a standalone
rem     Subtitles.py run started separately.
rem
rem Usage: double-click, or "Launch_With_Monitor.bat [script.py] [args...]"
rem        (bot script defaults to Main.py, e.g. "Launch_With_Monitor.bat Subtitles.py")
rem ---------------------------------------------------------------------------

cd /d "%~dp0"

start "Chonkers Bot - Monitor" "%~dp0Launch.bat" Monitor.py
start "Chonkers Bot" "%~dp0Launch.bat" %*

endlocal
