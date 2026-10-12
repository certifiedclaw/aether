@echo off
REM If install.bat ever closes without you seeing why, run THIS file
REM instead. cmd /k keeps the window open no matter what happens inside
REM install.bat -- even a fatal crash that would otherwise close the
REM window instantly -- so you can read the actual error and paste it
REM back.
cd /d "%~dp0"
cmd /k call install.bat
