# Continues the batches paused on 2026-11-14, as two parallel lanes in their own windows:
#   lane A: finish Concorde, then topics-batch2-a.txt (P-51 ... Hoover Dam)
#   lane B: finish Panama Canal, then topics-batch2-b.txt (Apollo 13 ... Chinook)
# Run from the SlopBot folder:  powershell -ExecutionPolicy Bypass -File resume-batches.ps1
$root = $PSScriptRoot
$awake = 'Add-Type -Name Power -Namespace SlopBot -MemberDefinition ''[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint flags);''; [SlopBot.Power]::SetThreadExecutionState([uint32]"0x80000001") | Out-Null'
$py = ".\.venv\Scripts\python.exe -u make_video.py explainer"

$lanes = @(
    @{ name = "A"; resume = "output\20261114-012209-why-the-fastest-airliner-ever-was-retired"; topics = "topics-batch2-a.txt" },
    @{ name = "B"; resume = "output\20261114-011757-how-ships-climb-over-panama-with-zero-pumps"; topics = "topics-batch2-b.txt" }
)
foreach ($lane in $lanes) {
    $log = "output\lane-$($lane.name).log"
    $cmd = "Set-Location '$root'; $awake; " +
           "& { $py --resume $($lane.resume); $py --topics-file $($lane.topics) } 2>&1 | Tee-Object -FilePath $log -Append"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd -WorkingDirectory $root
    Write-Host "lane $($lane.name) started (log: $log)"
}
