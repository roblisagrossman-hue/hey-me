@echo off
title Hey Me Listener
cd /d "%~dp0"
python -m pip install -q -r requirements.txt
python hey_me_satellite.py
pause
