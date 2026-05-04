#!/usr/bin/env bash
# setup.sh – One-time setup for Mac/Linux
set -e

echo "=== USDA Web Soil Survey Automation Setup ==="

# 1. Check Python 3.9+
python3 -c "import sys; assert sys.version_info >= (3,9), 'Python 3.9+ required'" \
  || { echo "ERROR: Python 3.9 or newer is required."; exit 1; }
echo "✓  Python OK ($(python3 --version))"

# 2. Install Python dependencies
echo "Installing Python dependencies…"
pip3 install -r requirements.txt

# 3. Install Chromium for Playwright
echo "Installing Chromium browser for Playwright…"
playwright install chromium

echo ""
echo "=== Setup complete. Run the automation with: ==="
echo "  python3 wss_automation.py 1063Test"
echo ""
echo "Options:"
echo "  python3 wss_automation.py <input_dir> [output_dir]"
echo "  python3 wss_automation.py 1063Test output --headless"
