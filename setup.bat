@echo off
echo ============================================
echo   CollegeFind - Windows Setup
echo ============================================
echo.

REM ── Check Python ──────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found!
    echo.
    echo Please install Python from: https://www.python.org/downloads/
    echo IMPORTANT: During install, check "Add Python to PATH"
    echo.
    pause
    exit /b 1
)
echo [1/4] Python found:
python --version
echo.

REM ── Create virtual environment ─────────────────
echo [2/4] Creating virtual environment...
python -m venv venv
if errorlevel 1 (
    echo ERROR: Failed to create virtual environment.
    pause
    exit /b 1
)
echo Done.
echo.

REM ── Install packages ───────────────────────────
echo [3/4] Installing packages (may take 2-3 minutes)...
call venv\Scripts\activate.bat
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo ERROR: Package installation failed.
    pause
    exit /b 1
)
echo Done.
echo.

REM ── Create .env ────────────────────────────────
if not exist .env (
    echo [4/4] Creating .env file...
    (
        echo NEON_DATABASE_URL=postgresql://neondb_owner:npg_UiljQAJvw7B5@ep-green-butterfly-aoa4v3oo-pooler.c-2.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
        echo SESSION_SECRET=collegefind-local-secret-2024
    ) > .env
    echo Done.
) else (
    echo [4/4] .env already exists, skipping.
)

echo.
echo ============================================
echo   Setup complete!
echo   Now double-click run.bat to start the app.
echo ============================================
echo.
pause
