# Using the local Marven Ollama model

This folder contains a local Ollama `Modelfile.marven` which defines a custom `marven` model wrapper.

Quick steps to install and test locally on Windows:

1. Open PowerShell and change to this folder (where `Modelfile.marven` lives):

   cd path\to\Marven\ollama

2. Run the installer script (this copies files to `%USERPROFILE%\.ollama\models\marven`):

   .\install_modelfile.ps1

   You can also specify a different model name or source dir:

   .\install_modelfile.ps1 -SourceDir . -ModelName marven

3. Verify Ollama CLI is installed and on your PATH. If you have the CLI, test the model:

   ollama run marven --prompt "Hello"

Notes and troubleshooting
- If `install_modelfile.ps1` reports that `Modelfile.marven` was not found, run the script from this folder or pass `-SourceDir`.
- If the model requires weight blobs, copy them into the `blobs/` subfolder inside `%USERPROFILE%\.ollama\models\marven`.
- If the Ollama CLI is not installed, follow the official Ollama install instructions (not included here). After installing, re-run the test command.

How the project uses the `marven` model
- The repository's Python code (for example, `marven.py`) defaults to `DEFAULT_MODEL = 'marven'` and uses `langchain_ollama.OllamaLLM(model='marven')`.
- No code changes are required if the model directory is placed at `%USERPROFILE%\.ollama\models\marven`.

Example: using the model from Python (already present in this repo)

The code in `marven.py` constructs an Ollama LLM like this:

```python
from langchain_ollama import OllamaLLM

llm = OllamaLLM(model='marven', temperature=0.7)
resp = llm.generate('Hello')
print(resp)
```

If you encounter errors in Python, ensure `langchain-ollama` is installed in your virtual environment and that the `ollama` CLI is reachable from your environment.

If you want, I can: (a) make the script create a backup of any existing model directory, (b) add an automated test that runs a tiny prompt and confirms a response, or (c) detect and adapt the Modelfile base (e.g., change FROM lines) — tell me which and I'll implement it.
