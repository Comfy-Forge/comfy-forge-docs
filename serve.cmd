@echo off
setlocal
rem Usage: serve [port]   (default 8001)
set "PORT=%~1"
if "%PORT%"=="" set "PORT=8001"
"%~dp0.venv\Scripts\mkdocs.exe" serve -f "%~dp0mkdocs.yml" --dev-addr 0.0.0.0:%PORT%
