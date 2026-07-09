@echo off
REM Aether Runner for Windows
REM Activates the virtual environment and launches the application

echo.
echo =============================================
echo   Aether - Local AI Assistant
echo =============================================
echo.

REM Check if virtual environment exists
if not exist venv (
    echo [ERROR] Virtual environment not found. Please run install.bat first.
    pause
    exit /b 1
)

REM Activate virtual environment and run the application
echo [INFO] Activating virtual environment...
call venv\Scripts\activate
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment
    pause
    exit /b 1
)

echo [INFO] Starting Aether...
python main.py
if errorlevel 1 (
    echo [ERROR] Application exited with error
    pause
    exit /b 1
)