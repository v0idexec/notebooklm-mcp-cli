@echo off
REM ============================================================================
REM  NotebookLM MCP & CLI - Windows installer
REM
REM  Installs everything needed to run this tool on Windows:
REM    1. uv            (Python package/tool manager from Astral)
REM    2. Python 3.12   (downloaded & managed by uv - no system Python needed)
REM    3. notebooklm-mcp-cli  (the `nlm` CLI + `notebooklm-mcp` server)
REM
REM  NOTE: Node.js is NOT required. This is a pure-Python tool.
REM  A Chromium browser (Chrome / Edge / Brave) is needed for `nlm login`.
REM  Microsoft Edge ships with Windows, so login works out of the box.
REM
REM  Usage: double-click this file, or run it from a terminal.
REM ============================================================================

setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title NotebookLM MCP/CLI - Windows Installer

REM ---- Your fork on GitHub (change here if the repo URL changes) -------------
set "REPO_URL=https://github.com/v0idexec/notebooklm-mcp-cli.git"

echo.
echo ============================================================
echo   NotebookLM MCP ^& CLI - Windows Installer
echo ============================================================
echo.

REM ----------------------------------------------------------------------------
REM Step 1: Make sure winget exists (used to install uv if missing)
REM ----------------------------------------------------------------------------
where winget >nul 2>&1
if errorlevel 1 (
    set "HAVE_WINGET=0"
    echo [!] winget not found. Will use the official PowerShell installer for uv.
) else (
    set "HAVE_WINGET=1"
)

REM ----------------------------------------------------------------------------
REM Step 2: Install uv (if not already installed)
REM ----------------------------------------------------------------------------
echo.
echo [*] Checking for uv...
where uv >nul 2>&1
if errorlevel 1 (
    echo [*] uv not found. Installing uv...
    if "!HAVE_WINGET!"=="1" (
        winget install --id=astral-sh.uv -e --accept-source-agreements --accept-package-agreements
    ) else (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    )
) else (
    echo [+] uv is already installed.
)

REM Add the usual uv install locations to PATH for THIS session
set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"

REM Re-check uv is now reachable
where uv >nul 2>&1
if errorlevel 1 (
    echo.
    echo [X] uv was installed but is not on PATH yet.
    echo     Please CLOSE this window, open a NEW terminal, and run this script again.
    echo.
    pause
    exit /b 1
)
echo [+] uv is ready.

REM ----------------------------------------------------------------------------
REM Step 3: Install a managed Python 3.12 (uv downloads it - no admin needed)
REM ----------------------------------------------------------------------------
echo.
echo [*] Installing Python 3.12 (managed by uv)...
uv python install 3.12
if errorlevel 1 (
    echo [X] Failed to install Python via uv.
    pause
    exit /b 1
)
echo [+] Python is ready.

REM ----------------------------------------------------------------------------
REM Step 4: Install the tool directly from your GitHub fork
REM ----------------------------------------------------------------------------
echo.
echo [*] Installing notebooklm-mcp-cli from your fork:
echo     %REPO_URL%

REM `git+` lets uv clone & build the repo. Requires git to be installed.
where git >nul 2>&1
if errorlevel 1 (
    echo [*] git not found. Installing git...
    if "!HAVE_WINGET!"=="1" (
        winget install --id=Git.Git -e --accept-source-agreements --accept-package-agreements
    ) else (
        echo [X] Cannot install git automatically without winget.
        echo     Please install Git from https://git-scm.com/ and re-run this script.
        pause
        exit /b 1
    )
    set "PATH=%ProgramFiles%\Git\cmd;%PATH%"
)

uv tool install --python 3.12 --force "git+%REPO_URL%"
set "INSTALL_RC=%errorlevel%"

if not "%INSTALL_RC%"=="0" (
    echo.
    echo [X] Installation failed. See the messages above.
    pause
    exit /b 1
)

REM Make sure uv's tool bin dir is on PATH for the user
uv tool update-shell >nul 2>&1

REM ----------------------------------------------------------------------------
REM Step 5: Browser check (informational only)
REM ----------------------------------------------------------------------------
echo.
echo [*] Checking for a Chromium browser (needed for `nlm login`)...
set "FOUND_BROWSER="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe"            set "FOUND_BROWSER=Google Chrome"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"       set "FOUND_BROWSER=Google Chrome"
if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"            set "FOUND_BROWSER=Google Chrome"
if not defined FOUND_BROWSER if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "FOUND_BROWSER=Microsoft Edge"
if not defined FOUND_BROWSER if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"      set "FOUND_BROWSER=Microsoft Edge"

if defined FOUND_BROWSER (
    echo [+] Found: !FOUND_BROWSER!
) else (
    echo [!] No Chromium browser detected. Edge usually ships with Windows.
    echo     If `nlm login` fails, install Google Chrome:
    echo         winget install --id=Google.Chrome -e
)

REM ----------------------------------------------------------------------------
REM Done
REM ----------------------------------------------------------------------------
echo.
echo ============================================================
echo   Installation complete!
echo ============================================================
echo.
echo   IMPORTANT: open a NEW terminal so PATH changes take effect.
echo.
echo   Next steps:
echo     1) Log in to NotebookLM:    nlm login
echo     2) List your notebooks:     nlm notebook list
echo     3) Run the MCP server:      notebooklm-mcp
echo.
pause
endlocal
