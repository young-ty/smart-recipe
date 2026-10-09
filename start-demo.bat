@echo off
rem 绕过 PowerShell 的执行策略限制，双击或命令行都能用。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-demo.ps1" %*
rem 双击启动时这个窗口会随脚本结束立刻关掉，自检 FAIL 的提示会来不及看：失败就停住等按键
if errorlevel 1 pause
