param(
    [Parameter(Mandatory=$true)]
    [string]$Url,
    
    [Parameter(Mandatory=$false)]
    [string]$OutputDir = ".\captures",
    
    [Parameter(Mandatory=$false)]
    [switch]$ListOnly
)

# Ensure output directory exists
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

# First, run yt-dlp to get stream info
$streamInfo = yt-dlp --dump-json $Url | ConvertFrom-Json

# Get current timestamp for unique folder
$timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm"
$eventName = $streamInfo.title -replace '[^\w\-\.]', '_'
$captureDir = Join-Path $OutputDir "$eventName-$timestamp"

if (-not (Test-Path $captureDir)) {
    New-Item -ItemType Directory -Path $captureDir | Out-Null
}

# Function to sanitize stream names for filenames
function Get-SafeName {
    param([string]$name)
    return ($name -replace '[^\w\-\.]', '_')
}

# Get available streams using --list-formats
$formats = yt-dlp --list-formats $Url | Out-String

# Parse the formats output to find available streams
$streams = @()
foreach ($line in ($formats -split "`n")) {
    if ($line -match "(\d+)\s+.*\s+(MultiCam|HighCam|\w+Cam)") {
        $streams += @{
            formatId = $Matches[1]
            name = $Matches[2]
        }
    }
}

Write-Host "Found $($streams.Count) streams:"
foreach ($stream in $streams) {
    Write-Host "- $($stream.name) (format ID: $($stream.formatId))"
}

if ($ListOnly) {
    return
}

# Launch yt-dlp for each stream
$jobs = @()
foreach ($stream in $streams) {
    $safeName = Get-SafeName $stream.name
    $outputTemplate = Join-Path $captureDir "${safeName}_%(title)s.%(ext)s"
    
    $ytdlpArgs = @(
        $Url,
        "-f", $stream.formatId,
        "-o", $outputTemplate,
        "--no-part",  # Don't use .part files
        "--live-from-start"  # Capture from the beginning of the stream
    )
    
    Write-Host "Starting capture of $($stream.name) stream..."
    $job = Start-Job -ScriptBlock {
        param($ytdlp, $args)
        & $ytdlp @args
    } -ArgumentList "yt-dlp", $ytdlpArgs
    
    $jobs += @{
        job = $job
        name = $stream.name
    }
}

# Monitor jobs
try {
    while ($jobs.job | Where-Object { $_.State -eq 'Running' }) {
        foreach ($jobInfo in $jobs) {
            $status = $jobInfo.job | Receive-Job
            if ($status) {
                Write-Host "[$($jobInfo.name)] $status"
            }
        }
        Start-Sleep -Seconds 5
    }
}
finally {
    # Cleanup jobs
    $jobs.job | Remove-Job -Force
}

Write-Host "All captures completed. Files saved in: $captureDir"
