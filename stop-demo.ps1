# 一键停止演示：把占用 8000 / 5173 的进程找出来结束掉。
#
#   .\stop-demo.ps1             先列出要结束的进程，逐个确认
#   .\stop-demo.ps1 -Force      不确认，直接结束（演示收尾/重启时用）
#   .\stop-demo.ps1 -ListOnly   只列出，不动手
#
# 为什么需要它：start-demo 会各开一个窗口，收尾时要去两个窗口各按一次 Ctrl+C；
# 而 5173 现在开了 strictPort（端口固定，localStorage 里的菜谱记忆才按 origin 找得到），
# 上一次没关干净会让下一次启动直接失败，所以给一个能一键清干净的入口。
#
# 只结束「看起来是我们自己的」进程，三种依据任一命中即可：
#   1) 命令行里带本仓库路径；
#   2) python 进程且命令行是 uvicorn 起 app.main（venv 的 python 会转发到基础解释器，
#      Windows 上拿到的进程映像可能是 Anaconda 的 python.exe，靠路径认不出来）；
#   3) node 进程且命令行是 vite。
# 别的程序占着这两个端口时只提示、不动它。
param(
    [switch]$Force,
    [switch]$ListOnly
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
# 与 start-demo.ps1 / backend/tools/preflight.py 保持同一组端口
$ports = @(8000, 5173)

function Get-ListenerPids {
    param([int]$Port)

    try {
        return @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        # 老系统或精简版 PowerShell 没有该 cmdlet：退回 netstat 解析
        return @(netstat -ano |
            Select-String -Pattern (":{0}\s+\S+\s+LISTENING\s+(\d+)" -f $Port) |
            ForEach-Object { [int]$_.Matches[0].Groups[1].Value } |
            Select-Object -Unique)
    }
}

$targets = @()
foreach ($port in $ports) {
    foreach ($procId in (Get-ListenerPids -Port $port)) {
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        $commandLine = (Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $procId) -ErrorAction SilentlyContinue).CommandLine
        $isOurBackend = $proc.ProcessName -eq "python" -and $commandLine -match "uvicorn" -and $commandLine -match "app\.main"
        $isOurFrontend = $proc.ProcessName -eq "node" -and $commandLine -match "vite"
        $targets += [pscustomobject]@{
            Port        = $port
            Id          = $procId
            Name        = $proc.ProcessName
            CommandLine = $commandLine
            IsOurs      = ($commandLine -and $commandLine -like ("*" + $root + "*")) -or $isOurBackend -or $isOurFrontend
        }
    }
}

if ($targets.Count -eq 0) {
    Write-Host "8000 与 5173 都没有服务在跑，无需停止。" -ForegroundColor Green
    exit 0
}

$ours = @($targets | Where-Object { $_.IsOurs })
$foreign = @($targets | Where-Object { -not $_.IsOurs })

foreach ($item in $targets) {
    $tag = if ($item.IsOurs) { "本项目" } else { "非本项目" }
    Write-Host ("  [{0}] {1}:{2}  {3}" -f $tag, $item.Port, $item.Id, $item.Name) -ForegroundColor DarkGray
    if ($item.CommandLine) {
        Write-Host ("        " + $item.CommandLine) -ForegroundColor DarkGray
    }
}

if ($foreign.Count -gt 0) {
    Write-Host ""
    Write-Host ("有 {0} 个进程占着端口但看着不像本项目，已跳过不动它：" -f $foreign.Count) -ForegroundColor Yellow
    Write-Host ("  " + (($foreign | ForEach-Object { "{0}({1})" -f $_.Name, $_.Id }) -join "、")) -ForegroundColor Yellow
}

if ($ListOnly) { exit 0 }

if ($ours.Count -eq 0) {
    Write-Host "没有可结束的进程。" -ForegroundColor Yellow
    exit 0
}

if (-not $Force) {
    $answer = Read-Host ("结束上面 {0} 个进程？(y/N)" -f $ours.Count)
    if ($answer -notin @("y", "Y", "yes", "YES")) {
        Write-Host "已取消。" -ForegroundColor Yellow
        exit 0
    }
}

$failed = @()
foreach ($item in $ours) {
    try {
        Stop-Process -Id $item.Id -Force -ErrorAction Stop
        Write-Host ("  已结束 {0}:{1}（{2}）" -f $item.Port, $item.Id, $item.Name) -ForegroundColor Green
    } catch {
        $failed += $item
        Write-Host ("  结束失败 {0}:{1} —— {2}" -f $item.Port, $item.Id, $_.Exception.Message) -ForegroundColor Red
    }
}

Start-Sleep -Milliseconds 500
$left = @($ports | Where-Object { (Get-ListenerPids -Port $_).Count -gt 0 })
if ($left.Count -eq 0) {
    Write-Host "端口已清干净，可以重新 start-demo。" -ForegroundColor Green
} else {
    Write-Host ("还在占用：" + ($left -join "、")) -ForegroundColor Yellow
}
if ($failed.Count -gt 0) { exit 1 }
