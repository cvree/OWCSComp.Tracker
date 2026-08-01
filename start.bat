@echo off
REM ===================================================================
REM  OWCS Comp Tracker - double-click this file to start.
REM
REM  It checks your tools, tells you plainly what is missing and how to
REM  fix it, then starts the control room and opens the portal in your
REM  browser. Nothing here downloads or changes anything on its own.
REM
REM  Close this window (or press Ctrl+C) to stop.
REM ===================================================================
setlocal
cd /d "%~dp0"
title OWCS Comp Tracker

echo.
echo   OWCS Comp Tracker
echo   -----------------
echo.

REM --- Python is the one thing we cannot check FOR you without it ------
where python >nul 2>nul
if errorlevel 1 (
  echo   Python is not installed, or was installed without "Add python.exe
  echo   to PATH" ticked.
  echo.
  echo   Install it from https://www.python.org/downloads/ and TICK
  echo   "Add python.exe to PATH" on the first screen of the installer,
  echo   then run this file again.
  echo.
  pause
  exit /b 1
)

echo   Checking your tools...
echo.
python pipeline\preflight.py
echo.

REM preflight exits non-zero when something REQUIRED is missing. It has
REM already printed the remedy for each one, so don't repeat it - just
REM stop before opening a portal that cannot do anything.
if errorlevel 1 (
  echo   ---------------------------------------------------------------
  echo   Something above is missing. Fix the items marked FAIL, then run
  echo   this file again. The full walkthrough is in start.html on the
  echo   site, under "Start here".
  echo   ---------------------------------------------------------------
  echo.
  pause
  exit /b 1
)

echo   Starting the control room. Your browser will open in a moment.
echo   Leave THIS window open while you work - it is the program.
echo.
python pipeline\serve.py
echo.
echo   Stopped.
pause
