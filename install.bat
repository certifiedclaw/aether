@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0"

echo.
echo =============================================
echo   Aether - Local AI Assistant Installer
echo =============================================
echo.

REM Step 1: find Python
where python >nul 2>&1
if errorlevel 1 goto :no_python

REM Ask Python directly for its version as two plain numbers, instead of
REM parsing "python --version" text. This avoids a common Windows trap
REM where "python" resolves to the Microsoft Store stub alias rather
REM than a real install -- in that case --version prints an unrelated
REM message and text-parsing silently breaks. Asking Python to print
REM sys.version_info directly either gives clean numbers or fails
REM outright, which is easy to detect.
set PY_MAJOR=
set PY_MINOR=
for /f "tokens=1,2" %%a in ('python -c "import sys;print(sys.version_info[0],sys.version_info[1])" 2^>nul') do set PY_MAJOR=%%a& set PY_MINOR=%%b

if not defined PY_MAJOR goto :bad_python
if not "%PY_MAJOR%"=="3" goto :bad_major

set PY_VERSION=%PY_MAJOR%.%PY_MINOR%

if %PY_MINOR% LSS 10 goto :bad_minor
if %PY_MINOR% GTR 12 echo [WARN] Python %PY_VERSION% is newer than this project has been tested with - 3.11 or 3.12 is recommended. Continuing anyway.

echo [INFO] Python version OK: %PY_VERSION%

REM Step 2: requirements.txt must exist
if not exist requirements.txt goto :no_requirements

REM Step 3: create the virtual environment if it isn't there already
if exist venv\Scripts\python.exe goto :venv_ready
if not exist venv goto :make_venv
echo [WARN] A "venv" folder exists but looks incomplete - removing it and starting fresh.
rmdir /s /q venv

:make_venv
echo [INFO] Creating virtual environment...
python -m venv venv
if errorlevel 1 goto :venv_create_failed

:venv_ready
echo [INFO] Virtual environment ready.

REM Step 4: activate it
echo [INFO] Activating virtual environment...
call "venv\Scripts\activate.bat"
if not defined VIRTUAL_ENV goto :activate_failed

REM Step 5: upgrade pip/setuptools/wheel. Must be run as "python -m pip",
REM never bare "pip" -- pip.exe cannot overwrite its own running
REM executable on Windows, which is the exact error this project hit
REM before. Non-fatal: an old pip can still install everything below.
echo [INFO] Upgrading pip, setuptools, and wheel...
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 echo [WARN] Could not upgrade pip/setuptools/wheel - continuing anyway.

REM Step 6: install dependencies, with one automatic retry
echo [INFO] Installing dependencies from requirements.txt...
python -m pip install -r requirements.txt
if not errorlevel 1 goto :success

echo [WARN] First attempt failed - retrying once...
python -m pip install -r requirements.txt
if errorlevel 1 goto :deps_failed

:success
echo.
echo =============================================
echo   Installation completed successfully!
echo =============================================
echo.
echo   Aether can run two ways:
echo     1. Normal  - the real app: window, mic, TTS. Use this for actual
echo                  use and for testing real user experience.
echo     2. Headless - no window, no mic, no audio. Scripted text in,
echo                  text out -- for fast automated testing of the
echo                  conversation/tool/memory logic only. This does NOT
echo                  test real voice, audio, or the GUI -- it is not a
echo                  substitute for a normal run.
echo.
choice /c 12 /n /m "  Set run.bat's default mode [1 or 2]: "
if errorlevel 2 goto :mode_headless
echo normal> aether_mode.txt
goto :mode_done

:mode_headless
echo headless> aether_mode.txt

:mode_done
echo.
echo   Saved to aether_mode.txt -- re-run install.bat anytime to change it,
echo   or edit that file directly ^("normal" or "headless"^).
echo   For scripted headless testing, see tests\live_test_script.txt.
echo.
echo   To run Aether, double-click run.bat
echo =============================================
pause
exit /b 0

:no_python
echo [ERROR] Python not found in PATH.
echo         Install Python 3.11 or 3.12 from https://python.org
echo         and make sure "Add python.exe to PATH" is checked during setup.
goto :fail

:bad_python
echo [ERROR] "python" was found on PATH but did not behave like a real Python interpreter.
echo         On Windows this usually means it resolved to the Microsoft Store stub
echo         instead of a real install. Fix: open Windows Settings, go to
echo         "Manage App Execution Aliases", and turn OFF the python.exe / python3.exe
echo         entries. Then install Python 3.11 or 3.12 from https://python.org
echo         and re-run this script.
goto :fail

:bad_major
echo [ERROR] Python 3.x required. Detected major version: %PY_MAJOR%
goto :fail

:bad_minor
echo [ERROR] Python 3.10+ required. Detected: %PY_VERSION%
goto :fail

:no_requirements
echo [ERROR] requirements.txt not found in this folder.
echo         Run this script from inside the Aether project folder.
goto :fail

:venv_create_failed
echo [ERROR] Failed to create virtual environment.
goto :fail

:activate_failed
echo [ERROR] Virtual environment did not activate correctly.
echo         Try deleting the "venv" folder and running this script again.
goto :fail

:deps_failed
echo [ERROR] Failed to install dependencies.
echo         Scroll up past the retry to find the FIRST error pip printed -
echo         pip repeats it, and the real cause is usually higher up than
echo         the last line, e.g. a specific package failing to build.
goto :fail

:fail
echo.
echo Installation did not complete. See the [ERROR] above for the cause.
pause
exit /b 1
