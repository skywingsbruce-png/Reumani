@echo off
chcp 65001 >nul
cd /d "%~dp0"
title SSc 研究方向智能体
echo.
echo ============================================
echo   正在启动 SSc 研究方向智能体网页版...
echo   启动后会自动打开浏览器 http://localhost:8501
echo   使用完想关闭网站：直接关掉这个黑色窗口即可
echo ============================================
echo.
streamlit run ssc_pi_agent_web.py
pause
