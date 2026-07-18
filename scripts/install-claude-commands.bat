@echo off
setlocal

for %%I in ("%~dp0..") do set "ROOT=%%~fI"

if defined CLAUDE_COMMANDS_DIR (
  set "DEST=%CLAUDE_COMMANDS_DIR%"
) else (
  set "DEST=%USERPROFILE%\.claude\commands"
)

rem --only <skill-name>: 只安装该 skill 的 .md；缺省保持全量安装
set "ONLY="
if "%~1"=="--only" (
  if "%~2"=="" (
    echo Error: --only requires a skill name 1>&2
    exit /b 2
  )
  set "ONLY=%~2"
) else if not "%~1"=="" (
  echo Error: unknown argument: %~1 1>&2
  exit /b 2
)

if defined ONLY (
  if not exist "%ROOT%\skills\%ONLY%.md" (
    echo Error: unknown skill '%ONLY%' ^(no skills\%ONLY%.md file^) 1>&2
    exit /b 1
  )
)

if not exist "%DEST%" mkdir "%DEST%"
if errorlevel 1 exit /b %ERRORLEVEL%

if defined ONLY (
  copy /Y "%ROOT%\skills\%ONLY%.md" "%DEST%\" >nul
) else (
  copy /Y "%ROOT%\skills\*.md" "%DEST%\" >nul
)
if errorlevel 1 exit /b %ERRORLEVEL%

if defined ONLY (
  echo Installed Claude Code command '%ONLY%' to %DEST%
) else (
  echo Installed Claude Code commands to %DEST%
)
