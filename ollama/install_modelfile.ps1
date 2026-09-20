param(
    [string]$SourceDir = (Split-Path -Parent $MyInvocation.MyCommand.Path),
    [string]$ModelName = "marven",
    [string]$TargetDir = ""
)

if (-not $TargetDir -or $TargetDir -eq "") {
    $TargetDir = Join-Path $env:USERPROFILE ".ollama\models\$ModelName"
}

Write-Output "Source directory: $SourceDir"
Write-Output "Target directory: $TargetDir"

$sourceModelfile = Join-Path $SourceDir "Modelfile.marven"
$sourceBlobs = Join-Path $SourceDir "blobs"

if (-not (Test-Path $sourceModelfile)) {
    Write-Error "Modelfile.marven not found in $SourceDir. Please run this script from the folder containing Modelfile.marven or pass -SourceDir."
    exit 1
}

# Create target directory
if (-not (Test-Path $TargetDir)) {
    New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
    Write-Output "Created directory: $TargetDir"
}

# Copy Modelfile
Copy-Item -Path $sourceModelfile -Destination (Join-Path $TargetDir "Modelfile.marven") -Force
Write-Output "Copied Modelfile.marven -> $TargetDir"

# Copy blobs if present
if (Test-Path $sourceBlobs) {
    $targetBlobs = Join-Path $TargetDir "blobs"
    Copy-Item -Path $sourceBlobs -Destination $targetBlobs -Recurse -Force
    Write-Output "Copied blobs/ -> $targetBlobs"
} else {
    Write-Output "No blobs/ folder found in $SourceDir — skipping blobs copy."
}

# Detect ollama CLI
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaCmd) {
    Write-Output "Found ollama CLI at: $($ollamaCmd.Path)"
    Write-Output "Try: ollama run $ModelName --prompt 'Hello'"
} else {
    Write-Warning "The 'ollama' CLI was not found in PATH. If you haven't installed Ollama, please install it and ensure it's on your PATH."
    Write-Output "After installing, run: ollama run $ModelName --prompt 'Hello'"
}

Write-Output "Installation complete. If your model uses external blobs you may need to ensure the files are present in the blobs/ folder under the model directory."
