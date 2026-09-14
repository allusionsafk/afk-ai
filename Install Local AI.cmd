@echo off
title AFK AI - installer
echo.
echo   AFK AI - guided installer
echo   -------------------------
echo   This checks your PC, picks AI models that fit your graphics card,
echo   and sets up a local-first AI chat on your own PC.
echo   Model inference can stay local. Setup, model downloads, and features
echo   such as web search can use the internet when needed or enabled.
echo.
echo   If an earlier try failed, no cleanup needed - the installer moves the
echo   old folder aside by itself and starts fresh.
echo.

rem If this .cmd sits inside the downloaded repo, run the local bootstrap.
rem Otherwise (someone downloaded just this one file), fetch it from master.
set "BOOT=%~dp0installer\bootstrap.ps1"
if exist "%BOOT%" goto :run

echo   Downloading the installer...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/allusionsafk/localai-windows-starter/master/installer/bootstrap.ps1' -OutFile ($env:TEMP + '\localai-bootstrap.ps1')"
if errorlevel 1 goto :failed
set "BOOT=%TEMP%\localai-bootstrap.ps1"

:run
echo   Starting the installer...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%BOOT%" %*
rem Exit 10 = a planned pause that needs something from you, not a failure.
rem This code is a fixed contract: installers already downloaded from the website
rem route 10 here and treat anything higher as an unexpected error.
if errorlevel 11 goto :failed
if errorlevel 10 goto :actionneeded
if errorlevel 1 goto :failed
echo.
echo   Finished. Your chat is at http://127.0.0.1:3000 (see the summary above).
goto :done

:actionneeded
echo.
echo   Almost there - AFK AI stopped on purpose and needs one thing from you.
echo   Read the steps above this line and do them (they may include restarting
echo   Windows), then double-click this file again.
echo   AFK AI re-checks your PC and continues where it left off - nothing you
echo   have already set up is lost.
goto :done

:failed
echo.
echo   Something went wrong - read the messages above this line.
echo   Double-click this file again to retry; it continues where it left
echo   off and moves any broken old folder aside automatically.

:done
echo.
pause
