# Marven — Windows Run Commands (Cheat Sheet)

This guide gives copy–paste commands to run text-only chat and the voice prototype on Windows.

Assumptions
- Project root: `C:\Marven`
- Python 3.11 installed as `py -3.11`
- Virtual env: `C:\Marven\.venv311` (single venv to avoid confusion)

## 1) Create / Use Virtual Env

Command Prompt (cmd):
- Create venv: `py -3.11 -m venv C:\Marven\.venv311`
- Activate: `C:\Marven\.venv311\Scripts\activate.bat`

PowerShell (no activation needed):
- Use interpreter directly: `C:\Marven\.venv311\Scripts\python.exe -V`

## 2) Install Dependencies

Text-only chat (minimal packages):
- One-liner: `C:\Marven\.venv311\Scripts\python.exe -m pip install langchain-ollama langchain langchain-community langchain-core`
- Or run helper script (PowerShell): `powershell -ExecutionPolicy Bypass -NoProfile -File "C:\Marven\install_text.ps1"`

Voice prototype (full stack: websockets, Vosk/Whisper, Coqui optional):
- `powershell -ExecutionPolicy Bypass -NoProfile -File "C:\Marven\install_all.ps1"`

Optional: pull a small Ollama model once (faster first reply):
- `ollama run tinyllama`

## 3) Run Text Chat (No Audio)

Fastest (no activation):
- `C:\Marven\.venv311\Scripts\python.exe C:\Marven\marven_text_chat.py -s chat -m tinyllama`

With cmd activation:
- `C:\Marven\.venv311\Scripts\activate.bat`
- `python C:\Marven\marven_text_chat.py -s chat -m tinyllama`

PowerShell helper:
- `powershell -ExecutionPolicy Bypass -NoProfile -File "C:\Marven\run_marven_text.ps1" -s chat -m tinyllama`

Notes:
- Inside the chat: `/exit`, `/session NEW_ID`, `/clearhistory`
- If `langchain_ollama` missing, install via the pip line in section 2.

## 4) Run Voice Server (Audio)

Start server (no activation needed):
- `C:\Marven\.venv311\Scripts\python.exe C:\Marven\marven-voice-interrupt-prototype_cpu_tuned\server.py`

Open client:
- Double-click `C:\Marven\marven-voice-interrupt-prototype_cpu_tuned\client.html` (Chrome/Edge), then click "Start" and "Ping Marven".

## 5) PowerShell Execution Policy (If Scripts Won’t Run)

Current session only:
- `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

Start new PS already in bypass:
- `powershell -ExecutionPolicy Bypass -NoProfile`

Unblock a single file (optional):
- `Unblock-File -Path C:\Marven\run_marven_text.ps1`

## 6) Configuration Tips

- Text latency is dominated by model generation. Use `-m tinyllama` (or another small local Ollama model).
- Voice: adjust `tts.wait_for_silence_ms` in `marven-voice-interrupt-prototype_cpu_tuned\server.py:49` via `config.yaml` (add under `tts:`) to reduce wait after you stop talking.
- Vosk model path must exist (see `config.yaml: vosk.model_path`). Download a Vosk English model and place it under `C:\Marven\marven-voice-interrupt-prototype_cpu_tuned\models` if missing.
- eSpeak NG: `tts.espeak_path` in `config.yaml` must match your installation (e.g. `C:/Program Files/eSpeak NG/espeak-ng.exe`).

## 7) Troubleshooting Quick Fixes

- “Missing module ‘websockets’”: `C:\Marven\.venv311\Scripts\python.exe -m pip install websockets`
- “Missing ‘langchain_ollama’”: install the text-only deps (section 2).
- PowerShell can’t load Activate.ps1: use cmd’s `activate.bat` or run Python directly.
- Wrong venv path: ensure there’s a backslash after `Marven` in `C:\Marven\.venv311\...`.


## 8) Run React App (marven-react-app)

Prereqs
- Install Node.js LTS (v18 or v20). Verify with `node -v` and `npm -v`.

Install dependencies (first time or after updates)
- `cd C:\Marven\marven-react-app`
- `npm ci`  (or `npm install`)

Backend must be running (API on 8000)
- In a separate terminal, from `C:\Marven` run one of:
  - `python server.py`
  - or: `C:\Marven\.venv311\Scripts\python.exe C:\Marven\server.py`
- Verify: `Invoke-RestMethod http://127.0.0.1:8000/api/local/capabilities | Select-Object -Expand Content`

Start development server
- `npm start`
- Opens `http://localhost:3000`. If 3000 is busy, accept the prompt to use a different port.
- Optional: pin API base for this session
  - PowerShell: `$env:REACT_APP_API_BASE='http://127.0.0.1:8000'; npm start`

Build production bundle
- `npm run build`
- Serve locally: `npx serve -s build` and open the printed URL.

Notes
- Client code defaults to `http://127.0.0.1:8000` (see `src/lib/marvenClient.js`). You can override with `REACT_APP_API_BASE`.
- `package.json` also declares a CRA `proxy` to `http://localhost:8000`.

## 8.1) Chat Bar: Self‑aware + Brain Tour

Where to see them
- Open the React app at `http://localhost:3000` after starting the backend (`python server.py`).
- In the main chat page, look at the input bar row (bottom of the chat):
  - The `Self-aware` checkbox is to the right of `Auto-apply code fences` (enables reflective responses).
  - The `Play Brain Tour` button sits next to `Send`/`Stop` and toggles sections. It plays an audio tour explaining Marven’s “galaxy brain”.

Feature references in code
- `Self-aware` checkbox in `marven-react-app/src/components/MarvenChat.jsx:200`.
- `Play Brain Tour` button in `marven-react-app/src/components/MarvenChat.jsx:236`.

How the Self‑aware toggle works
- When checked, the client includes `selfAware: true` in requests, which the backend uses to bias responses toward self‑reflection and context awareness.
- You can flip it any time before sending a message; it applies to the next request.

How the Brain Tour works (offline + online)
- Clicking `Play Brain Tour` requests metadata from `GET /api/brain/meta` and then fetches the audio from `GET /api/brain/tour?variant=full` (or `compact`).
- If offline audio files exist under `assets/brain/`, they are streamed directly.
- If offline audio is not present but an online base is configured, the server returns a redirect JSON; the client follows it automatically and plays the hosted MP3.

Prepare offline audio (optional)
- Place these files under `C:\Marven\assets\brain\`:
  - `marven_galaxy_brain_full_offline_tour_v2.mp3`
  - `marven_galaxy_brain_full_compact.mp3`

Enable online version of the tour
- Host the same MP3 filenames at a public URL (e.g., `https://yourcdn.example.com/marven-brain/`).
- Set an environment variable before launching the backend so the API can reference it:
  - PowerShell (session only): `$env:MARVEN_BRAIN_BASE_URL='https://yourcdn.example.com/marven-brain' ; python server.py`
  - Command Prompt: `set MARVEN_BRAIN_BASE_URL=https://yourcdn.example.com/marven-brain` then `python server.py`
- Verify:
  - `Invoke-RestMethod http://127.0.0.1:8000/api/brain/meta` should show `{ online: true }`.
  - In the React app, click `Play Brain Tour` and the audio should play from the hosted URL.

If you don’t see the toggle or button
- Make sure you’re in the React UI, not the voice prototype client or CLI.
- Backend must be running at `http://127.0.0.1:8000` and the React app at `http://localhost:3000`.
- Resize the window if your viewport is very narrow; the chat bar wraps controls.

Troubleshooting (React + API)
- "TypeError: Failed to fetch": backend not running or wrong port. Start `server.py` first and confirm `http://127.0.0.1:8000` responds.
- 500 from `/api/respond`: check the server console for a traceback. Ensure Ollama is running and the requested model exists (e.g. run `ollama run mistral` once).
- CORS/mixed content: serve both sites over HTTP locally; avoid mixing HTTPS front-end with HTTP API.
- Bad request shape: server expects `sessionId` (not `session`) for chat routes. The bundled client uses `sessionId` already.
- Firewall: allow Python on port 8000 in Windows Defender Firewall if prompted.

## 9) Quick Commands (CLI)

From PowerShell, use the helper wrapper:
- `C:\Marven\marven.ps1 say "summarize this week"`
- `C:\Marven\marven.ps1 remember "I prefer concise bullet answers."`
- `C:\Marven\marven.ps1 remember-mm "MetaMirror: protect continuity of projects."`
- `C:\Marven\marven.ps1 search "near-term LLM roadmap"`

Under the hood this calls `C:\Marven\marven_cli.py` in the 3.11 venv.

## 10) New Features: EQ + MetaMirror

- Emotional Intelligence (EQ Matrix): the backend analyzes tone and subtly adjusts guidance for empathy and clarity. You don’t need to do anything—every reply benefits automatically.
- Memory commands in any chat box:
  - `remember: <text>` stores an episodic memory for continuity.
  - `remember_mm: <text>` stores in the MetaMirror memory layer and indexes for retrieval.
- Command Bar: in the React chat, use the small dropdown next to Attach to insert/run quick commands like `remember:`, `remember_mm:`, `web:search`, and file helpers.
- MetaMirror Core Archive: the architecture is encoded in `memory/core_archive/MetaMirror_Core_Archive.md` and indexed into memory at startup.
