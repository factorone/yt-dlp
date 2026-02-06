# Define paths
$inputVideo = "R:\Marching Arts Archive\DCI\2025\08-09-2025 - DCI All-Age World Championships Finals (Indianapolis, IN)\08-09-2025 - DCI All-Age World Championships Finals (indianapolis, IN) HIGH CAM.mp4"
$outputFolder = "R:\Marching Arts Archive\DCI\2025\08-09-2025 - DCI All-Age World Championships Finals (Indianapolis, IN)\High Cam"
$csvData = @"
12 - Fusion core,0:11:32,0:20:52
11 - MBI,0:28:45,0:38:27
10 - Govenaires,0:45:26,0:54:52
09 - Sunrisers,1:03:33,1:13:28
08 - Rogues Hollow Regiment,1:19:33,1:29:47
07 - White Sabers,1:36:43,1:46:30
06 - Connecticut Hurricanes,2:09:50,2:20:40
05 - Cincinnati Tradition,2:25:10,2:34:42
04 - Atlanta CV,2:40:05,2:51:58
03 - Bushwackers,2:59:25,3:09:49
02 - Hawthorne Caballeros,3:13:54,3:25:20
01 - Reading Buccaneers,3:31:55,3:43:51
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
    $outputFile = Join-Path $outputFolder "$($clip.Name) (High Cam).mp4"
    
    Write-Host "[$current/$total] Processing: $($clip.Name)"
    Write-Host "Start Time: $($clip.StartTime) - End Time: $($clip.EndTime)"
    
    # Run ffmpeg
    &   
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Success: $($clip.Name)" -ForegroundColor Green
    } else {
        Write-Host "Failed: $($clip.Name)" -ForegroundColor Red
    }
    
    Write-Host ("=" * 80)
}

Write-Host "All clips processed!"