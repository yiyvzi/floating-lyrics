@echo off
setlocal
cd /d "%~dp0"
set "APP_PYTHON=.venv\Scripts\python.exe"

if not exist "%APP_PYTHON%" (
    call :create_env
    if errorlevel 1 goto failed
)

"%APP_PYTHON%" -m pip --version >nul 2>&1
if errorlevel 1 (
    "%APP_PYTHON%" -m ensurepip --upgrade --default-pip
    if errorlevel 1 goto failed
)

"%APP_PYTHON%" -c "from PySide6 import QtWidgets, QtNetwork; from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager" >nul 2>&1
if errorlevel 1 (
    "%APP_PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 goto failed
)

start "" /wait /min "%APP_PYTHON%" main.py
if errorlevel 1 goto failed
exit /b 0

:create_env
if exist "%LOCALAPPDATA%\Python\bin\python.exe" (
    "%LOCALAPPDATA%\Python\bin\python.exe" -m venv .venv
    if not errorlevel 1 exit /b 0
)
py -3 -m venv .venv
if not errorlevel 1 exit /b 0
python -m venv .venv
exit /b %errorlevel%

:failed
echo.
echo Floating Lyrics could not start. Check the message above and README.md.
pause
exit /b 1

