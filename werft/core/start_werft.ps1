[CmdletBinding()]
param(
    [int]$MaxCycles = 0,
    [int]$PauseSeconds = -1,
    [string]$PythonPath = "",
    [string]$LogPath = ""
)

$coreDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $PythonPath) {
    $workspacePython = Join-Path $coreDir "..\..\.venv\Scripts\python.exe"
    $PythonPath = if (Test-Path $workspacePython) { $workspacePython } else { "python" }
}
if (-not $LogPath) {
    $runtimeDir = Join-Path $coreDir "..\runtime"
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
    $LogPath = Join-Path $runtimeDir "master_loop_windows.log"
}

Set-Location $coreDir
$env:PYTHONPATH = $coreDir

if ($PauseSeconds -lt 0) {
    $PauseSeconds = [int](& $PythonPath -c "from configloader import config; print(config.get('timeouts.master_loop_pause_sec', 10))")
}

while ($true) {
    $arguments = if ($MaxCycles -gt 0) {
        @("-c", "from master_loop import main; main(max_zyklen=$MaxCycles, pause_s=$PauseSeconds)")
    } else {
        @("-c", "from master_loop import main; main(pause_s=$PauseSeconds)")
    }
    & $PythonPath @arguments *>> $LogPath
    $exitCode = $LASTEXITCODE
    Add-Content -Path $LogPath -Value "$(Get-Date -Format s) master_loop exited with code $exitCode; restarting in 3s"
    if ($MaxCycles -gt 0) {
        exit $exitCode
    }
    Start-Sleep -Seconds 3
}
