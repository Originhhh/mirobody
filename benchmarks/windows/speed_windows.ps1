# MiniCPM5-2B Q8_0 speed on fixed synthetic turns. ASCII for PowerShell 5.1.
# Invoke this script from a checkout; -Kit contains downloaded models/builds.
param(
    [string]$Kit = "C:\mirobody-bench",
    [ValidateSet("all", "gpu", "cpu")] [string]$Only = "all",
    [string]$Results = "",
    [string]$CudaServer = "",
    [int]$Port = 18557
)
. "$PSScriptRoot\native-common.ps1"
$env:PYTHONUTF8 = "1"
$Uv = Find-Uv
$Model = Join-Path $Kit "models\MiniCPM5-2B-Q8_0.gguf"
$Draft = Join-Path $Kit "models\MiniCPM5-2.6B-DSpark.gguf"
$Template = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\..\docker\minicpm5-2b.jinja"))
$Probe = Join-Path $PSScriptRoot "speed_probe.py"
foreach ($file in @($Model, $Template, $Probe)) { Require-File $file }
if (-not $Results) { $Results = Join-Path $Kit "_work\speed-$(Get-Date -Format yyyyMMdd-HHmmss-fff)-$PID" }
if (Test-Path -LiteralPath $Results) { throw "Results already exists: $Results; choose a fresh directory." }
$null = New-Item -ItemType Directory -Path $Results
$Out = Join-Path $Results "speed_windows.out"
function Log([string]$Line) {
    Write-Host $Line
    Add-Content -LiteralPath $Out -Value $Line -Encoding UTF8
}
function Get-VramMb {
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $usage = & nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>$null | Select-Object -First 1
        if ($LASTEXITCODE -eq 0 -and $usage -match '^\s*\d+\s*$') { return [int]$usage }
    }
    return $null
}
function Run-One([string]$Label, [string]$Exe, [string]$Cases, [string]$Flags) {
    if (-not $Exe) { throw "No llama-server.exe for selected configuration $Label; use ONLY to select installed builds." }
    $process = $null
    try {
        Assert-FreePort $Port
        Require-File $Cases
        $load = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
        Log "== $Label cpu_load $load% $(Get-Date -Format s)"
        $before = Get-VramMb
        $argLine = "-m `"$Model`" --jinja --chat-template-file `"$Template`" -c 32768 -np 2 --kv-unified " +
                   "--cache-ram 1024 --host 127.0.0.1 --port $Port $Flags"
        $process = Start-Process -FilePath $Exe -ArgumentList $argLine -PassThru -WindowStyle Hidden `
            -RedirectStandardError (Join-Path $Results "$Label.log") `
            -RedirectStandardOutput (Join-Path $Results "$Label.stdout.log")
        Wait-Server $process $Port
        Invoke-Probe $Uv @("run", "--no-project", "--python", "3.12", $Probe,
                           "http://127.0.0.1:$Port", $Label, $Cases, (Join-Path $Results "$Label.out.json"))
        $proc = Get-Process -Id $process.Id
        $after = Get-VramMb
        $vram = if ($null -ne $before -and $null -ne $after) { "$($after - $before)" } else { "n/a" }
        Log "   $Label working_set_mb $([int]($proc.WorkingSet64 / 1MB)) private_mb $([int]($proc.PrivateMemorySize64 / 1MB)) vram_mb $vram"
        return $true
    } finally {
        Stop-Server $process
    }
}

$Vulkan = Find-Server (Join-Path $Kit "llama-vulkan")
$Sm = $null
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $cc = & nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>$null | Select-Object -First 1
    if ($LASTEXITCODE -eq 0 -and $cc -match '^\s*(\d+)\.') { $Sm = [int]$Matches[1] }
}
$CudaWanted = if ($null -ne $Sm -and $Sm -ge 12) { "llama-cuda-13.4" } else { "llama-cuda" }
$Cuda = $null
$CudaDirs = @(Get-ChildItem -LiteralPath $Kit -Directory -Filter "llama-cuda*" -ErrorAction SilentlyContinue |
    Sort-Object { if ($_.Name -eq $CudaWanted) { 0 } else { 1 } }, Name)
foreach ($dir in $CudaDirs) { $Cuda = Find-Server $dir.FullName; if ($Cuda) { break } }
if ($CudaServer) { Require-File $CudaServer; $Cuda = $CudaServer }
$Cpu = Find-Server (Join-Path $Kit "llama-cpu")
$cores = (Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum
$gpus = (Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) -join "; "
Log "#### $(Get-Date -Format s) $([Environment]::OSVersion.VersionString) logical_cores $cores gpu: $gpus"
Log "#### uv $Uv; CUDA build $Cuda (sm_$Sm, wanted $CudaWanted); results $Results"
$q8 = "-c 65536 -fa on -ctk q8_0 -ctv q8_0"
$ds = "-fa on -md `"$Draft`" --spec-type draft-dspark --spec-draft-n-max 7"
$matrix = @()
if ($Only -ne "cpu") {
    $matrix += ,@("vulkan-base", $Vulkan, "speed_cases.json", "-ngl 99")
    $matrix += ,@("vulkan-64k-q8", $Vulkan, "speed_cases.json", "-ngl 99 $q8")
    $matrix += ,@("vulkan-dspark7", $Vulkan, "speed_cases.json", "-ngl 99 -ngld 99 $ds")
    $matrix += ,@("cuda-base", $Cuda, "speed_cases.json", "-ngl 99")
    $matrix += ,@("cuda-64k-q8", $Cuda, "speed_cases.json", "-ngl 99 $q8")
    $matrix += ,@("cuda-dspark7", $Cuda, "speed_cases.json", "-ngl 99 -ngld 99 $ds")
}
if ($Only -ne "gpu") {
    $matrix += ,@("cpu4-base", $Cpu, "speed_cases_cpu.json", "-t 4")
    $matrix += ,@("cpu4-64k-q8", $Cpu, "speed_cases_cpu.json", "-t 4 $q8")
    $matrix += ,@("cpu4-dspark7", $Cpu, "speed_cases_cpu.json", "-t 4 $ds")
}
if ($env:ONLY) {
    $labels = @($env:ONLY.Split(",") | ForEach-Object { $_.Trim() })
    foreach ($label in $labels) {
        if ($label -notin @($matrix | ForEach-Object { $_[0] })) { throw "Unknown/excluded ONLY label: $label" }
    }
    $matrix = @($matrix | Where-Object { $_[0] -in $labels })
}
$ran = 0
$failed = 0
foreach ($item in $matrix) {
    try {
        if ($item[0] -like "*-dspark*" -and $item[1]) { Require-File $Draft }
        if (Run-One $item[0] $item[1] (Join-Path $PSScriptRoot $item[2]) $item[3]) { $ran++ }
    } catch {
        $failed++
        Log "== $($item[0]) FAILED: $($_.Exception.Message); see $Results"
    }
}
Log "#### done $(Get-Date -Format s); completed $ran; failed $failed"
if ($failed -gt 0 -or $ran -eq 0) { exit 1 }
exit 0
