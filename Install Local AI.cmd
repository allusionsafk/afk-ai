@echo off
setlocal
rem Started from a PowerShell 7 terminal, cmd inherits PowerShell 7's module path
rem and Windows PowerShell then cannot load its own Get-FileHash or
rem Invoke-WebRequest, so the integrity check below could never pass. Let
rem Windows PowerShell compute its own default module path.
set "PSModulePath="
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
rem Otherwise (someone downloaded just this one file), fetch the bootstrap from
rem an IMMUTABLE commit and verify its SHA-256 before PowerShell may run it.
rem Fetching from master put every change to master between this launcher and
rem the pinned release. Release order: tag, pin bootstrap.ps1, then pin here.
set "BOOT=%~dp0installer\bootstrap.ps1"
if exist "%BOOT%" goto :run

set "BOOTSTRAP_COMMIT=78b4b13aaf32e4eff8b8a6cb9773e5aff7a289ef"
set "BOOTSTRAP_SHA256=440B3308BC11A3CA96432170A026B20AC7BA5A087C62B36112A4659CF3F619EF"
set "BOOT=%TEMP%\localai-bootstrap-%BOOTSTRAP_COMMIT%.ps1"
set "BOOT_URL=https://raw.githubusercontent.com/allusionsafk/afk-ai/%BOOTSTRAP_COMMIT%/installer/bootstrap.ps1"

echo   Downloading the installer...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; $out=$env:BOOT; Remove-Item -LiteralPath $out -Force -ErrorAction SilentlyContinue; Invoke-WebRequest -UseBasicParsing $env:BOOT_URL -OutFile $out; $actual=(Get-FileHash -LiteralPath $out -Algorithm SHA256).Hash.ToUpperInvariant(); $expected=$env:BOOTSTRAP_SHA256.ToUpperInvariant(); if ($actual -ne $expected) { Remove-Item -LiteralPath $out -Force -ErrorAction SilentlyContinue; Write-Error ('Installer integrity check failed. Expected SHA-256 ' + $expected + ', got ' + $actual + '. Refusing to run the downloaded bootstrap.'); exit 23 }"
if errorlevel 1 goto :failed
if not exist "%BOOT%" goto :failed

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
