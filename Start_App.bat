@echo off
chcp 65001 >nul
title AI 숏폼 릴스 원클릭 자동 제작기
cd /d "%~dp0"

echo.
echo  ============================================
echo    AI 숏폼 릴스 원클릭 자동 제작기
echo  ============================================
echo.
echo  실행 중... 브라우저가 자동으로 열립니다.
echo  이 창은 닫지 마세요. 종료 후에도 아무 키나 누르면 닫힙니다.
echo.

start "" cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:8501/"

python -m streamlit run app.py --server.headless false --browser.gatherUsageStats false

echo.
echo  프로그램이 종료되었습니다. 오류 메시지가 있으면 위를 확인하세요.
pause
