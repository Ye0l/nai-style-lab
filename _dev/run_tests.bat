@echo off
rem Runs every offline test (fake image generator; NovelAI is never called).
cd /d "%~dp0.."
for %%t in (_dev\tests\test_*.py) do (
    rem -B: no __pycache__ from test runs, so the app folder stays as the release ships it.
    "python\python.exe" -B "%%t" || (echo FAILED: %%t & exit /b 1)
)
echo All tests passed.
