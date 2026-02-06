# Define paths
$inputVideo = "R:\Marching Arts Archive\DCI\2025\08-09-2025 - DCI All-Age World Championships Finals (Indianapolis, IN)\08-09-2025 - DCI All-Age World Championships Finals (Indianapolis, IN) MULTI CAM.mp4"
$outputFolder = "R:\Marching Arts Archive\DCI\2025\08-09-2025 - DCI All-Age World Championships Finals (Indianapolis, IN)\Multi Cam"
$csvData = @"
12 - Fusion core,05:25,0:14:01
11 - MBI,21:56,0:31:40
10 - Govenaires,0:38:37,0:48:02
09 - Sunrisers,0:56:50,1:06:38
08 - Rogues Hollow Regiment,1:12:44,1:23:03
07 - White Sabers,1:29:54,1:39:47
06 - Connecticut Hurricanes,2:03:04,2:13:53
05 - Cincinnati Tradition,2:18:20,2:27:55
04 - Atlanta CV,2:35:05,2:45:10
03 - Bushwackers,2:52:35,3:03:00
02 - Hawthorne Caballeros,3:07:0,3:18:30
01 - Reading Buccaneers,3:27:00,3:37:12
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