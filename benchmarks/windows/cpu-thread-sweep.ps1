# Same synthetic turn and CPU settings at several thread counts; ASCII for PS 5.1.
param(
    [string]$Kit = "C:\mirobody-bench",
    [ValidateScript({ @($_ | Where-Object { $_ -lt 1 }).Count -eq 0 })]
    [int[]]$Threads = @(4, 6, 8, 10, 16),
    [string]$Cases = "",
    [string]$Results = "",
    [int]$Port = 18558
)
. "$PSScriptRoot\native-common.ps1"
$env:PYTHONUTF8 = "1"
$Uv = Find-Uv
$Model = Join-Path $Kit "models\MiniCPM5-2B-Q8_0.gguf"
$Template = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\..\docker\minicpm5-2b.jinja"))
$Probe = Join-Path $PSScriptRoot "speed_probe.py"
if (-not $Cases) { $Cases = Join-Path $PSScriptRoot "results\raw\cpu_case_one.json" }
$Exe = Find-Server (Join-Path $Kit "llama-cpu")
if (-not $Exe) { throw "No llama-server.exe beneath $Kit\llama-cpu." }
foreach ($file in @($Model, $Template, $Probe, $Cases)) { Require-File $file }
if (-not $Results) { $Results = Join-Path $Kit "_work\threads-$(Get-Date -Format yyyyMMdd-HHmmss-fff)-$PID" }
if (Test-Path -LiteralPath $Results) { throw "Results already exists: $Results; choose a fresh directory." }
$null = New-Item -ItemType Directory -Path $Results
$Out = Join-Path $Results "cpu-threads.out"
function Log([string]$Line) {
    Write-Host $Line
    Add-Content -LiteralPath $Out -Value $Line -Encoding UTF8
}
Log "#### $(Get-Date -Format s) logical cores $((Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum)"
Log "#### physical cores $((Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfCores -Sum).Sum); results $Results"
$failed = 0
foreach ($t in $Threads) {
    $process = $null
    try {
        Assert-FreePort $Port
        $argsLine = "-m `"$Model`" --jinja --chat-template-file `"$Template`" -c 32768 -np 2 --kv-unified " +
                    "--cache-ram 1024 --host 127.0.0.1 --port $Port -t $t"
        $stderrPath = Join-Path $Results "threads-$t.log"
        $process = Start-Process -FilePath $Exe -ArgumentList $argsLine -PassThru -WindowStyle Hidden `
            -RedirectStandardError $stderrPath -RedirectStandardOutput (Join-Path $Results "threads-$t.stdout.log")
        Wait-Server $process $Port
        $match = Select-String -LiteralPath $stderrPath -Pattern 'n_threads = (\d+)' | Select-Object -First 1
        $actual = if ($match) { $match.Matches.Groups[1].Value } else { "unreported" }
        Log "== t=$t (server reports n_threads=$actual)"
        $timer = [Diagnostics.Stopwatch]::StartNew()
        Invoke-Probe $Uv @("run", "--no-project", "--python", "3.12", $Probe,
                           "http://127.0.0.1:$Port", "cpu-t$t", $Cases, (Join-Path $Results "cpu-t$t.out.json"))
        Log "   elapsed $([int]$timer.Elapsed.TotalSeconds)s"
        $proc = Get-Process -Id $process.Id
        Log "   working_set_mb $([int]($proc.WorkingSet64 / 1MB))"
    } catch {
        $failed++
        Log "== t=$t FAILED: $($_.Exception.Message); see $Results"
    } finally {
        Stop-Server $process
    }
}
Log "#### done $(Get-Date -Format s); failed $failed"
if ($failed -gt 0 -or $Threads.Count -eq 0) { exit 1 }
exit 0
