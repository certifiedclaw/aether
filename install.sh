#!/bin/bash
# Aether Installer for Linux/macOS
# Creates a virtual environment and installs dependencies

echo ""
echo "============================================"
echo "   Aether - Local AI Assistant Installer"
echo "============================================"
echo ""

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python 3 not found. Please install Python 3.11 or 3.12 from https://python.org"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1-2)
if [[ "$PYTHON_VERSION" != "3.11" && "$PYTHON_VERSION" != "3.12" ]]; then
    echo "[ERROR] Python 3.11 or 3.12 required. Detected: $(python3 --version)"
    exit 1
fi

echo "[INFO] Python version check passed: $(python3 --version)"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "[INFO] Creating virtual environment..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to create virtual environment"
        exit 1
    fi
else
    echo "[INFO] Virtual environment already exists"
fi

# Activate virtual environment
echo "[INFO] Activating virtual environment..."
source venv/bin/activate
if [ $? -ne 0 ] || [ -z "$VIRTUAL_ENV" ]; then
    echo "[ERROR] Failed to activate virtual environment"
    exit 1
fi

# Upgrade pip/setuptools/wheel via "python -m pip", not bare "pip" -- on some
# platforms pip cannot reliably overwrite its own running executable when
# invoked directly. Non-fatal: an older pip can still install everything
# below fine.
echo "[INFO] Upgrading pip, setuptools, and wheel..."
python3 -m pip install --prefer-binary --upgrade pip setuptools wheel
if [ $? -ne 0 ]; then
    echo "[WARN] Could not upgrade pip/setuptools/wheel -- continuing with the existing version."
fi

if [ ! -f requirements.txt ]; then
    echo "[ERROR] requirements.txt not found in $(pwd)."
    echo "        Run this script from inside the Aether project folder."
    exit 1
fi

echo "[INFO] Installing dependencies from requirements.txt..."
python3 -m pip install --prefer-binary -r requirements.txt
if [ $? -ne 0 ]; then
    echo "[WARN] First attempt failed -- retrying once..."
    python3 -m pip install --prefer-binary -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to install dependencies."
        echo "        Scroll up past the retry to find the FIRST error pip printed --"
        echo "        pip repeats it, and the real cause is usually higher up than"
        echo "        the last line (e.g. a specific package failing to build, or"
        echo "        one needing a system library)."
        exit 1
    fi
fi

echo ""
echo "============================================"
echo "   Installation completed successfully!"
echo "============================================"
echo ""
echo "Aether can run two ways:"
echo "  1. Normal   - the real app: window, mic, TTS. Use this for actual"
echo "                use and for testing real user experience."
echo "  2. Headless - no window, no mic, no audio. Scripted text in, text"
echo "                out -- for fast automated testing of the"
echo "                conversation/tool/memory logic only. This does NOT"
echo "                test real voice, audio, or the GUI -- it is not a"
echo "                substitute for a normal run."
echo ""
read -p "Set run.sh's default mode [1 or 2]: " _mode_choice
if [ "$_mode_choice" = "2" ]; then
    echo "headless" > aether_mode.txt
else
    echo "normal" > aether_mode.txt
fi
echo ""
echo "Saved to aether_mode.txt -- re-run install.sh anytime to change it,"
echo "or edit that file directly (\"normal\" or \"headless\")."
echo "For scripted headless testing, see tests/live_test_script.txt."
echo ""
echo "To run Aether, execute: ./run.sh"
echo "============================================"
