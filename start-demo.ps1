# 一键启动演示：先自检，再起后端与前端。
#
#   .\start-demo.ps1            自检 + 启动后端与前端 + 打开浏览器
#   .\start-demo.ps1 -Check     只自检，不启动
#   .\start-demo.ps1 -NoBrowser 启动但不自动开浏览器
#
# 已经起着的服务会被跳过（例如后端在跑、只想重开前端），重复双击不会起两套；
# 收尾用配套的 .\stop-demo.bat。
# 两个服务各开一个窗口：演示时要能看到日志，也要能 Ctrl+C 关掉，所以这里用可见窗口。
# 执行策略受限的话用配套的 start-demo.bat（它带 -ExecutionPolicy Bypass）。
param(
    [switch]$Check,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "找不到虚拟环境：$python" -ForegroundColor Red
    Write-Host "先按 README「快速开始 1」创建 .venv 并装依赖。" -ForegroundColor Yellow
    exit 1
}

function Test-PageReady {
    param([string]$Url)

    try {
        Invoke-WebRequest -Uri $Url -TimeoutSec 2 -UseBasicParsing | Out-Null
        return $true
    } catch {
        return $false
    }
}

# ---- 1. 自检：环境不对就别浪费时间启动 ----
& $python (Join-Path $root "backend\tools\preflight.py")
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "自检有必须先修的项，处理完再来。" -ForegroundColor Red
    exit 1
}
if ($Check) { exit 0 }

# ---- 2. 后端 ----
Write-Host ""
$backend = $null
$health = $null
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 3
    Write-Host "后端已在运行（http://127.0.0.1:8000），跳过启动。" -ForegroundColor Yellow
} catch {
    Write-Host "启动后端 http://127.0.0.1:8000 ..." -ForegroundColor Cyan
    $backend = Start-Process -FilePath $python `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "8000" `
        -WorkingDirectory (Join-Path $root "backend") -PassThru

    # 首次启动要建表、灌种子数据，还要加载 335 MB 权重，给它足够时间
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 3
            break
        } catch {
            Start-Sleep -Milliseconds 1500
        }
    }
}

if (-not $health) {
    Write-Host "后端 90 秒内没起来，去看后端那个窗口的报错。" -ForegroundColor Red
    Write-Host "要停掉它：Stop-Process -Id $($backend.Id)，或直接跑 .\stop-demo.bat" -ForegroundColor Yellow
    exit 1
}

$modelState = if ($health.model.loaded) { "已加载 " + $health.model.version } else { "未加载（降级为模拟结果）" }
$apiState = if ($health.generator.configured) { $health.generator.model } else { "未配置（库外菜会提示生成不可用）" }
Write-Host ("  识别模型：" + $modelState) -ForegroundColor DarkGray
Write-Host ("  大模型：" + $apiState) -ForegroundColor DarkGray
Write-Host ("  菜谱库：" + $health.recipe_count + " 道") -ForegroundColor DarkGray

# ---- 3. 前端 ----
$frontend = "http://localhost:5173/"
$frontendUp = Test-PageReady $frontend
if ($frontendUp) {
    Write-Host "前端已在运行（$frontend），跳过启动。" -ForegroundColor Yellow
} else {
    Write-Host "启动前端 $frontend ..." -ForegroundColor Cyan
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npm run dev" `
        -WorkingDirectory (Join-Path $root "frontend") | Out-Null

    # 等它就绪再开浏览器：慢机器上 4 秒不够，快机器上白等
    $frontendDeadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $frontendDeadline) {
        if (Test-PageReady $frontend) {
            $frontendUp = $true
            break
        }
        Start-Sleep -Milliseconds 1000
    }
    if (-not $frontendUp) {
        Write-Host "前端 60 秒内没起来，去看前端那个窗口的报错。" -ForegroundColor Red
        Write-Host "端口被上一次的服务占着就用 .\stop-demo.bat 清干净再试。" -ForegroundColor Yellow
    }
}

if (-not $NoBrowser -and $frontendUp) {
    Start-Process $frontend | Out-Null
}

Write-Host ""
Write-Host "就绪。演示入口：http://localhost:5173/" -ForegroundColor Green
Write-Host "收尾用一键停止： .\stop-demo.bat" -ForegroundColor DarkGray
if ($backend) {
    Write-Host ("  （也可以在后端窗口 Ctrl+C；本次后端进程 Id：" + $backend.Id + "）") -ForegroundColor DarkGray
}
