@echo off
rem Start the app with the page at its minimum width (#app's min-width in web\app.css), to check the narrowest layout.
rem An app already running is only brought to the front: close it first (it is replaced anyway after a code change).
cd /d "%~dp0.."
if not exist "python\pythonw.exe" (echo python\ is missing: run _dev\setup_python.bat first. & pause & exit /b 1)
set NAI_PAGE_WIDTH=1022
start "" "python\pythonw.exe" "%~dp0..\main.py"
