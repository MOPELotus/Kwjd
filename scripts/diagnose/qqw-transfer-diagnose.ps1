#Requires -Version 7.0
<#
《卿卿误我》7 集传输上传/下载诊断（Windows PowerShell 7）。
测试只上传 8 MiB 随机数据，不上传视频，不触碰已有 FRP 配置。
Usage: pwsh -NoProfile -File .\qqw-transfer-diagnose.ps1
#>
[CmdletBinding()]
param(
    [string]$SshAlias = 'TodayEatUbuntu-7K62',
    [string]$SourceDir = 'D:\K坑王驾到S1',
    [int]$ProbeMiB = 8,
    [int]$TimeoutSeconds = 60
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Section([string]$Title) {
    Write-Host "`n========== $Title ==========" -ForegroundColor Cyan
}
function Invoke-ScpProbe([string]$LocalFile, [string]$RemoteName, [bool]$Legacy, [bool]$Download = $false) {
    $scpExe = (Get-Command scp -ErrorAction Stop).Source
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $scpExe
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardError = $true
    foreach ($a in @('-B','-q','-o','BatchMode=yes','-o','Compression=no','-o','ConnectTimeout=15')) {
        [void]$psi.ArgumentList.Add($a)
    }
    if ($Legacy) { [void]$psi.ArgumentList.Add('-O') }
    if ($Download) {
        [void]$psi.ArgumentList.Add("${SshAlias}:qingqingwu/work/$RemoteName")
        [void]$psi.ArgumentList.Add($LocalFile)
    } else {
        [void]$psi.ArgumentList.Add($LocalFile)
        [void]$psi.ArgumentList.Add("${SshAlias}:qingqingwu/work/$RemoteName")
    }
    $p = [System.Diagnostics.Process]::new()
    $p.StartInfo = $psi
    $modeName = "$(if ($Download) {'Download'} else {'Upload'}) / $(if ($Legacy) {'legacy SCP (-O)'} else {'SFTP'})"
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    if (-not $p.Start()) { throw '无法启动 scp' }
    $finished = $p.WaitForExit($TimeoutSeconds * 1000)
    $timer.Stop()
    if (-not $finished) {
        try { $p.Kill($true); $p.WaitForExit(3000) | Out-Null } catch { }
        return [pscustomobject]@{ Mode=$modeName; Result="超时，低于约 $([math]::Round($ProbeMiB / $TimeoutSeconds, 2)) MiB/s"; Seconds=$timer.Elapsed.TotalSeconds; MiBs=$null; Error='' }
    }
    $stderr = $p.StandardError.ReadToEnd().Trim()
    $exitCode = $p.ExitCode
    $p.Dispose()
    if ($exitCode -ne 0) {
        return [pscustomobject]@{ Mode=$modeName; Result="失败（退出码 $exitCode）"; Seconds=$timer.Elapsed.TotalSeconds; MiBs=$null; Error=$stderr }
    }
    return [pscustomobject]@{ Mode=$modeName; Result='成功'; Seconds=[math]::Round($timer.Elapsed.TotalSeconds,2); MiBs=[math]::Round($ProbeMiB / [math]::Max($timer.Elapsed.TotalSeconds,0.001),2); Error='' }
}

Section '1. Windows 环境和文件范围'
Write-Host "PowerShell $($PSVersionTable.PSVersion)"
Write-Host "SSH alias: $SshAlias"
Write-Host "Source: $SourceDir"
foreach ($exe in @('ssh','scp')) { Write-Host "$exe = $((Get-Command $exe -ErrorAction Stop).Source)" }
if (-not (Test-Path -LiteralPath $SourceDir -PathType Container)) { throw "找不到目录: $SourceDir" }
$videos = @(Get-ChildItem -LiteralPath $SourceDir -File -Filter '*.mp4' | Where-Object { $_.Name -match '^坑王驾到S01E(49|5[0-5])\.' } | Sort-Object Name)
$videos | ForEach-Object { '{0,9:N2} GiB  {1}' -f ($_.Length / 1GB), $_.Name }
$totalBytes = ($videos | Measure-Object -Property Length -Sum).Sum
Write-Host ("Found: {0}/7 files; total: {1:N2} GiB" -f $videos.Count, ($totalBytes/1GB)) -ForegroundColor Yellow
if ($videos.Count -ne 7) { Write-Warning '文件数量不是 7，请确认完整文件清单。诊断可以继续，但不要直接执行正式上传。' }

Section '2. SSH 连接配置（不会显示密钥和 ProxyCommand 内容）'
$config = @(& ssh -G $SshAlias 2>$null)
foreach ($key in @('hostname','port','proxyjump','compression','addressfamily')) {
    $line = $config | Where-Object { $_ -match "^$key\s" } | Select-Object -First 1
    if ($line) { Write-Host $line }
}
$proxyCommand = $config | Where-Object { $_ -match '^proxycommand\s' } | Select-Object -First 1
Write-Host ('proxycommand configured: ' + [string]($null -ne $proxyCommand -and $proxyCommand -notmatch '^proxycommand none$'))

Section '3. 远端信息（只读 + 创建独立工作目录）'
$remoteInfo = 'printf "USER=%s HOME=%s\n" "$USER" "$HOME"; uname -srm; df -h "$HOME"; for x in frpc frps python3 ffmpeg; do command -v "$x" || true; done; mkdir -p "$HOME/qingqingwu/work"'
& ssh -T -o BatchMode=yes -o ConnectTimeout=15 $SshAlias $remoteInfo
if ($LASTEXITCODE -ne 0) { throw 'SSH 远程命令失败。请先确认 ssh 别名可以免交互登录；不要继续传输测试。' }

Section '4. 两种 SSH 文件协议上下行测速'
if ($ProbeMiB -lt 1 -or $ProbeMiB -gt 256) { throw 'ProbeMiB 需要在 1..256 范围内。' }
$tempFile = Join-Path ([IO.Path]::GetTempPath()) ("qqw-probe-{0}.bin" -f ([guid]::NewGuid().ToString('N')))
$remoteBase = "__qqw-probe-$([guid]::NewGuid().ToString('N'))"
try {
    $buf = [byte[]]::new(1048576)
    $stream = [System.IO.File]::Open($tempFile, [System.IO.FileMode]::CreateNew)
    try {
        for ($i=0; $i -lt $ProbeMiB; $i++) {
            [System.Security.Cryptography.RandomNumberGenerator]::Fill($buf)
            $stream.Write($buf, 0, $buf.Length)
        }
    } finally { $stream.Dispose() }
    Write-Host "准备了 $ProbeMiB MiB 不可压缩随机样本，限时 ${TimeoutSeconds}s/模式。"
    $results = @()
    $r1 = Invoke-ScpProbe $tempFile "${remoteBase}-sftp.bin" $false
    $results += $r1
    $r2 = Invoke-ScpProbe $tempFile "${remoteBase}-legacy.bin" $true
    $results += $r2
    if ($r1.Result -eq '成功') {
        $dl1 = "$tempFile.download-sftp"
        $results += Invoke-ScpProbe $dl1 "${remoteBase}-sftp.bin" $false $true
        Remove-Item -LiteralPath $dl1 -Force -ErrorAction SilentlyContinue
    }
    if ($r2.Result -eq '成功') {
        $dl2 = "$tempFile.download-legacy"
        $results += Invoke-ScpProbe $dl2 "${remoteBase}-legacy.bin" $true $true
        Remove-Item -LiteralPath $dl2 -Force -ErrorAction SilentlyContinue
    }
    $results | Format-Table Mode,Result,Seconds,MiBs -AutoSize | Out-String | Write-Host
    foreach ($r in $results) { if ($r.Error) { Write-Warning ("$($r.Mode): $($r.Error)") } }
} finally {
    Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue
    $cleanup = "rm -f `"`$HOME/qingqingwu/work/${remoteBase}-sftp.bin`" `"`$HOME/qingqingwu/work/${remoteBase}-legacy.bin`""
    & ssh -T -o BatchMode=yes -o ConnectTimeout=15 $SshAlias $cleanup 2>$null | Out-Null
}
Section '5. 下一步'
Write-Host '请把上面的输出发给 ChatGPT（可隐藏 hostname/IP）。'
Write-Host '重点看：SSH 是否通过代理、SFTP 和 legacy SCP 上下行分别多少 MiB/s、服务器可用空间。'
Write-Host '如果两种方式都很慢，就改走现有 FRP + HTTPS/分块上传；不要直接拿 7 集试。' -ForegroundColor Green