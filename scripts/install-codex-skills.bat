@echo off
setlocal

for %%I in ("%~dp0..") do set "ROOT=%%~fI"

if defined CODEX_HOME (
  set "DEST=%CODEX_HOME%\skills"
) else (
  set "DEST=%USERPROFILE%\.codex\skills"
)

rem --only <skill-name>: 只安装该 skill；缺省保持全量安装
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

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  set "PY=py -3"
) else (
  set "PY=python"
)

%PY% "%ROOT%\scripts\sync-codex-skills.py"
if errorlevel 1 exit /b %ERRORLEVEL%

if defined ONLY (
  if not exist "%ROOT%\codex-skills\%ONLY%" (
    echo Error: unknown skill '%ONLY%' ^(no codex-skills\%ONLY% directory^) 1>&2
    exit /b 1
  )
)

if not exist "%DEST%" mkdir "%DEST%"
if errorlevel 1 exit /b %ERRORLEVEL%

rem 覆盖前把已存在的同名 skill 移入备份目录（滚动保留上一代）
set "BACKUP=%DEST%-backup"

for /d %%D in ("%ROOT%\codex-skills\*") do (
  if not defined ONLY (
    call :install_one "%%~fD" "%%~nxD"
    if errorlevel 1 exit /b 1
  ) else if "%%~nxD"=="%ONLY%" (
    call :install_one "%%~fD" "%%~nxD"
    if errorlevel 1 exit /b 1
  )
)

if exist "%BACKUP%" echo Previous versions backed up to %BACKUP% (one generation kept).
if defined ONLY (
  echo Installed Codex skill '%ONLY%' to %DEST%
) else (
  echo Installed Codex skills to %DEST%
)
echo Run .\scripts\install-codex-prompts.bat if you want slash-command prompts.
echo Restart Codex to pick up new skills.
exit /b 0

:install_one
if exist "%DEST%\%~2" (
  if not exist "%BACKUP%" mkdir "%BACKUP%"
  if exist "%BACKUP%\%~2" rmdir /s /q "%BACKUP%\%~2"
  move "%DEST%\%~2" "%BACKUP%\%~2" >nul
  if errorlevel 1 exit /b 1
)
xcopy "%~1" "%DEST%\%~2\" /E /I /Y >nul
if errorlevel 1 exit /b 1
exit /b 0
