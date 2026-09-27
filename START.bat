@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 server.py 
) else (
    python server.py 
)
if errorlevel 1 (
    echo.
    echo Для запуска требуется Python 3.10 или новее.
    echo Убедитесь, что архив полностью распакован.
)
pause
