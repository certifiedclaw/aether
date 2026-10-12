@echo off
REM Aether Runner for Windows

cd /d "%~dp0"

echo.
echo =============================================
echo   Aether - Local AI Assistant
echo =============================================
echo.

if exist venv\Scripts\python.exe goto :venv_ok
echo [ERROR] Virtual environment not found. Please run install.bat first.
pause
exit /b 1

:venv_ok
echo [INFO] Activating virtual environment...
call "venv\Scripts\activate.bat"
if defined VIRTUAL_ENV goto :activated
echo [ERROR] Failed to activate virtual environment
pause
exit /b 1

:activated
REM Mode is chosen during install.bat and stored in aether_mode.txt
REM ("normal" or "headless"). Defaults to normal -- the real GUI app --
REM if that file is missing, e.g. a venv set up before this option
REM existed. Re-run install.bat any time to change it, or edit
REM aether_mode.txt directly.
set AETHER_MODE=normal
if exist aether_mode.txt set /p AETHER_MODE=<aether_mode.txt

if /i "%AETHER_MODE%"=="headless" goto :run_headless
goto :run_normal

:run_headless
set AETHER_HEADLESS=1
echo [INFO] Starting Aether in HEADLESS mode -- no window, no mic, no TTS.
echo        This is for scripted/automated testing of the conversation,
echo        tool, and memory logic only. It does NOT test real voice
echo        recognition, audio quality, or the GUI itself -- for that,
echo        run in normal mode instead.
echo.
echo        Interact via the web dashboard once it's up, or feed it a
echo        scripted test file instead of running this normally:
echo          set AETHER_HEADLESS_STDIN=1
echo          run.bat ^< tests\live_test_script.txt
echo.
echo        To switch back to the real app, re-run install.bat and choose
echo        option 1, or edit aether_mode.txt to say "normal".
echo.
goto :launch

:run_normal
echo [INFO] Starting Aether...
echo.
goto :launch

:launch
python main.py
set AETHER_EXIT=%ERRORLEVEL%

echo.
if not "%AETHER_EXIT%"=="0" echo [ERROR] Aether exited with code %AETHER_EXIT% - see any messages above.
if "%AETHER_EXIT%"=="0" echo Aether has closed.
REM Always pause here, success or failure, so the window never vanishes
REM before you can read the last thing it printed.
pause
exit /b %AETHER_EXIT%
