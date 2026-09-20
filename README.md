# Marven

Marven is a local-first conversational AI project made up of several small, inspectable prototypes. It combines a Python backend and command-line tools with an optional React interface, local model integrations, policy-checked capabilities, memory experiments, and a voice-interrupt prototype. The project is designed for experimentation on a user's own machine rather than as a hosted service.

## What is included

- **Python core and CLI:** `marven.py`, `marven_cli.py`, and `marven_text_chat.py` provide the core response flow and text interfaces.
- **Flask API:** `server.py` exposes local chat and capability endpoints for the web client. It can use local Ollama or compatible OpenAI-style model endpoints configured through environment variables.
- **React UI:** `marven-react-app/` contains the browser client for chatting with the local API.
- **Policy and self-update experiments:** `marven_local/` contains capability checks, plugins, learner experiments, and signed update workflows. These are experimental and should be reviewed before enabling them.
- **Voice prototype:** `marven-voice-interrupt-prototype_cpu_tuned/` contains an optional local speech input/output server. Model weights are intentionally not committed.
- **Training and Ollama helpers:** `marven-training/` and `ollama/` contain optional local-model experimentation files.

Runtime conversations, memory archives, audit logs, model weights, generated builds, and the `ShadowBox_Framework/` directory are intentionally excluded from this public repository. See `.gitignore` for the publication boundary.

## Requirements

- Python 3.11 or newer
- Node.js 18 or 20 LTS for the React client
- Ollama, or another compatible local model service, for model-backed chat
- Windows PowerShell commands below work on Windows; equivalent Python and npm commands work on macOS and Linux

## Install

Clone the repository and create an isolated Python environment:

```powershell
git clone https://github.com/ahesh001/Marven.git
cd Marven
py -3.11 -m venv .venv311
.\.venv311\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

The optional voice prototype has a separate dependency set:

```powershell
python -m pip install -r marven-voice-interrupt-prototype_cpu_tuned\requirements.txt
```

Install the frontend dependencies:

```powershell
cd marven-react-app
npm ci
cd ..
```

Install and start a local Ollama model separately, for example:

```powershell
ollama run tinyllama
```

## Test

Run the Python tests from the repository root:

```powershell
python -m pytest
```

Run the React test suite:

```powershell
cd marven-react-app
npm test -- --watchAll=false
```

Build the React client as a publication check:

```powershell
npm run build
```

The generated `build/` directory is ignored and should not be committed.

## Run Marven

For a text-only session:

```powershell
python marven_text_chat.py -s chat -m tinyllama
```

For the local API:

```powershell
python server.py
```

In a second terminal, start the React client:

```powershell
cd marven-react-app
npm start
```

Open `http://localhost:3000`. The API listens on `http://127.0.0.1:8000` by default. Set `REACT_APP_API_BASE` if the API runs elsewhere. The API and the model service must be running locally before sending model-backed requests.

For the optional voice prototype, install its dependencies and start:

```powershell
python marven-voice-interrupt-prototype_cpu_tuned\server.py
```

Then open `marven-voice-interrupt-prototype_cpu_tuned/client.html` in a browser. A Vosk model and any optional TTS dependencies must be installed separately; no model weights are included here.

## Configuration and privacy

Marven is local-first, but local does not automatically mean private. Chat history, memory, uploaded files, model prompts, and server logs may contain sensitive information depending on how you run the software. Review the code and configure storage before using personal or confidential data. Do not commit credentials, model caches, conversation exports, local paths, or private audio.

Read [PRIVACY.md](PRIVACY.md) for the project privacy policy and data-handling expectations. The repository publication boundary is enforced by `.gitignore`, but contributors should review staged files before every push.

## Project status

This is an experimental prototype collection. APIs, prompts, model adapters, and storage formats may change without notice. It is not a production security boundary, medical service, therapist, or autonomous decision-maker.

## License

Marven is released under the [MIT License](LICENSE). Third-party dependencies and model weights retain their own licenses and are not relicensed by this repository.
