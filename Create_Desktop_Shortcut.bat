@echo off
setlocal EnableExtensions
chcp 65001 >nul
title 바탕화면 바로가기 만들기
cd /d "%~dp0"

set "APPDIR=%~dp0"
if "%APPDIR:~-1%"=="\" set "APPDIR=%APPDIR:~0,-1%"
set "TARGET=%~dp0Start_App.bat"
set "LNKNAME=AI 숏폼 릴스 자동 제작기.lnk"

set "DESKTOP="
if exist "%USERPROFILE%\Desktop\" set "DESKTOP=%USERPROFILE%\Desktop"
if not defined DESKTOP if exist "%USERPROFILE%\OneDrive\Desktop\" set "DESKTOP=%USERPROFILE%\OneDrive\Desktop"
if not defined DESKTOP set "DESKTOP=%USERPROFILE%\Desktop"

if not exist "%TARGET%" (
  echo [오류] Start_App.bat 을 찾을 수 없습니다.
  echo 위치: %TARGET%
  pause
  exit /b 1
)

set "VBS=%TEMP%\create_shorts_maker_shortcut.vbs"
> "%VBS%" echo Set oWS = CreateObject("WScript.Shell")
>> "%VBS%" echo sLink = oWS.ExpandEnvironmentStrings("%DESKTOP%\%LNKNAME%")
>> "%VBS%" echo Set oLink = oWS.CreateShortcut(sLink)
>> "%VBS%" echo oLink.TargetPath = "%TARGET%"
>> "%VBS%" echo oLink.WorkingDirectory = "%APPDIR%"
>> "%VBS%" echo oLink.WindowStyle = 1
>> "%VBS%" echo oLink.Description = "AI 숏폼 릴스 원클릭 자동 제작기"
>> "%VBS%" echo oLink.Save

cscript //nologo "%VBS%"
set "ERR=%ERRORLEVEL%"
del "%VBS%" >nul 2>&1

if not "%ERR%"=="0" (
  echo [오류] 바로가기 생성에 실패했습니다.
  pause
  exit /b %ERR%
)

echo.
echo 바탕화면에 바로가기를 만들었습니다.
echo   %DESKTOP%\%LNKNAME%
echo.
echo 이제 바탕화면 아이콘을 더블클릭하면 프로그램이 실행됩니다.
echo.
pause
endlocal
exit /b 0
