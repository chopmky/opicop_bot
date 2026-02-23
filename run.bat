@echo off
cd /d "%~dp0"

call venv\Scripts\activate

python opicop_bot.py

pause