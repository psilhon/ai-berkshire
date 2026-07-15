@echo off
setlocal

for %%I in ("%~dp0..") do set "ROOT=%%~fI"

if defined CODEX_HOME (
  set "DEST=%CODEX_HOME%\skills"
) else (
  set "DEST=%USERPROFILE%\.codex\skills"
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  set "PY=py -3"
) else (
  set "PY=python"
)

%PY% "%ROOT%\scripts\sync-codex-skills.py"
if errorlevel 1 exit /b %ERRORLEVEL%

if not exist "%DEST%" mkdir "%DEST%"
if errorlevel 1 exit /b %ERRORLEVEL%

rem 覆盖前把已存在的同名 skill 移入备份目录（滚动保留上一代）
set "BACKUP=%DEST%-backup"

for /d %%D in ("%ROOT%\codex-skills\*") do (
  if exist "%DEST%\%%~nxD" (
    if not exist "%BACKUP%" mkdir "%BACKUP%"
    if exist "%BACKUP%\%%~nxD" rmdir /s /q "%BACKUP%\%%~nxD"
    move "%DEST%\%%~nxD" "%BACKUP%\%%~nxD" >nul
    if errorlevel 1 exit /b 1
  )
  xcopy "%%~fD" "%DEST%\%%~nxD\" /E /I /Y >nul
  if errorlevel 1 exit /b 1
)

if exist "%BACKUP%" echo Previous versions backed up to %BACKUP% (one generation kept).
echo Installed Codex skills to %DEST%
echo Run .\scripts\install-codex-prompts.bat if you want slash-command prompts.
echo Restart Codex to pick up new skills.
