#!/bin/bash
# Aether Runner for Linux/macOS
# Activates the virtual environment and launches the application

echo ""
echo "============================================"
echo "   Aether - Local AI Assistant"
echo "============================================"
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "[ERROR] Virtual environment not found. Please run ./install.sh first."
    echo ""
    exit 1
fi

# Activate virtual environment and run the application
echo "[INFO] Activating virtual environment..."
source venv/bin/activate
if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to activate virtual environment"
    exit 1
fi

# Mode is chosen during install.sh and stored in aether_mode.txt ("normal"
# or "headless"). Defaults to normal -- the real GUI app -- if that file
# is missing, e.g. a venv set up before this option existed.
AETHER_MODE="normal"
if [ -f aether_mode.txt ]; then
    AETHER_MODE=$(cat aether_mode.txt | tr -d '[:space:]')
fi

if [ "$AETHER_MODE" = "headless" ]; then
    export AETHER_HEADLESS=1
    echo "[INFO] Starting Aether in HEADLESS mode -- no window, no mic, no TTS."
    echo "       This is for scripted/automated testing of the conversation,"
    echo "       tool, and memory logic only. It does NOT test real voice"
    echo "       recognition, audio quality, or the GUI itself -- for that,"
    echo "       run in normal mode instead."
    echo ""
    echo "       Interact via the web dashboard once it's up, or feed it a"
    echo "       scripted test file instead of running this normally:"
    echo "         AETHER_HEADLESS_STDIN=1 ./run.sh < tests/live_test_script.txt"
    echo ""
    echo "       To switch back to the real app, re-run install.sh and choose"
    echo "       normal mode, or edit aether_mode.txt to say \"normal\"."
    echo ""
else
    echo "[INFO] Starting Aether..."
    echo ""
fi

python3 main.py
AETHER_EXIT=$?

echo ""
if [ $AETHER_EXIT -ne 0 ]; then
    echo "[ERROR] Aether exited with code $AETHER_EXIT -- see any messages above."
else
    echo "Aether has closed."
fi
# Keep the terminal open if this was double-clicked from a file manager
# rather than run from an already-open shell.
read -p "Press Enter to close this window..." _
exit $AETHER_EXIT