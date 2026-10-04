@echo off
cd /d "%~dp0"
rem python\ is the embedded Python: shipped in the release zip, made by _dev\setup_python.bat in a source checkout.
if not exist "python\pythonw.exe" (echo python\ is missing: run _dev\setup_python.bat first. & pause & exit /b 1)
start "" "python\pythonw.exe" "%~dp0main.py"
exit /b
