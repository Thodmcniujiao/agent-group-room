@echo off
chcp 65001 >nul
title GPT-SoVITS Training WebUI (RTX 5080)
cd /d "%~dp0GPT-SoVITS-Engine\GPT-SoVITS-v2pro-20250604-nvidia50"
call go-webui.bat
pause
