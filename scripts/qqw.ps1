#requires -Version 7.0
<#
Windows controller. No destructive operation or remote job start occurs automatically.
Run from repository: pwsh ./scripts/qqw.ps1 -Command status
#>
[CmdletBinding()]
param(
 [ValidateSet('adopt','status','doctor','cut','upload-cut','fetch-srt','send-master','sync-style','submit','fetch-final','remote-status')]
 [string]$Command='status',
 [int]$Episode=0,
 [string]$RootDir='D:\qingqingwu',
 [string]$SourceDir='D:\K坑王驾到S1',
 [string]$SshAlias='TodayEatUbuntu-7K62',
 [string]$MasterPath=''
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$repo=Split-Path -Parent $PSScriptRoot
function AssertExit([string]$operation) {
 if($LASTEXITCODE -ne 0){ throw "$operation failed ($LASTEXITCODE)" }
}
function Ep([int]$n){
 if($n -lt 49 -or $n -gt 55){throw 'Allowed episodes: 49..55'}
 return ('S01E{0:d2}' -f $n)
}
function RemoteHash([string]$p) {
 if($p -notmatch '^[A-Za-z0-9_./-]+$'){ throw "Unsafe remote path: $p" }
 $v=@(& ssh.exe -o BatchMode=yes $SshAlias "sha256sum '$p' 2>/dev/null || true")
 AssertExit 'remote hash'
 $line=@($v | Where-Object {$_ -match '^[0-9a-fA-F]{64}\s+'} | Select-Object -First 1)
 if($line.Count -eq 0){return ''}
 return ([regex]::Match($line[0],'^[0-9a-fA-F]{64}').Value.ToLowerInvariant())
}
function Upload([string]$from,[string]$to){
 if(-not(Test-Path -LiteralPath $from -PathType Leaf)){throw "Missing: $from"}
 $hash=(Get-FileHash -Algorithm SHA256 -LiteralPath $from).Hash.ToLowerInvariant()
 $old=RemoteHash $to
 if($old -eq $hash){Write-Host "Already uploaded $to";return}
 if($old){throw "Remote file differs, refusing overwrite: $to"}
 $parent=$to.Substring(0,$to.LastIndexOf('/'))
 & ssh.exe -o BatchMode=yes $SshAlias "mkdir -p '$parent'"; AssertExit 'mkdir'
 $partial="$to.uploading"
 $address=$SshAlias+':'+$partial
 & scp.exe -O -o Compression=no -- $from $address; AssertExit 'scp upload'
 if((RemoteHash $partial) -ne $hash){throw "Upload SHA mismatch: $to"}
 & ssh.exe -o BatchMode=yes $SshAlias "test ! -e '$to' && mv '$partial' '$to'"; AssertExit 'publish'
 Write-Host "Verified upload $to" -ForegroundColor Green
}
function Download([string]$from,[string]$to){
 $hash=RemoteHash $from
 if(-not $hash){Write-Warning "Not ready: $from";return}
 New-Item -ItemType Directory -Force (Split-Path -Parent $to) | Out-Null
 if(Test-Path -LiteralPath $to){
  if((Get-FileHash -Algorithm SHA256 -LiteralPath $to).Hash.ToLowerInvariant() -eq $hash){
   Write-Host "Already fetched: $to";return
  }
  throw "Local file differs; won't overwrite $to"
 }
 $part="$to.downloading"
 & scp.exe -O -o Compression=no -- ($SshAlias+':'+$from) $part; AssertExit 'scp download'
 if((Get-FileHash -Algorithm SHA256 -LiteralPath $part).Hash.ToLowerInvariant() -ne $hash){throw 'Download SHA mismatch'}
 Move-Item -LiteralPath $part -Destination $to
 Write-Host "Verified download $to" -ForegroundColor Green
}
if($Episode -ne 0){[void](Ep $Episode)}
if($Command -in @('cut','upload-cut')){
 $script=Join-Path $PSScriptRoot 'cut\qqw-local-cut-upload-v2.ps1'
 $mode=if($Command -eq 'cut'){'CutOnly'}else{'UploadOnly'}
 $extra=@()
 if($Episode){$extra=@('-OnlyEpisode',$Episode)}
 & pwsh -NoProfile -File $script -Mode $mode -SourceDir $SourceDir -RootDir $RootDir -SshAlias $SshAlias -ReuseWhiteCache @extra
 AssertExit 'trusted cutter'
 return
}
if($Command -in @('adopt','status','doctor')){
 & python (Join-Path $repo 'kwjd.py') $Command --role windows --root $RootDir
 AssertExit 'kwjd.py'
 return
}
if($Command -eq 'remote-status'){
 & ssh.exe -o BatchMode=yes $SshAlias 'python3 ~/qingqingwu/repo/kwjd.py status --role linux'
 AssertExit 'remote status'
 return
}
if($Command -eq 'sync-style'){
 $folder=Join-Path $RootDir 'styles'
 if(-not(Test-Path (Join-Path $folder 'template.ass'))){throw 'Aegisub template missing'}
 & ssh.exe -o BatchMode=yes $SshAlias 'mkdir -p ~/qingqingwu/post'
 AssertExit 'style mkdir'
 & scp.exe -O -r -o Compression=no -- $folder ($SshAlias+':qingqingwu/post/')
 AssertExit 'style sync'
 Write-Host 'Style copied. Verify libass selected expected font before production.'
 return
}
$numbers=if($Episode){@($Episode)}else{@(49..55)}
foreach($n in $numbers){
 $id=Ep $n
 switch($Command){
  'fetch-srt' {
   $srtRemote="qingqingwu/output/$id.zh.srt"
   $sealRemote="$srtRemote.ready.json"
   if(-not (RemoteHash $sealRemote)){
    Write-Warning "$id OCR review is not sealed yet; skip download"
    break
   }
   $srtLocal=Join-Path $RootDir "subtitles\$id.zh.srt"
   $sealLocal="$srtLocal.ready.json"
   Download $srtRemote $srtLocal
   Download $sealRemote $sealLocal
   $seal=Get-Content -LiteralPath $sealLocal -Raw -Encoding UTF8|ConvertFrom-Json
   if(-not $seal.reviewed){throw "Unreviewed OCR seal: $id"}
   $digest=(Get-FileHash -LiteralPath $srtLocal -Algorithm SHA256).Hash.ToLowerInvariant()
   if($digest -ne $seal.sha256){throw "OCR seal hash mismatch: $id"}
   Write-Host "$id reviewed SRT verified" -ForegroundColor Green
  }
  'send-master' {
   if($MasterPath){
    if($numbers.Count -ne 1){throw 'MasterPath requires Episode'}
    $master=$MasterPath
   }else{
    $matches=@(Get-ChildItem -LiteralPath (Join-Path $RootDir 'masters') -File -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match ("^$id[.]clean-master[.](mp4|mkv|mov)$") })
    if($matches.Count -ne 1){throw "Expected master: $id.clean-master.(mp4|mkv|mov)"}
    $master=$matches[0].FullName
   }
   $name=[IO.Path]::GetFileName($master)
   if($name -notmatch ("^$id[.]clean-master[.](mp4|mkv|mov)$")){throw "Invalid name: $name"}
   $a=(Get-Item -LiteralPath $master).Length
   Start-Sleep -Seconds 4
   $b=(Get-Item -LiteralPath $master).Length
   if($a -ne $b -or $a -lt 1MB){throw "Master not finalized: $master"}
   Upload $master "qingqingwu/post/incoming/$name"
  }
  'fetch-final' {
   Download "qingqingwu/post/final/$id.final-1080p60.mkv" (Join-Path $RootDir "final\$id.final-1080p60.mkv")
  }
  'submit' {
   $files=@(Get-ChildItem -LiteralPath (Join-Path $RootDir 'masters') -File -ErrorAction SilentlyContinue |
       Where-Object {$_.Name -match ("^$id[.]clean-master[.](mp4|mkv|mov)$")})
   if($files.Count -ne 1){throw "Master not found: $id"}
   $name=$files[0].Name
   if(-not(RemoteHash "qingqingwu/post/incoming/$name")){throw "Upload master first: $id"}
   $job=[ordered]@{
    schema_version=1;episode=$id;state='QUEUED'
    created_at=(Get-Date).ToUniversalTime().ToString('o')
    master="incoming/$name";srt="../output/$id.zh.srt"
    style='styles/template.ass';output="final/$id.final-1080p60.mkv"
    spec=@{width=1920;height=1080;fps=60;subtitle='burn-in-last'}
   }
   $tmp=Join-Path $env:TEMP "$id.kwjd-job.json"
   try {
    $job|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $tmp -Encoding utf8
    Upload $tmp "qingqingwu/post/jobs/$id.json"
   }finally{Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue}
  }
 }
}
