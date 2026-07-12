@echo off
chcp 65001 >nul
:: —— 双击本文件即可把「每日文献哨兵」注册为 Windows 定时任务（每天 9:00 自动跑）——
:: 需要管理员权限，双击后会弹出 UAC 授权窗口，点「是」即可。

:: 自动申请管理员权限
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo 正在申请管理员权限...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo ============================================
echo   正在注册「SSc/SLE 每日文献哨兵」定时任务
echo   每天上午 9:00 自动扫描新文献并生成报告
echo ============================================
echo.

schtasks /create /tn "SSc_SLE_DailyLiteratureWatch" /tr "\"F:\R\python.exe\" \"F:\SSC\My_AGI_MrCat\ssc_daily_watch.py\"" /sc daily /st 09:00 /f

echo.
if %errorlevel% equ 0 (
    echo [成功] 定时任务已注册！以后电脑开机且登录时，每天 9:00 会自动运行。
    echo 报告会存到：F:\SSC\My_AGI_MrCat\daily_reports\
    echo 也可以在网页的「每日文献」页面查看。
) else (
    echo [失败] 注册未成功，请把上面的错误信息发给我。
)
echo.
pause
