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

# Activate virtual environment and install dependencies
echo "[INFO] Activating virtual environment and installing dependencies..."
source venv/bin/activate
if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to activate virtual environment"
    exit 1
fi

pip install --upgrade pip
if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to upgrade pip"
    exit 1
fi

pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to install dependencies"
    exit 1
fi

echo ""
echo "============================================"
echo "   Installation completed successfully!"
echo ""
echo "To run Aether, execute: ./run.sh"
echo "============================================"