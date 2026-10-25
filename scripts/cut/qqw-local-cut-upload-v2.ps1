#requires -Version 7.0
<#
《坑王驾到》第一季 S01 通用无重编码裁剪与 SSH 上传（延续 v2 白帧逻辑）
运行：pwsh -NoProfile -ExecutionPolicy Bypass -File .\qqw-local-cut-upload.ps1
试运行（只裁剪不上传）：... -Mode CutOnly -OnlyEpisode 49 -ReuseWhiteCache
只上传已裁剪文件：... -Mode UploadOnly
取回字幕：... -Mode FetchSubtitles
注意：失败或裁剪边界不可靠的文件不会被上传。
#>
[CmdletBinding()]
param(
    [ValidateSet('CutUpload','CutOnly','UploadOnly','FetchSubtitles')]
    [string]$Mode = 'CutUpload',
    [string]$SourceDir = 'D:\K坑王驾到S1',
    [string]$RootDir = 'D:\qingqingwu',
    [string]$SshAlias = 'TodayEatUbuntu-7K62',
    [double]$OpeningSeconds = 30.0,
    [double]$TailSafetySeconds = 0.25,
    [switch]$ReuseWhiteCache,
    [int]$OnlyEpisode = 0,
    [switch]$ForceRebuild
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$script:Culture = [Globalization.CultureInfo]::InvariantCulture
$script:VideoDir = Join-Path $RootDir 'videos'
$script:WorkDir = Join-Path $RootDir 'work'
$script:SubtitleDir = Join-Path $RootDir 'subtitles'
$script:ManifestDir = Join-Path $RootDir 'manifests'
$script:Report = Join-Path $RootDir 'processing-report.json'
foreach ($dir in @($RootDir, $VideoDir, $WorkDir, $SubtitleDir, $ManifestDir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}
function Fmt([double]$v) { return $v.ToString('0.000000', $script:Culture) }
function Need([string]$name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "缺少 $name。请先安装 FFmpeg（需 ffmpeg、ffprobe）及 Windows OpenSSH。"
    }
}
function Probe([string]$path) {
    $raw = & ffprobe -v error -show_format -show_streams -of json -- $path 2>&1
    if ($LASTEXITCODE -ne 0) { throw "FFprobe 失败: $path`n$($raw -join [Environment]::NewLine)" }
    return ($raw -join "`n" | ConvertFrom-Json)
}
function Duration([object]$info) {
    $d = 0.0
    if ($null -ne $info.format.duration) { $d = [double]::Parse([string]$info.format.duration, $script:Culture) }
    if ($d -le 0) { throw '视频没有可用的 duration 信息' }
    return $d
}
function NextKeyframes([string]$path, [double]$after) {
    # 从目标前约 5 秒开始读；-read_intervals 可能回退关键帧，需自行筛掉早于 after 的帧。
    $from = [Math]::Max(0, $after - 5)
    $interval = "$(Fmt $from)%+40"
    $raw = & ffprobe -v error -skip_frame nokey -select_streams v:0 -read_intervals $interval -show_frames -show_entries 'frame=key_frame,best_effort_timestamp_time' -of json -- $path 2>&1
    if ($LASTEXITCODE -ne 0) { throw "扫描关键帧失败: $($raw -join ' ')" }
    $frames = @((($raw -join "`n") | ConvertFrom-Json).frames)
    $result = @()
    foreach ($f in $frames) {
        if (($f.key_frame -eq 1) -and $null -ne $f.best_effort_timestamp_time) {
            $t = [double]::Parse([string]$f.best_effort_timestamp_time, $script:Culture)
            if ($t -ge $after - 0.000001) { $result += $t }
        }
    }
    return @($result | Sort-Object -Unique)
}
function FrameHash([string]$path, [double]$seek = -1) {
    # 通过解码帧 MD5 比较源文件候选关键帧和裁剪成品的第一帧（非容器哈希）。
    $args = @('-hide_banner','-v','error','-nostdin')
    if ($seek -ge 0) { $args += @('-ss',(Fmt $seek)) }
    $args += @('-i',$path,'-map','0:v:0','-frames:v','1','-f','framemd5','-')
    $lines = & ffmpeg @args 2>&1
    if ($LASTEXITCODE -ne 0) { throw "计算首帧 MD5 失败: $($lines -join ' ')" }
    $record = @($lines | Where-Object { $_ -match '^0,' } | Select-Object -Last 1)
    if ($record.Count -eq 0) { throw "没有成功解码首帧: $path" }
    $hash = [string](($record[-1] -split ',')[-1]).Trim().ToLowerInvariant()
    if ($hash -notmatch '^[0-9a-f]{32}$') { throw "帧 MD5 格式无效: $hash" }
    return $hash
}
function ScanWhite([string]$path, [double]$scanFrom, [string]$statsName) {
    # signalstats 先缩小分辨率，逐帧统计接近纯白程度。
    # 该滤镜对有限范围 YUV 白色(Y~235)和全范围白色(Y~255)均可识别。
    $statsPath = Join-Path $script:WorkDir $statsName
    Remove-Item -LiteralPath $statsPath -ErrorAction SilentlyContinue
    Push-Location $script:WorkDir
    try {
        $filter = "scale=160:-2:flags=area,signalstats,metadata=mode=print:file=$statsName"
        $msg = & ffmpeg -hide_banner -v error -nostdin -ss (Fmt $scanFrom) -i $path -map '0:v:0' -an -sn -vf $filter -f null - 2>&1
        if ($LASTEXITCODE -ne 0) { throw "逐帧白色检测失败: $($msg -join ' ')" }
    } finally { Pop-Location }
    if (-not (Test-Path -LiteralPath $statsPath)) { throw "FFmpeg 未生成帧统计: $statsPath" }
    $candidate = New-Object 'System.Collections.Generic.List[double]'
    $frameTime = $null
    $yMin = 0.0; $yLow = 0.0; $yAvg = 0.0; $yHigh = 0.0; $uAvg = 0.0; $vAvg = 0.0
    $seen = New-Object 'System.Collections.Generic.HashSet[string]'
    function FinishFrame {
        if (($null -ne $frameTime) -and ($seen.Count -ge 6)) {
            if (($yAvg -ge 231) -and ($yLow -ge 225) -and ($yMin -ge 185) -and
                (($yHigh - $yLow) -le 12) -and
                ([Math]::Abs($uAvg - 128) -le 8) -and ([Math]::Abs($vAvg - 128) -le 8)) {
                $candidate.Add([double]$frameTime)
            }
        }
    }
    foreach ($line in [System.IO.File]::ReadLines($statsPath)) {
        if ($line -match '^frame:\s*\d+\s+pts:\s*\S+\s+pts_time:\s*([-\d.]+)') {
            FinishFrame
            $frameTime = $scanFrom + [double]::Parse($Matches[1], $script:Culture)
            $seen.Clear()
            $yMin = 0; $yLow = 0; $yAvg = 0; $yHigh = 0; $uAvg = 0; $vAvg = 0
        } elseif ($line -match '^lavfi\.signalstats\.(YMIN|YLOW|YAVG|YHIGH|UAVG|VAVG)=([\d.]+)') {
            $key = $Matches[1]
            $val = [double]::Parse($Matches[2], $script:Culture)
            [void]$seen.Add($key)
            switch ($key) {
                'YMIN'  { $yMin  = $val }
                'YLOW'  { $yLow  = $val }
                'YAVG'  { $yAvg  = $val }
                'YHIGH' { $yHigh = $val }
                'UAVG'  { $uAvg  = $val }
                'VAVG'  { $vAvg  = $val }
            }
        }
    }
    FinishFrame
    return @($candidate.ToArray())
}
function SaveWhitePreviews([string]$path, [int]$episode, [double]$first, [double]$second) {
    # 两处白帧间隔固定时，保存候选画面，便于人工确认统一片尾模板。
    $samples = @(
        [pscustomobject]@{ Label = 'before'; Seconds = [Math]::Max(0, $first - 0.5) }
        [pscustomobject]@{ Label = 'first';  Seconds = $first }
        [pscustomobject]@{ Label = 'second'; Seconds = $second }
    )
    foreach ($item in $samples) {
        $imagePath = Join-Path $script:WorkDir "white-e$episode-$($item.Label).jpg"
        $msg = & ffmpeg -hide_banner -v error -nostdin -y -ss (Fmt $item.Seconds) -i $path -map '0:v:0' -frames:v 1 -vf 'scale=960:-2' -q:v 3 $imagePath 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "  无法生成白帧预览 $($item.Label)：$($msg -join ' ')"
        }
    }
}
function GetWhiteTime([string]$path, [double]$duration, [int]$episode) {
    # 上一轮已经扫描成功但误因多组白帧中断时，可以复用相同视频的 JSON，省去重复解码。
    $cachePath = Join-Path $script:WorkDir "white-e$episode.json"
    if ($ReuseWhiteCache -and (Test-Path -LiteralPath $cachePath)) {
        try {
            $sourceItem = Get-Item -LiteralPath $path
            $cacheItem = Get-Item -LiteralPath $cachePath
            $cached = Get-Content -LiteralPath $cachePath -Raw -Encoding utf8 | ConvertFrom-Json
            $cachedFirst = [double]$cached.First
            if (($cacheItem.LastWriteTimeUtc -ge $sourceItem.LastWriteTimeUtc) -and
                ($cachedFirst -gt 30) -and ($cachedFirst -lt $duration) -and
                ($cachedFirst -ge [Math]::Max(0, $duration - 600))) {
                Write-Host "  复用上次已扫描的白帧结果：$(Fmt $cachedFirst) 秒（源文件修改时间未变）" -ForegroundColor Green
                return $cachedFirst
            }
            Write-Warning "  E$episode 白帧缓存时间或范围异常，重新扫描。"
        } catch {
            Write-Warning "  E$episode 无法读取白帧缓存，重新扫描：$($_.Exception.Message)"
        }
    }
    foreach ($window in @(180,300,600)) {
        $begin = [Math]::Max(0, $duration - $window)
        Write-Host "  扫描片尾最近 $window 秒..."
        $found = @(ScanWhite $path $begin "white-e$episode.txt" | Sort-Object)
        if ($found.Count -gt 0) {
            # 用户明确要求：从片尾第一次出现纯白画面的地方切掉后续内容。
            # 一集存在多次白帧并不表示检测错误；该系列 7 集均检测到相隔 4.84 秒的两次白帧。
            $groups = @()
            $groupStart = [double]$found[0]
            $groupLast = $groupStart
            $count = 1
            for ($i = 1; $i -lt $found.Count; $i++) {
                $t = [double]$found[$i]
                if (($t - $groupLast) -gt 0.6) {
                    $groups += [pscustomobject]@{ Start = $groupStart; Last = $groupLast; Frames = $count }
                    $groupStart = $t
                    $count = 0
                }
                $groupLast = $t
                $count++
            }
            $groups += [pscustomobject]@{ Start = $groupStart; Last = $groupLast; Frames = $count }
            $selected = [double]$groups[0].Start
            $groupStarts = @($groups | ForEach-Object { [double]$_.Start })
            [pscustomobject]@{
                SelectionRule = 'first-white-frame-before-ending'
                First = $selected
                GroupStarts = $groupStarts
                Groups = @($groups)
                ScanBegin = $begin
            } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $script:WorkDir "white-e$episode.json") -Encoding utf8
            if ($groups.Count -gt 1) {
                $later = [double]$groups[1].Start
                $gap = $later - $selected
                Write-Host "  检测到 $($groups.Count) 组白帧，首组 $(Fmt $selected) 秒；第二组 $(Fmt $later) 秒，间隔 $(Fmt $gap) 秒。" -ForegroundColor Yellow
                Write-Host '  按要求选择最早白帧作为片尾边界（后面的白帧属于待裁掉区域）。' -ForegroundColor Yellow
                SaveWhitePreviews $path $episode $selected $later
            }
            return $selected
        }
    }
    throw "E$episode 最后 10 分钟没有检测到可靠的纯白帧，不自动猜测结束时间。"
}
function CreateCut([string]$src, [string]$out, [double]$start, [double]$whiteAt, [double]$extraSafety) {
    $duration = $whiteAt - $start - $extraSafety
    if ($duration -lt 10) { throw '裁剪持续时间异常' }
    $tmp = Join-Path $script:WorkDir ([IO.Path]::GetFileNameWithoutExtension($out) + '.partial.mp4')
    Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
    # 在关键帧后数毫秒寻址，FFmpeg 按最近的前一个 seek point 开始复制。
    # 是否真从所选关键帧开始由后续逐帧哈希比对负责验证。
    $seekAt = $start + 0.002
    $args = @('-hide_banner','-v','error','-nostdin','-y',
      '-ss',(Fmt $seekAt),'-i',$src,'-t',(Fmt $duration),
      '-map','0','-c','copy','-avoid_negative_ts','make_zero',$tmp)
    $msg = & ffmpeg @args 2>&1
    if ($LASTEXITCODE -ne 0) { throw "无损裁剪 FFmpeg 失败：$($msg -join ' ')" }
    if (-not (Test-Path -LiteralPath $tmp) -or (Get-Item -LiteralPath $tmp).Length -lt 10240) { throw '裁剪成品不存在或体积异常' }
    $sourceHash = FrameHash $src $start
    $outputHash = FrameHash $tmp
    if ($sourceHash -ne $outputHash) {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        throw "首帧校验不一致。原始关键帧 $start 秒与成品首帧不一致，停止以防残留片头。"
    }
    $outInfo = Probe $tmp
    $outDuration = Duration $outInfo
    $from = [Math]::Max(0, $outDuration - 4)
    $whites = @(ScanWhite $tmp $from 'white-output-check.txt')
    if ($whites.Count -gt 0) {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        throw "输出文件尾部仍含疑似纯白帧（$($whites[0]) 秒），停止上传。可适当增加 -TailSafetySeconds 再运行。"
    }
    if ($outDuration -gt $duration + 1) {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        throw "成品比预期长出超过 1 秒：实际 $outDuration，预期 $duration，请检查时间戳。"
    }
    $inInfo = Probe $src
    $v1 = @($inInfo.streams | Where-Object { $_.codec_type -eq 'video' } | Select-Object -First 1)[0]
    $v2 = @($outInfo.streams | Where-Object { $_.codec_type -eq 'video' } | Select-Object -First 1)[0]
    if ($v1.codec_name -ne $v2.codec_name -or $v1.width -ne $v2.width -or $v1.height -ne $v2.height) { throw '视频编码或尺寸有变化，未通过 stream-copy 验证' }
    $a1 = @($inInfo.streams | Where-Object { $_.codec_type -eq 'audio' })
    $a2 = @($outInfo.streams | Where-Object { $_.codec_type -eq 'audio' })
    if ($a1.Count -ne $a2.Count) { throw '音频轨数与源文件不一致' }
    for ($j = 0; $j -lt $a1.Count; $j++) {
        if ($a1[$j].codec_name -ne $a2[$j].codec_name) { throw '音频编码变化' }
    }
    Move-Item -LiteralPath $tmp -Destination $out -Force
    return $outDuration
}
function RemoteSize([string]$remote) {
    # 仅由脚本生成安全的 ASCII 名称；无须 shell 转义中文视频文件名。
    $value = & ssh -o BatchMode=yes -o ConnectTimeout=15 $SshAlias "stat -c %s '$remote' 2>/dev/null || true" 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'SSH 检查远端文件大小失败' }
    $line = @($value | Where-Object { $_ -match '^\d+$' } | Select-Object -Last 1)
    if ($line.Count -eq 0) { return -1L }
    return [long]::Parse($line[-1])
}
function Upload([string]$local, [int]$ep) {
    $id = 'S01E{0:d2}' -f $ep
    $remote = "qingqingwu/input/$id.mp4"
    $size = (Get-Item -LiteralPath $local).Length
    $existing = RemoteSize $remote
    if ($existing -eq $size) { Write-Host "  服务器已有同体积文件，跳过 E$ep 上传" -ForegroundColor DarkGreen; return }
    $part = "qingqingwu/input/$id.mp4.uploading"
    Write-Host "  上传 E$ep / $([Math]::Round($size / 1GB,2)) GiB（SCP -O）..." -ForegroundColor Cyan
    & scp.exe -O -o Compression=no -o ServerAliveInterval=30 -o ServerAliveCountMax=6 -- $local "${SshAlias}:$part"
    if ($LASTEXITCODE -ne 0) { throw "E$ep 上传失败。SCP 不支持可靠断点续传，该集可重跑。" }
    $uploaded = RemoteSize $part
    if ($uploaded -ne $size) { throw "E$ep 上传大小校验失败！本地 $size 远端 $uploaded" }
    & ssh -o BatchMode=yes $SshAlias "test ! -e '$remote' && mv '$part' '$remote'"
    if ($LASTEXITCODE -ne 0) { throw "E$ep 临时文件重命名失败" }
    Write-Host "  E$ep 上传完成" -ForegroundColor Green
}
function GetFiles {
    if (-not (Test-Path -LiteralPath $SourceDir)) { throw "源视频目录不存在：$SourceDir" }
    $selected = @(Get-ChildItem -LiteralPath $SourceDir -File -Filter '*.mp4' | Where-Object { $_.Name -match '^坑王驾到S01E\d{2,3}\.' } | Sort-Object Name)
    if ($selected.Count -eq 0) { throw "未找到《坑王驾到》第一季 S01 源视频。" }
    return $selected
}

if ($Mode -in @('CutUpload','CutOnly')) { Need 'ffmpeg'; Need 'ffprobe' }
if ($Mode -in @('CutUpload','UploadOnly','FetchSubtitles')) { Need 'ssh'; Need 'scp.exe' }
$all = @(GetFiles)
if ($OnlyEpisode -ne 0) {
    if ($OnlyEpisode -lt 1 -or $OnlyEpisode -gt 999) { throw '-OnlyEpisode 必须为 1～999，0 表示自动扫描所有第一季素材。' }
    $all = @($all | Where-Object { [int]([regex]::Match($_.Name, '^坑王驾到S01E(\d{2,3})\.')).Groups[1].Value -eq $OnlyEpisode })
    if ($all.Count -ne 1) { throw "找不到 E$OnlyEpisode 的唯一源视频。" }
}
Write-Host "来源: $SourceDir"
Write-Host "输出: $RootDir"
Write-Host "模式: $Mode | 第一季已发现 $($all.Count) 回" -ForegroundColor Cyan

if ($Mode -eq 'FetchSubtitles') {
    throw '请使用 scripts/qqw.ps1 -Command fetch-srt（需审校完成哈希标记），禁止旧脚本直接下载未完成字幕。'
    foreach ($file in $all) {
        $ep = [int]([regex]::Match($file.Name, '^坑王驾到S01E(\d+)')).Groups[1].Value
        $local = Join-Path $script:SubtitleDir ($file.BaseName + '.zh.srt')
        $id = 'S01E{0:d2}' -f $ep
        $remote = "${SshAlias}:qingqingwu/output/$id.zh.srt"
        & scp.exe -O -o Compression=no $remote $local
        if ($LASTEXITCODE -ne 0) { Write-Warning "E$ep 字幕尚未生成或下载失败" }
        else { Write-Host "已下载：$local" -ForegroundColor Green }
    }
    return
}
if ($Mode -in @('CutUpload','UploadOnly')) {
    & ssh -o BatchMode=yes -o ConnectTimeout=20 $SshAlias 'mkdir -p ~/qingqingwu/input ~/qingqingwu/output'
    if ($LASTEXITCODE -ne 0) { throw "无法通过 $SshAlias 连接或创建远端目录" }
}
$reports = @()
foreach ($file in $all) {
    $ep = [int]([regex]::Match($file.Name, '^坑王驾到S01E(\d+)')).Groups[1].Value
    $out = Join-Path $script:VideoDir $file.Name
    $id = 'S01E{0:d2}' -f $ep
    $manifest = Join-Path $script:ManifestDir "$id.json"
    Write-Host "`n========== E$ep ==========" -ForegroundColor Yellow
    try {
        if ($Mode -ne 'UploadOnly' -and ($ForceRebuild -or -not (Test-Path -LiteralPath $out) -or -not (Test-Path -LiteralPath $manifest))) {
            $info = Probe $file.FullName
            $total = Duration $info
            Write-Host "  源视频时长 $(Fmt $total) 秒"
            $white = GetWhiteTime $file.FullName $total $ep
            Write-Host "  第一纯白帧：$(Fmt $white) 秒"
            $keys = @(NextKeyframes $file.FullName $OpeningSeconds)
            if ($keys.Count -eq 0) { throw '片头 30 秒后未找到有效关键帧' }
            $start = [double]$keys[0]
            if ($start -ge $white - 30) { throw '关键帧与片尾边界冲突' }
            Write-Host "  起始关键帧：$(Fmt $start) 秒，额外删除 $(Fmt ($start-$OpeningSeconds)) 秒"
            $copyDuration = CreateCut $file.FullName $out $start $white $TailSafetySeconds
            $meta = [ordered]@{
                Episode = $id
                InputName = $file.Name
                OutputName = $file.Name
                SourceDurationSeconds = $total
                RequestedOpeningSeconds = $OpeningSeconds
                ChosenStartKeyframeSeconds = $start
                ExtraOpeningRemovedSeconds = $start - $OpeningSeconds
                FirstWhiteFrameSeconds = $white
                TailSafetySeconds = $TailSafetySeconds
                CopyTargetSeconds = $white - $start - $TailSafetySeconds
                OutputDurationSeconds = $copyDuration
                VideoCodecCopied = $true
                AudioCodecCopied = $true
                GeneratedAt = (Get-Date).ToString('o')
            }
            $meta | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifest -Encoding utf8
            Write-Host "  已通过首帧、尾部白帧、编码格式校验：$out" -ForegroundColor Green
        } else {
            if (-not (Test-Path -LiteralPath $out) -or -not (Test-Path -LiteralPath $manifest)) { throw '缺少已裁剪视频或清单，请先用 CutOnly/CutUpload 完成裁剪' }
            Write-Host '  使用之前已通过验证的本地裁剪结果'
        }
        if ($Mode -in @('CutUpload','UploadOnly')) { Upload $out $ep }
        $reports += [pscustomobject]@{ Episode = $id; Status = 'OK'; Local = $out; Remote = "~/qingqingwu/input/$id.mp4" }
    } catch {
        Write-Warning "E$ep 处理失败：$($_.Exception.Message)"
        $reports += [pscustomobject]@{ Episode = $id; Status = 'FAILED'; Error = $_.Exception.Message }
    }
    $reports | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $script:Report -Encoding utf8
}
$failed = @($reports | Where-Object { $_.Status -ne 'OK' })
Write-Host "`n执行完成：$($reports.Count - $failed.Count)/$($reports.Count) 成功。详细结果：$script:Report"
if ($failed.Count -gt 0) { throw "$($failed.Count) 回未通过校验或上传失败；请查看报告。" }
if ($Mode -in @('CutUpload','UploadOnly')) {
    Write-Host '服务器视频位置：~/qingqingwu/input/S01E*.mp4' -ForegroundColor Green
    Write-Host 'Codex 无需裁剪，直接以这些成品视频作为 OCR 输入。' -ForegroundColor Green
}