@echo off
rem Backs up the ProcureTrack database. Schedule this with Windows Task Scheduler (see README).
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m flask --app app backup
