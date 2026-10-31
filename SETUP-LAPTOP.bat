@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Trading Agent - Laptop Setup
echo ================================================
echo    Trading Agent - laptop setup (one click)
echo ================================================
echo.

rem ---- 1. Git ----
where git >nul 2>nul
if errorlevel 1 (
  echo [X] Git install nahi hai.
  choice /C YN /M "Git abhi install karoon (winget se)"
  if errorlevel 2 goto :nogit
  winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
  echo.
  echo Git install ho gaya. Ye window band karke SETUP-LAPTOP.bat dobara double-click karo.
  pause
  exit /b 0
)

rem ---- 2. Python ----
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [X] Python install nahi hai.
  choice /C YN /M "Python 3.12 abhi install karoon (winget se)"
  if errorlevel 2 goto :nopy
  winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
  echo.
  echo Python install ho gaya. Ye window band karke SETUP-LAPTOP.bat dobara double-click karo.
  pause
  exit /b 0
)

rem ---- 3. Repo (Documents\trading-agent) ----
set "BASE=%USERPROFILE%\Documents"
if exist "%~dp0config.yaml" if exist "%~dp0.git" (
  set "DIR=%~dp0"
  goto :haverepo
)
set "DIR=%BASE%\trading-agent"
if exist "%DIR%\.git" goto :haverepo
echo [1/6] Repo download ho raha hai (GitHub login maange to apne account se login karo)...
cd /d "%BASE%"
git clone https://github.com/ah2233092/trading-agent.git
if errorlevel 1 (
  echo [X] Clone nahi hua. GitHub login check karo aur dobara chalao.
  pause
  exit /b 1
)
:haverepo
cd /d "%DIR%"
echo [1/6] Latest code le raha hoon...
git checkout -q main
git pull -q

rem ---- 4. venv + packages ----
echo [2/6] Python packages install ho rahe hain (pehli dafa 2-5 minute)...
%PY% scripts\setup_venv.py
if errorlevel 1 (
  echo [X] Python setup fail hua. Upar ka error Claude ko bhej do.
  pause
  exit /b 1
)
set "VPY=%DIR%\.venv\Scripts\python.exe"

rem ---- 5. .env ----
echo [3/6] Telegram settings...
"%VPY%" scripts\fill_env.py --chat-id <chat-id>
if errorlevel 1 (
  pause
  exit /b 1
)

rem ---- 6. Tests ----
echo.
echo [4/6] Offline tests...
"%VPY%" tests\test_smc.py
"%VPY%" tests\test_phase2.py
echo.
echo [5/6] Live MEXC data + Telegram test...
"%VPY%" scripts\test_data.py
"%VPY%" scripts\test_telegram.py
echo.
echo [6/6] Live deep scan (SOL)...
"%VPY%" lib\deep_scan.py --symbol SOL --no-git
echo.
echo ================================================
echo  Setup mukammal. Folder: %DIR%
echo  Ab is folder mein Claude Code kholo:  claude
echo  aur likho:  /deep-scan SOL
echo ================================================
pause
exit /b 0

:nogit
echo Git ke baghair setup nahi ho sakta. git-scm.com se install karo.
pause
exit /b 1
:nopy
echo Python ke baghair setup nahi ho sakta. python.org se install karo ("Add to PATH" tick karna).
pause
exit /b 1
