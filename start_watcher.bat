@echo off
REM SWA Data Analysis Tool - background watcher (Windows).
REM Put your config.yaml next to this file, or edit the path below. Leave this window open (or minimise it).
cd /d "%~dp0"
python -m swa_data_tool watch --config config.yaml
pause
