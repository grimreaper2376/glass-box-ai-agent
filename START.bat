@echo off
setlocal enabledelayedexpansion
REM =======================================================================
REM GlassBox — one-click launcher for Windows.
REM
REM Double-click this file. It checks for Python, creates an isolated
REM environment the first time it runs, installs everything GlassBox needs,
REM then starts the dashboard and opens it in your browser automatically.
REM Every run after the first skips straight to launching, in a couple of
REM seconds.
REM =======================================================================
title GlassBox
cd /d "%~dp0backend"

echo.
echo   GlassBox
echo   ========
echo.

REM --- 1. Python present? -------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo   Python was not found on your PATH.
    echo.
    echo   Install it from https://www.python.org/downloads/windows/
    echo   and make sure to tick "Add python.exe to PATH" on the first
    echo   installer screen. Then double-click this file again.
    echo.
    pause
    exit /b 1
)

REM --- 2. First run: create the environment and install dependencies -----
if not exist ".venv\Scripts\python.exe" (
    echo   First time setup — this takes about a minute...
    echo.
    python -m venv .venv
    if errorlevel 1 (
        echo   Could not create the environment. See QUICKSTART_WINDOWS.md.
        pause
        exit /b 1
    )
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip --quiet
    pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   Package installation failed. Check your internet connection
        echo   and try again, or see QUICKSTART_WINDOWS.md for manual steps.
        pause
        exit /b 1
    )
    echo.
    echo   Setup complete.
) else (
    call .venv\Scripts\activate.bat
    REM A fast, network-free check: if everything GlassBox needs is already
    REM importable, skip pip entirely. Running "pip install" unconditionally
    REM on every launch would still reach out to check for newer versions
    REM even when nothing changed — meaning a machine that loses internet
    REM access after a successful first setup would hang on every later
    REM launch, which defeats the whole point of "one click, no waiting."
    python -c "import fastapi, uvicorn, httpx, pydantic, yaml, websockets" >nul 2>nul
    if errorlevel 1 (
        echo   Updating dependencies...
        pip install -r requirements.txt --quiet --disable-pip-version-check
    )
)

REM --- 3. Launch. Real Binance data by default, paper trading by default,
REM        browser opens itself. Nothing further to type. -----------------
echo.
echo   Starting GlassBox with live Binance market data...
echo   Your browser will open automatically in a moment.
echo   Close this window (or press Ctrl+C) to stop.
echo.
python -m glassbox serve

pause
