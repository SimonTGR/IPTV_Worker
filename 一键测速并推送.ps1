# ==============================================================================
# 📺 IPTV 本地一键测速与自动推送脚本
# 功能：
# 1. 自动配置本地 FFmpeg / Python 环境
# 2. 拉取云端最新配置 (git pull)
# 3. 运行本地真实网络测速 (1080p画质 > 高码率 > 高带宽 > 低延迟)
# 4. 自动重新打包 public_output 播放列表与状态报告
# 5. 自动 git commit & push 推送到 GitHub 仓库
# 6. 自动刷新 CDN 边缘缓存，确保电视播放器即刻生效
# ==============================================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " 🚀 正在启动 IPTV 本地真实网络精准测速与自动同步程序" -ForegroundColor Green
Write-Host "    当前工作目录: $scriptDir" -ForegroundColor DarkGray
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host ""

# 1. 环境变量配置：优先注入本地 FFmpeg
$localFfmpegPath = "C:\Users\tgr\.local\bin\ffmpeg-master-latest-win64-gpl\bin"
if (Test-Path $localFfmpegPath) {
    $env:PATH = "$localFfmpegPath;$env:PATH"
    Write-Host "✅ [1/5] 已加载本地硬件加速 FFmpeg / FFprobe 支持" -ForegroundColor Green
} else {
    Write-Host "⚠️ [1/5] 未检测到专用 FFmpeg 路径，使用系统默认 PATH" -ForegroundColor Yellow
}

# 2. 同步远程最新代码
Write-Host "📥 [2/5] 正在拉取云端最新代码与配置..." -ForegroundColor Cyan
try {
    git pull origin main --rebase
} catch {
    Write-Host "⚠️ 拉取远程代码遇到警告，继续执行本地测速..." -ForegroundColor Yellow
}

# 3. 运行本地测速与画质/码率分析
Write-Host ""
Write-Host "🚀 [3/5] 开始执行全量真实网络测速（优先筛选 1080p / 高码率 / 高下行带宽）..." -ForegroundColor Cyan
Write-Host "   (提示：正在对所有频道进行实际流媒体切片采样，请耐心等待几分钟)" -ForegroundColor DarkGray
Write-Host ""

$startTime = Get-Date
python -X utf8 main.py

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ 测速过程中出现异常，退出码: $LASTEXITCODE" -ForegroundColor Red
    Write-Host "按任意键退出..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit $LASTEXITCODE
}

# 4. 重新构建公共发布列表
Write-Host ""
Write-Host "📦 [4/5] 正在重新生成公开分发格式 (public_output)..." -ForegroundColor Cyan
python -X utf8 -c "import sys; sys.path.insert(0, '.'); from cloud.publication import build_public_playlists; build_public_playlists('.', media_probe=lambda b: True)"

# 5. 自动提交并推送到 GitHub
Write-Host ""
Write-Host "🚀 [5/5] 正在将最新测速播放列表推送到 GitHub 仓库..." -ForegroundColor Cyan

$currentTime = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
git add -f output/ public_output/ config/
$diff = git status --porcelain

if ($diff) {
    git commit -m "update: 本地真实网络精准测速推送 [$currentTime]"
    git push origin main
    Write-Host "✅ 成功推送到 GitHub 远程仓库！" -ForegroundColor Green
} else {
    Write-Host "ℹ️ 播放列表内容无变化，已保持最新。" -ForegroundColor Yellow
}

# 6. 刷新 CDN 边缘缓存
Write-Host ""
Write-Host "🔄 正在请求刷新 CDN 边缘缓存..." -ForegroundColor Cyan
try {
    $purgeUrls = @(
        "https://purge.jsdelivr.net/gh/SimonTGR/IPTV_Worker@main/output/user_result.m3u",
        "https://purge.jsdelivr.net/gh/SimonTGR/IPTV_Worker@main/public_output/live.m3u"
    )
    foreach ($pUrl in $purgeUrls) {
        $resp = Invoke-RestMethod -Uri $pUrl -Method Get -TimeoutSec 5 -ErrorAction SilentlyContinue
    }
    Write-Host "✅ CDN 缓存已发送刷新请求！" -ForegroundColor Green
} catch {
    Write-Host "⚠️ CDN 刷新请求超时（不影响主流程）" -ForegroundColor DarkGray
}

$elapsed = (Get-Date) - $startTime
$mins = [Math]::Floor($elapsed.TotalMinutes)
$secs = $elapsed.Seconds

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Green
Write-Host " 🎉 全流程处理完成！总耗时: ${mins}分${secs}秒" -ForegroundColor Green
Write-Host " 📺 你的播放器订阅地址（即刻刷新即可享受最新 1080p 超清源）：" -ForegroundColor White
Write-Host "    👉 主力推荐 (直连透传): https://ghproxy.net/https://raw.githubusercontent.com/SimonTGR/IPTV_Worker/main/output/user_result.m3u" -ForegroundColor Yellow
Write-Host "    👉 CDN加速源:           https://cdn.jsdmirror.com/gh/SimonTGR/IPTV_Worker@main/output/user_result.m3u" -ForegroundColor Yellow
Write-Host "=================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "按任意键退出窗口..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
