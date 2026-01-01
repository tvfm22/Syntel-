@echo off
echo Clearing Django cache...
rd /s /q __pycache__ 2>nul
for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"
echo Cache cleared successfully!
pause
