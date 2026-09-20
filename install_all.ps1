# install_all.ps1
# Ensures all Marven voice prototype dependencies are installed into .venv311

$venvPy = "C:\Marven\.venv311\Scripts\python.exe"
$reqFile = "C:\Marven\marven-voice-interrupt-prototype_cpu_tuned\requirements.txt"

if (!(Test-Path $venvPy)) {
    Write-Host "`nERROR: Can't find $venvPy"
    Write-Host "Did you create the venv already? Run:"
    Write-Host "  py -3.11 -m venv C:\Marven\.venv311"
    exit 1
}

Write-Host "`n>>> Using interpreter:" ( & $venvPy -V )

Write-Host "`n>>> Upgrading pip / setuptools / wheel ..."
& $venvPy -m pip install --upgrade pip setuptools wheel

Write-Host "`n>>> Installing core dependencies from requirements.txt ..."
& $venvPy -m pip install -r $reqFile

Write-Host "`n>>> Installing CPU-only PyTorch + torchaudio (needed for Coqui TTS) ..."
& $venvPy -m pip install torch==2.2.2+cpu torchaudio==2.2.2+cpu --index-url https://download.pytorch.org/whl/cpu

Write-Host "`n>>> Installing Coqui TTS itself ..."
& $venvPy -m pip install TTS==0.22.0

Write-Host "`n>>> Final check ..."
& $venvPy -m pip show websockets webrtcvad soundfile vosk faster-whisper torch torchaudio TTS

Write-Host "`nAll installs complete. To run the server, use:"
Write-Host "  & $venvPy C:\Marven\marven-voice-interrupt-prototype_cpu_tuned\server.py"
