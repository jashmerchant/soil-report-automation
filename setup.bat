@echo off
REM setup.bat – One-time setup for Windows
echo === USDA Web Soil Survey Automation Setup ===

REM 1. Check Python
python --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo ERROR: Python not found. Install Python 3.9+ from https://python.org
    exit /b 1
)
echo Python OK

REM 2. Install Python dependencies
echo Installing Python dependencies...
pip install -r requirements.txt
IF ERRORLEVEL 1 ( echo ERROR: pip install failed & exit /b 1 )

REM 3. Install Chromium for Playwright
echo Installing Chromium browser for Playwright...
playwright install chromium
IF ERRORLEVEL 1 ( echo ERROR: playwright install failed & exit /b 1 )

echo.
echo === Setup complete. Run the automation with: ===
echo   python wss_automation.py 1063Test
echo.
echo Options:
echo   python wss_automation.py ^<input_dir^> [output_dir]
echo   python wss_automation.py 1063Test output --headless
