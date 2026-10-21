@echo off
setlocal
set "OUT=%~dp0"
cd /d "%~dp0..\.."
set "AGENT_ROOM_DUBIOUS_REPO=C:\Users\Thodmc\OneDrive\Desktop\New folder"
del "%OUT%done.txt" 2>nul
(git --version & where git) > "%OUT%git-version.txt" 2>&1
git -C "%AGENT_ROOM_DUBIOUS_REPO%" status --short > "%OUT%plain-git-new-folder.txt" 2>&1
echo exit=%ERRORLEVEL%>> "%OUT%plain-git-new-folder.txt"
.venv\Scripts\python.exe gui\test_git_bridge.py -v > "%OUT%git-bridge.log" 2>&1
echo exit=%ERRORLEVEL%>> "%OUT%git-bridge.log"
.venv\Scripts\python.exe scripts\run_context_checks.py --report-dir test-reports\git-quota-2026-10-21 > "%OUT%full-suite.txt" 2>&1
echo exit=%ERRORLEVEL%>> "%OUT%full-suite.txt"
echo finished> "%OUT%done.txt"
