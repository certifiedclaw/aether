@echo off
REM Aether Installer for Windows
REM Creates a virtual environment and installs dependencies

echo.
echo =============================================
echo   Aether - Local AI Assistant Installer
echo =============================================
echo.

REM Check Python version
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH. Please install Python 3.11 or 3.12 from https://python.org
    pause
    exit /b 1
)

python --version | find " 3.11" >nul
if not errorlevel 1 goto :version_ok
python --version | find " 3.12" >nul
if not errorlevel 1 goto :version_ok

echo [ERROR] Python 3.11 or 3.12 required. Detected:
python --version
pause
exit /b 1

:version_ok
echo [INFO] Python version check passed

REM Create virtual environment if it doesn't exist
if not exist venv (
    echo [INFO] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment
        pause
        exit /b 1
    )
) else (
    echo [INFO] Virtual environment already exists
)

REM Activate virtual environment and install dependencies
echo [INFO] Activating virtual environment and installing dependencies...
call venv\Scripts\activate
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment
    pause
    exit /b 1
)

pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] Failed to upgrade pip
    pause
    exit /b 1
)

pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies
    pause
    exit /b 1
)

echo.
echo =============================================
echo   Installation completed successfully!
echo.
echo To run Aether, double-click run.bat
echo =============================================
pause