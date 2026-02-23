@echo off
cd /d "%~dp0"

echo Adding changes...
git add .

echo Committing...
set /p msg=Enter commit message: 
git commit -m "%msg%"

echo Pushing to GitHub...
git push

echo Done!
pause