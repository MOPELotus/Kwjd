#requires -Version 7.0
<#
Windows controller. Verified upload includes explicitly authorized local cleanup.
Run from repository: pwsh ./scripts/qqw.ps1 -Command status
#>
[CmdletBinding()]
param(
 [ValidateSet('adopt','status','doctor','cut','upload-cut','fetch-srt','send-master','confirm-upload','sync-style','submit','fetch-final','remote-status','complete-episode','cleanup-cache')]
 [string]$Command='status',
 [int]$Episode=0,
 [string]$RootDir='D:\qingqingwu',
 [string]$SourceDir='D:\K坑王驾到S1',
 [string]$SshAlias='TodayEatUbuntu-7K62',
 [string]$MasterPath='',
 [string]$QualityNote=''
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$repo=Split-Path -Parent $PSScriptRoot
$project=Get-Content -LiteralPath (Join-Path $repo 'config/project.json') -Raw -Encoding utf8 | ConvertFrom-Json
$season=[string]$project.season
function AssertExit([string]$operation) {
 if($LASTEXITCODE -ne 0){ throw "$operation failed ($LASTEXITCODE)" }
}
function Ep([int]$n){
 if($n -lt 1 -or $n -gt 999){throw 'Episode must be 1..999'}
 return ('{0}E{1:d2}' -f $season,$n)
}
function DiscoverEpisodes {
 $candidates=New-Object 'System.Collections.Generic.HashSet[int]'
 # Existing 49-55 are bootstrap hints, not an exclusive episode range.
 foreach($n in $project.bootstrap_episodes){ [void]$candidates.Add([int]$n) }
 foreach($folder in @('videos','masters','subtitles','final')){
  $path=Join-Path $RootDir $folder
  if(Test-Path -LiteralPath $path -PathType Container){
   foreach($f in (Get-ChildItem -LiteralPath $path -File)){
    if($f.Name -match ("(?i)" + [regex]::Escape($season) + "E(\d{2,3})(?!\d)")){
     [void]$candidates.Add([int]$Matches[1])
    }
   }
  }
 }
 return @($candidates|Sort-Object)
}
function RemoteHash([string]$p) {
 if($p -notmatch '^[A-Za-z0-9_./-]+$'){ throw "Unsafe remote path: $p" }
 $v=@(& ssh.exe -o BatchMode=yes $SshAlias "sha256sum '$p' 2>/dev/null || true")
 AssertExit 'remote hash'
 $line=@($v | Where-Object {$_ -match '^[0-9a-fA-F]{64}\s+'} | Select-Object -First 1)
 if($line.Count -eq 0){return ''}
 return ([regex]::Match($line[0],'^[0-9a-fA-F]{64}').Value.ToLowerInvariant())
}
function Upload([string]$from,[string]$to,[switch]$ReplaceStyle){
 if(-not(Test-Path -LiteralPath $from -PathType Leaf)){throw "Missing: $from"}
 $hash=(Get-FileHash -Algorithm SHA256 -LiteralPath $from).Hash.ToLowerInvariant()
 $old=RemoteHash $to
 if($old -eq $hash){Write-Host "Already uploaded $to";return $hash}
 if($ReplaceStyle -and $to -notmatch '^qingqingwu/post/styles/(template[.]ass([.]ready[.]json)?|fonts/[A-Za-z0-9_.-]+[.](ttf|otf|ttc))$'){throw 'Replacement is limited to current style assets'}
 if($old -and -not $ReplaceStyle){throw "Remote file differs, refusing overwrite: $to"}
 $parent=$to.Substring(0,$to.LastIndexOf('/'))
 & ssh.exe -o BatchMode=yes $SshAlias "mkdir -p '$parent'"; AssertExit 'mkdir'
 $partial="$to.uploading"
 $address=$SshAlias+':'+$partial
 & scp.exe -O -o Compression=no -- $from $address; AssertExit 'scp upload'
 if((RemoteHash $partial) -ne $hash){throw "Upload SHA mismatch: $to"}
 if($old -and $ReplaceStyle){
  # Compare-and-replace: never replace a concurrently changed style asset.
  & ssh.exe -o BatchMode=yes $SshAlias "test `"`$(sha256sum '$to' | cut -d ' ' -f 1)`" = '$old' && mv '$partial' '$to'"
 }else{
  & ssh.exe -o BatchMode=yes $SshAlias "test ! -e '$to' && mv '$partial' '$to'"
 }
 AssertExit 'publish'
 Write-Host "Verified upload $to" -ForegroundColor Green
 return $hash
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
function ConfirmUpload([string]$id){
 $record=Get-Content -LiteralPath (Join-Path $RootDir ".kwjd/uploads/$id.json") -Raw -Encoding utf8|ConvertFrom-Json
 if($record.episode -ne $id -or -not $record.verified -or -not $record.local_master_deleted){throw 'Local master release must be verified first'}
 if((RemoteHash $record.remote_path) -ne $record.sha256){throw 'Remote master changed before release handoff'}
 $confirmation=[ordered]@{schema_version=1;episode=$id;sha256=$record.sha256;verified=$true;local_master_deleted=$true;local_master_deleted_at=$record.local_master_deleted_at}
 $folder=Join-Path $RootDir '.kwjd/handoffs'
 New-Item -ItemType Directory -Force $folder|Out-Null
 $path=Join-Path $folder "$id.upload.json"
 $confirmation|ConvertTo-Json|Set-Content -LiteralPath "$path.tmp" -Encoding utf8
 Move-Item -LiteralPath "$path.tmp" -Destination $path -Force
 Upload $path "qingqingwu/post/receipts/$id.upload.json" | Out-Null
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
 & ssh.exe -o BatchMode=yes $SshAlias 'mkdir -p ~/qingqingwu/post/styles/fonts'
 AssertExit 'style mkdir'
 Upload (Join-Path $folder 'template.ass') 'qingqingwu/post/styles/template.ass' -ReplaceStyle | Out-Null
 foreach($font in (Get-ChildItem -LiteralPath (Join-Path $folder 'fonts') -File)){
  if($font.Name -notmatch '^[A-Za-z0-9_.-]+[.](ttf|otf|ttc)$'){throw 'Font filename must be a safe ASCII transfer name'}
  Upload $font.FullName ('qingqingwu/post/styles/fonts/'+$font.Name) -ReplaceStyle | Out-Null
 }
 $styleSeal=Join-Path $folder 'template.ass.ready.json'
 if(Test-Path -LiteralPath $styleSeal){
  $approval=Get-Content -LiteralPath $styleSeal -Raw -Encoding utf8|ConvertFrom-Json
  if(-not $approval.approved -or $approval.sha256 -ne (Get-FileHash -LiteralPath (Join-Path $folder 'template.ass') -Algorithm SHA256).Hash.ToLowerInvariant()){throw 'Style approval is stale'}
  if(-not $approval.PSObject.Properties['fonts']){throw 'Seal style with seal_subtitle_style.py to bind actual font hashes'}
  foreach($fontEntry in $approval.fonts.PSObject.Properties){
   if($fontEntry.Name -notmatch '^[A-Za-z0-9_.-]+[.](ttf|otf|ttc)$'){throw 'Unsafe sealed font name'}
   if((Get-FileHash -LiteralPath (Join-Path $folder ('fonts/'+$fontEntry.Name)) -Algorithm SHA256).Hash.ToLowerInvariant() -ne $fontEntry.Value){throw 'Approved font has changed'}
  }
  Upload $styleSeal 'qingqingwu/post/styles/template.ass.ready.json' -ReplaceStyle | Out-Null
 }
 Write-Host 'Style copied. Verify libass selected expected font before production.'
 return
}
$numbers=@(if($Episode){$Episode}else{DiscoverEpisodes})
if($numbers.Count -eq 0){throw "No $season episodes discovered under $RootDir"}
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
   & python -X utf8 (Join-Path $PSScriptRoot 'episode_lifecycle.py') gate-next --root $RootDir --episode $id
   AssertExit 'previous local upload cleanup gate'
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
   $manifest=Join-Path $RootDir "manifests\$id.json"
   if(-not(Test-Path -LiteralPath $manifest)){throw 'Need the cut manifest to validate master duration'}
   $cut=Get-Content -LiteralPath $manifest -Raw -Encoding utf8|ConvertFrom-Json
   $validation=Join-Path $RootDir ".kwjd\master-validation\$id.json"
   & python -X utf8 (Join-Path $PSScriptRoot 'validate_master.py') --file $master --receipt $validation --expected-duration $cut.OutputDurationSeconds
   AssertExit 'master technical validation'
   $validated=Get-Content -LiteralPath $validation -Raw -Encoding utf8|ConvertFrom-Json
   $discarded=Join-Path $RootDir ('.kwjd\'+$id.ToLowerInvariant().Replace('s01','')+'-discarded-master.json')
   if(Test-Path -LiteralPath $discarded){
    $retired=Get-Content -LiteralPath $discarded -Raw -Encoding utf8|ConvertFrom-Json
    if($retired.sha256 -eq $validated.sha256){throw 'This is the retired master; wait for the new export'}
   }
   & python -X utf8 (Join-Path $PSScriptRoot 'resolve_cache.py') snapshot --root $RootDir --episode $id
   AssertExit 'CacheClip upload-start snapshot'
   $remote="qingqingwu/post/incoming/$name"
   $hash=Upload $master $remote
   if($hash -ne $validated.sha256){throw 'Master changed after validation'}
   $upload=Get-Content -LiteralPath (Join-Path $RootDir ".kwjd\cache-snapshots\$id.json") -Raw -Encoding utf8|ConvertFrom-Json
   $upload|Add-Member -NotePropertyName verified -NotePropertyValue $true -Force
   $upload|Add-Member -NotePropertyName sha256 -NotePropertyValue $hash -Force
   $upload|Add-Member -NotePropertyName local_path -NotePropertyValue $master -Force
   $upload|Add-Member -NotePropertyName remote_path -NotePropertyValue $remote -Force
   $upload|Add-Member -NotePropertyName ssh_alias -NotePropertyValue $SshAlias -Force
   $receiptDir=Join-Path $RootDir '.kwjd\uploads'
   New-Item -ItemType Directory -Force $receiptDir|Out-Null
   $receiptPath=Join-Path $receiptDir "$id.json"
   $upload|ConvertTo-Json -Depth 10|Set-Content -LiteralPath "$receiptPath.tmp" -Encoding utf8
   Move-Item -LiteralPath "$receiptPath.tmp" -Destination $receiptPath -Force
   & python -X utf8 (Join-Path $PSScriptRoot 'episode_lifecycle.py') register --root $RootDir --episode $id --artifact "masters/$name"
   AssertExit 'register uploaded master'
   & python -X utf8 (Join-Path $PSScriptRoot 'resolve_cache.py') cleanup --root $RootDir --episode $id --execute
   if($LASTEXITCODE -ne 0){Write-Warning 'Upload verified; CacheClip was retained. Inspect cache state before cleanup.'}
   & python -X utf8 (Join-Path $PSScriptRoot 'resolve_cache.py') delete-uploaded-master --root $RootDir --episode $id --execute
   AssertExit 'delete only the SHA-verified uploaded local master'
   ConfirmUpload $id
  }
  'confirm-upload' { ConfirmUpload $id }
  'cleanup-cache' {
   & python -X utf8 (Join-Path $PSScriptRoot 'resolve_cache.py') cleanup --root $RootDir --episode $id --execute
   AssertExit 'verified upload CacheClip cleanup'
  }
  'fetch-final' {
   $finalName=& python -X utf8 (Join-Path $PSScriptRoot 'episode_lifecycle.py') final-name --root $RootDir --episode $id
   AssertExit 'story chapter metadata'
   $report=Join-Path $RootDir ".kwjd\remote-reports\$id.json"
   Download "qingqingwu/post/reports/$id.json" $report
   if(-not(Test-Path -LiteralPath $report)){throw 'No remote validated final report'}
   $finalLocal=Join-Path $RootDir "final\$finalName"
   Download "qingqingwu/post/final/$id.final-1080p60.mkv" $finalLocal
   & python -X utf8 (Join-Path $PSScriptRoot 'episode_lifecycle.py') record-final --root $RootDir --episode $id --file $finalLocal --remote-report $report
   AssertExit 'final receipt validation'
  }
  'complete-episode' {
   if(-not $Episode -or -not $QualityNote){throw 'Complete one explicit Episode with actual QualityNote describing picture/subtitles/sync/audio checks'}
   $lifecycle=Join-Path $PSScriptRoot 'episode_lifecycle.py'
   & python -X utf8 $lifecycle quality-checked --root $RootDir --episode $id --note $QualityNote
   AssertExit 'final quality check receipt'
   $receipt=Join-Path $RootDir ".kwjd\receipts\$id.json"
   Upload $receipt "qingqingwu/post/receipts/$id.json" | Out-Null
   & ssh.exe -o BatchMode=yes $SshAlias "python3 ~/qingqingwu/repo/scripts/episode_lifecycle.py cleanup --root ~/qingqingwu --episode $id --role linux --receipt ~/qingqingwu/post/receipts/$id.json --execute"
   AssertExit 'Linux receipt-gated episode cleanup'
   & python -X utf8 $lifecycle cleanup --root $RootDir --episode $id --role windows --execute
   AssertExit 'Windows receipt-gated episode cleanup'
   Download "qingqingwu/.kwjd/cleanup/$id.linux.json" (Join-Path $RootDir ".kwjd\cleanup\$id.linux.json")
  }
  'submit' {
   & python -X utf8 (Join-Path $PSScriptRoot 'episode_lifecycle.py') gate-next --root $RootDir --episode $id
   AssertExit 'previous local upload cleanup gate'
   $files=@(Get-ChildItem -LiteralPath (Join-Path $RootDir 'masters') -File -ErrorAction SilentlyContinue |
       Where-Object {$_.Name -match ("^$id[.]clean-master[.](mp4|mkv|mov)$")})
   if($files.Count -eq 1){
    $name=$files[0].Name
    $localHash=(Get-FileHash -LiteralPath $files[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
   }else{
    $uploaded=Get-Content -LiteralPath (Join-Path $RootDir ".kwjd\uploads\$id.json") -Raw -Encoding utf8|ConvertFrom-Json
    if(-not $uploaded.verified -or -not $uploaded.local_master_deleted){throw "No verified uploaded master receipt: $id"}
    $name=[IO.Path]::GetFileName($uploaded.local_path)
    $localHash=$uploaded.sha256
   }
   if((RemoteHash "qingqingwu/post/incoming/$name") -ne $localHash){throw "Upload and verify current master first: $id"}
   $job=[ordered]@{
    schema_version=1;episode=$id;state='QUEUED'
    created_at=(Get-Date).ToUniversalTime().ToString('o')
    master="incoming/$name";srt="../output/$id.zh.srt"
    master_sha256=$localHash
    style='styles/template.ass';output="final/$id.final-1080p60.mkv"
    spec=@{width=1920;height=1080;fps=60;subtitle='burn-in-last'}
   }
   $tmp=Join-Path $env:TEMP "$id.kwjd-job.json"
   try {
    $job|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $tmp -Encoding utf8
    Upload $tmp "qingqingwu/post/jobs/$id.json" | Out-Null
    $submissions=Join-Path $RootDir '.kwjd\submissions'
    New-Item -ItemType Directory -Force $submissions|Out-Null
    Copy-Item -LiteralPath $tmp -Destination (Join-Path $submissions "$id.json")
   }finally{Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue}
  }
 }
}
