# Build script for ShadowBox UI
# Run from anywhere: .\build-ui.ps1

$uiPath = Join-Path $PSScriptRoot "ShadowBox_Framework\ui-chatbox-app"

Write-Host "Building ShadowBox UI from: $uiPath" -ForegroundColor Cyan

# Set legacy OpenSSL provider for Node 24 compatibility
$env:NODE_OPTIONS = '--openssl-legacy-provider'

# Run npm build
npm --prefix "$uiPath" run build

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nBuild completed successfully!" -ForegroundColor Green
} else {
    Write-Host "`nBuild failed with exit code: $LASTEXITCODE" -ForegroundColor Red
}
