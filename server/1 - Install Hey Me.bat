@echo off
title Install Hey Me
cd /d "%~dp0"
echo Installing the parts Hey Me needs. This takes a few minutes...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Something went wrong. Is Python installed with "Add Python to PATH" checked?
  pause
  exit /b
)
echo.
set /p AUTO=Start Hey Me automatically when Windows starts? (Y/N) 
if /i "%AUTO%"=="Y" (
  echo @echo off> "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Hey Me.bat"
  echo cd /d "%~dp0">> "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Hey Me.bat"
  echo start "Hey Me" /min python hey_me_server.py>> "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Hey Me.bat"
  echo Done - Hey Me will start with Windows.
)
echo.
echo All installed! Now edit settings.py, then double-click "2 - Start Hey Me.bat".
pause
