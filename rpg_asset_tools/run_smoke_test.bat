@echo off
cd /d "%~dp0"
python smoke_test_qt.py %*
pause
