@echo off
title Armador DTF - con internet
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0iniciar_con_internet.ps1"
pause
