@echo off
rem 一键停止演示（等价于 stop-demo.ps1 -Force），同样绕过执行策略限制。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-demo.ps1" -Force %*
rem 双击启动时窗口会随脚本结束关掉：有没停掉的进程就停住，把提示留住
if errorlevel 1 pause
