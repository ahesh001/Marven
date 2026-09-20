# Marven Voice — Real‑Time Local ASR + Coqui TTS with Interrupts

This prototype gives Marven a **local-first speaking voice** with **real-time ASR** (Vosk or Faster‑Whisper) and **preemptible Coqui TTS** so Marven can **interject** or **yield** instantly when a human starts talking.

Two ASR modes are supported out-of-the-box:
- **Vosk (default)** — easiest to run fully offline.
- **Faster‑Whisper (optional)** — better accuracy/speed on some machines (CPU/GPU), requires model download.

> **Bonus:** There’s a **Whisper.cpp adapter** stub if you prefer compiling **whisper.cpp** yourself. See the section “Using whisper.cpp” below.

## Quick Start (Local)

1) **Install system deps**
- Python 3.10+ recommended.
- (Windows) `pip install pipwin && pipwin install webrtcvad`
- (Linux/macOS) `brew install portaudio` (mac) or `sudo apt-get install portaudio19-dev` (linux) if you later want live playback/dev; not required for this demo.
- Ensure you have `ffmpeg` available in PATH (optional, good to have).

2) **Create & activate venv (recommended)**
```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

3) **Install Python requirements**
```bash
pip install -r requirements.txt
```

4) **Download a Vosk model (offline ASR)**
Choose a small English model (works well for prototyping):
- Go to: https://alphacephei.com/vosk/models
- Download e.g. `vosk-model-small-en-us-0.15.zip`
- Unzip it into `./models/vosk-model-small-en-us-0.15`

Your tree should look like:
```
models/
  vosk-model-small-en-us-0.15/
    am/
    conf/
    graph/
    ...
```
The server resolves this path relative to the project directory, so you can launch `server.py` from anywhere.

5) **(Optional) Faster‑Whisper**
If you want Faster‑Whisper instead of Vosk:
```bash
pip install faster-whisper
```
The model will auto-download the first time (e.g. `small` by default). You can change which model in `config.yaml`.

6) **Run the server**
```bash
python server.py
```
You should see: `Server started on ws://localhost:8765`.

7) **Open the client**
- Open `client.html` in Chrome/Edge.
- Click **Start** to grant mic permissions.
- Speak, then press **“Ping Marven (ASR→Reply)”** to trigger a quick reply path.
- While Marven is speaking, **start talking** — the TTS should **stop immediately** (preemption).

---

## Config

Edit `config.yaml`:
```yaml
asr_backend: vosk       # options: vosk | faster_whisper | whispercpp
sample_rate: 16000
vad_mode: 2             # 0..3 (3 = most aggressive)
marven:
  enabled: true
  session_id: "voice"
  model: null            # leave null to let marven choose its default model
  prompt_prefix: ""      # optional text prepended before sending transcripts to marven
tts:
  provider: coqui
  model: "tts_models/en/vctk/vits"   # good default; change to XTTS_v2 if installed
  speaker: "p225"                    # some models ignore this
  chunk_ms: 160                      # chunk size sent to client (lower = more responsive)
```

When `marven.enabled` is true the server imports the root `marven.py` brain and forwards each transcript to `marven.marven_response` using the configured `session_id`. Set it to `false` if you want to keep the lightweight canned replies. Use `prompt_prefix` to inject a short hint (for example `"voice mode:"`) before handing text to Marven. Set `tts.provider: disabled` if you need to run the stack without Coqui installed (audio playback will be skipped).

### Nemotron / NeMo VoiceChat
If you want to use NVIDIA NeMo VoiceChat as the speech backend instead of Coqui, set:
```yaml
tts:
  provider: nemotron
  nemo_dir: "C:/path/to/Speech"
  checkpoint_dir: "C:/path/to/nemotron_checkpoint"
  device: "cuda"              # or "cpu"
  python_executable: "C:/Marven/.venv311/Scripts/python.exe"
  chunk_ms: 160
```

This mode sends captured user audio to NeMo's offline voicechat inference script and streams back the generated response audio. It bypasses the local ASR → Marven text reply path, so it works as a direct audio-based assistant when the NeMo model is available locally.

### Tuning tips
- **VAD**: increase `vad_mode` for noisier rooms; add “hangover” in code if needed.
- **Latency**: reduce `chunk_ms` (e.g., 80–120) to make TTS more responsive to stop.
- **Accuracy**: switch to `faster_whisper` and use model `small` or `medium` if your laptop can handle it.

---

## Using whisper.cpp (optional)

If you prefer **whisper.cpp**:
1) Build it from: https://github.com/ggerganov/whisper.cpp
2) Run a local **server** from that repo (see `examples/server`), or adapt to pipe mode.
3) In `config.yaml`, set `asr_backend: whispercpp` and adjust URL/command in `asr_backends.py`.
   - The included `WhisperCppClient` assumes a local HTTP server endpoint; adapt if you use a different interface.

> This repo includes a **stub** for whisper.cpp integration to keep this prototype self-contained. If you want direct pipe streaming, wire stdin/stdout in `WhisperCppClient.transcribe_stream()`.

---

## Files

- `server.py` — asyncio WebSocket server + VAD + ASR backend + preemptible Coqui TTS.
- `client.html` — microphone capture and low-latency PCM playback in the browser.
- `asr_backends.py` — pluggable ASR backends: Vosk, Faster‑Whisper, and a whisper.cpp stub.
- `tts_backend.py` — Coqui TTS wrapper with chunked streaming and hard **preempt()**.
- `config.yaml` — tweak backends and performance knobs.
- `requirements.txt` — Python deps.

---

## Safety & Etiquette Notes

- **Human always has priority**: when VAD detects user speech, TTS stops instantly.
- **Interjection etiquette**: keep interjections short and purposeful. Consider adding a chime before speaking.
- Add a **hangover** (200–400 ms) if you get rapid start/stop on short silences.
- This is a **local prototype**. For public demos, add clear **consent** prompts and visible status indicators.

---

## Troubleshooting

- **No TTS voice / model not found**: The first run may download a model; ensure you have disk space and a stable connection.
- **Vosk errors**: Make sure the model path in `config.yaml` exists.
- **Audio choppy**: Increase `tts.chunk_ms` (e.g., 200–240) or increase WebSocket buffer sizes. Close extra Chrome tabs.
- **Faster‑Whisper GPU**: If you have a GPU, install `pip install faster-whisper` and set `compute_type: float16` in the code (already set if CUDA is available).

Enjoy!
