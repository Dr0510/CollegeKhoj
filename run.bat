@echo off
echo ============================================
echo   CollegeFind - Starting Server
echo ============================================
echo.

if not exist venv (
    echo ERROR: Virtual environment not found!
    echo Please run setup.bat first.
    pause
    exit /b 1
)

if not exist .env (
    echo ERROR: .env file not found!
    echo Please run setup.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

echo Server starting at: http://localhost:8080
echo Open your browser and go to that address.
echo Press Ctrl+C to stop the server.
echo.

python start_local.py

pause
