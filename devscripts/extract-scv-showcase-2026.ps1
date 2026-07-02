# Define paths
$inputVideo = "F:\cadence-captures\2026\Santa Clara Vanguard Showcase\2026-06-27_Multicam.mp4"
$outputFolder = "R:\Marching Arts Archive\DCI\2026\06-26-2026 - Santa Clara Vanguard Showcase"
$csvData = @"
06-26-2026 - Santa Clara Vanguard Showcase (MULTI CAM),5:39,1:57:20
"@

# Create output directory if it doesn't exist
if (-not (Test-Path -Path $outputFolder)) {
    New-Item -ItemType Directory -Force -Path $outputFolder
    Write-Host "Created output directory: $outputFolder"
}

# Convert the CSV string to an array of objects
$clips = $csvData | ConvertFrom-Csv -Header "Name", "StartTime", "EndTime"

# Process each clip
$total = $clips.Count
$current = 0

foreach ($clip in $clips) {
    $current++
    
    # Format output filename
    $outputFile = Join-Path $outputFolder "$($clip.Name).mp4"
    
    Write-Host "[$current/$total] Processing: $($clip.Name)"
    Write-Host "Start Time: $($clip.StartTime) - End Time: $($clip.EndTime)"
    
    # Run ffmpeg
    & ffmpeg -i "$inputVideo" -ss $clip.StartTime -to $clip.EndTime -c copy "$outputFile"
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Success: $($clip.Name)" -ForegroundColor Green
    } else {
        Write-Host "Failed: $($clip.Name)" -ForegroundColor Red
    }
    
    Write-Host ("=" * 80)
}

Write-Host "All clips processed!"