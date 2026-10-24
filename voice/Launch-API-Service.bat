@echo off
chcp 65001 >nul
title GPT-SoVITS RTX 5080 Inference API Service (Port 9880)
cd /d "%~dp0GPT-SoVITS-Engine\GPT-SoVITS-v2pro-20250604-nvidia50"
runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
pause
