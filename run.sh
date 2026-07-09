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

echo "[INFO] Starting Aether..."
python main.py
if [ $? -ne 0 ]; then
    echo "[ERROR] Application exited with error"
    exit 1
fi