@echo off
rem Starts ProcureTrack with the production server (waitress).
cd /d "%~dp0.."
".venv\Scripts\python.exe" app.py
