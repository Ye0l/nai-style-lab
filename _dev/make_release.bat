@echo off
rem Builds _dev\release\NAI-Style-Lab.zip: the app plus an embedded Python, so it runs on a PC without Python.
setlocal
cd /d "%~dp0.."
set "OUT=_dev\release\NAI-Style-Lab"

if exist _dev\release rmdir /s /q _dev\release
mkdir "%OUT%\app" || exit /b 1
rem Empty, so the unzipped folder already shows where the user's data goes.
mkdir "%OUT%\data"
rem A fresh Python, not a copy of python\, so no local caches end up in the zip.
call "%~dp0setup_python.bat" "%OUT%\python" || exit /b 1

copy /y app\*.py "%OUT%\app\" >nul || exit /b 1
xcopy web "%OUT%\web\" /e /i /q >nul || exit /b 1
copy /y main.py "%OUT%\" >nul
copy /y run.bat "%OUT%\" >nul
copy /y README.md "%OUT%\" >nul

rem Windows' own tar (bsdtar) writes zip; a Unix tar earlier on PATH (Git's) does not.
"%SystemRoot%\System32\tar.exe" -a -cf _dev\release\NAI-Style-Lab.zip -C _dev\release NAI-Style-Lab || exit /b 1
rmdir /s /q "%OUT%"
explorer /select,"%CD%\_dev\release\NAI-Style-Lab.zip"
echo Built: _dev\release\NAI-Style-Lab.zip
