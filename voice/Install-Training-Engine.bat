@echo off
chcp 65001 >nul
title Deploy GPT-SoVITS Training Engine (RTX 5080)
cd /d "%~dp0"
echo ==========================================================
echo   开始部署 RTX 5080 专供 GPT-SoVITS 深度训练引擎
echo ==========================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Deploy-GPTSoVITS-Engine.ps1"
pause
