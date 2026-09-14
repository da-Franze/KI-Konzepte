[CmdletBinding()]
param()

$processes = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'from master_loop import main' }

foreach ($process in $processes) {
    Stop-Process -Id $process.ProcessId -Force
    Write-Output "Stopped master_loop process $($process.ProcessId)"
}

if (-not $processes) {
    Write-Output "No master_loop process found"
}
