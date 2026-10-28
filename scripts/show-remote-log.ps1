param(
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9_-]+$')][string]$SshAlias,
    [Parameter(Mandatory)][ValidatePattern('^/[A-Za-z0-9_./-]+$')][string]$LogPath
)

$ErrorActionPreference = 'Stop'
if ($LogPath.Split('/') -contains '..') { throw 'Parent traversal is not allowed' }
$Host.UI.RawUI.WindowTitle = 'Kwjd remote progress log'
Write-Host "Read-only remote log: ${SshAlias}:$LogPath"
Write-Host 'Closing this window does not stop the remote job. Ctrl+C ends this view.'
# Parameter validation above restricts this remote shell argument to a safe path.
& ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=30 $SshAlias "tail -n 60 -F -- '$LogPath'"
if ($LASTEXITCODE -ne 0) { Write-Warning "Log connection ended with exit code $LASTEXITCODE; the remote job is independent." }
