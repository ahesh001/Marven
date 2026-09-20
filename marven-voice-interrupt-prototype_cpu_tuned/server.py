import asyncio, websockets, json, yaml
import sys
from pathlib import Path

import webrtcvad
from typing import AsyncIterator
from asr_backends import make_asr
from tts_backend import make_tts

# -------- Config --------
CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
if not CONFIG_PATH.exists():
    raise FileNotFoundError(f"Config file not found at {CONFIG_PATH}")
with CONFIG_PATH.open("r", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

CFG.setdefault("_base_dir", CONFIG_PATH.parent.as_posix())

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import marven  # type: ignore
except Exception as exc:
    marven = None
    print(f"[voice] Warning: could not import marven core: {exc}")

MARVEN_CFG = CFG.get("marven", {})
MARVEN_SESSION_ID = MARVEN_CFG.get("session_id", "voice")
MARVEN_MODEL = MARVEN_CFG.get("model")
MARVEN_PROMPT_PREFIX = MARVEN_CFG.get("prompt_prefix", "")
MARVEN_ENABLED = bool(MARVEN_CFG.get("enabled", True)) and marven is not None

SAMPLE_RATE = CFG.get("sample_rate", 16000)
FRAME_MS = 30
BYTES_PER_FRAME = int(SAMPLE_RATE * FRAME_MS / 1000 * 2)

VAD_MODE = CFG.get("vad_mode", 2)  # 0..3
vad = webrtcvad.Vad(VAD_MODE)

# Shared state
is_user_speaking = False
is_marven_speaking = False

# Initialize backends
ASR = make_asr(CFG, SAMPLE_RATE)
TTS = make_tts(CFG, SAMPLE_RATE)
TTS_SUPPORTS_AUDIO = hasattr(TTS, "respond_to_stream")
TTS_WAIT_FOR_SILENCE_SEC = max(0.0, float(CFG.get('tts', {}).get('wait_for_silence_ms', 600)) / 1000.0)

# ---- Helpers ----
def vad_is_speech(pcm_bytes: bytes) -> bool:
    try:
        return vad.is_speech(pcm_bytes, SAMPLE_RATE)
    except Exception:
        return False

async def pcm_collector(queue: asyncio.Queue) -> AsyncIterator[bytes]:
    """Yield PCM frames previously enqueued by the websocket handler until a sentinel is received."""
    while True:
        item = await queue.get()
        if item is None:  # sentinel
            break
        yield item

def fallback_reply(text: str) -> str:
    lower = text.lower()
    if "hello" in lower or "hi" in lower:
        return "Quick note: I can speak over you politely if something urgent comes up."
    if "test" in lower:
        return "Test received. I will try a short reply so you can interrupt me."
    return "Noted. Here is a brief thought: We can enable natural turn-taking with real-time voice activity detection."


async def dialog_manager(transcript: str) -> str:
    text = (transcript or "").strip()
    if not text:
        return ""
    if not MARVEN_ENABLED:
        return fallback_reply(text)

    loop = asyncio.get_running_loop()
    input_text = f"{MARVEN_PROMPT_PREFIX}{text}" if MARVEN_PROMPT_PREFIX else text

    def _call():
        return marven.marven_response(input_text, session_id=MARVEN_SESSION_ID, model=MARVEN_MODEL)

    try:
        reply = await loop.run_in_executor(None, _call)
    except Exception as exc:
        print(f"[voice] marven_response error: {exc}")
        return fallback_reply(text)

    if not isinstance(reply, str):
        reply = str(reply)

    return reply.strip()

# ---- WebSocket Handler ----
async def handler(ws, path=None):
    global is_user_speaking, is_marven_speaking
    print("Client connected")
    buffer = bytearray()
    # For ASR collection
    asr_queue: asyncio.Queue = asyncio.Queue()
    # VAD smoothing (hangover)
    non_speech_count = 0
    HANGOVER_FRAMES = 8  # ~240 ms at 30 ms frames
    tts_preempted = False

    async def send_tts(text: str):
        nonlocal tts_preempted
        global is_marven_speaking
        if not text:
            return

        loop = asyncio.get_running_loop()
        if TTS_WAIT_FOR_SILENCE_SEC > 0.0 and is_user_speaking:
            deadline = loop.time() + TTS_WAIT_FOR_SILENCE_SEC
            check_interval = FRAME_MS / 1000.0
            while is_user_speaking and loop.time() < deadline:
                await asyncio.sleep(check_interval)
            if is_user_speaking:
                print('[voice] Skipping TTS: user is still speaking')
                tts_preempted = False
                return

        tts_preempted = False
        is_marven_speaking = True
        streamed_any = False
        preempted_during_run = False
        try:
            async for pcm_chunk in TTS.synthesize_stream(text):
                if tts_preempted:
                    preempted_during_run = True
                    break
                await ws.send(pcm_chunk)
                streamed_any = True
                if tts_preempted:
                    preempted_during_run = True
                    break
            if not (preempted_during_run or tts_preempted) and streamed_any:
                await ws.send(json.dumps({'type': 'tts_end'}))
        finally:
            tts_preempted = False
            is_marven_speaking = False

    async def send_audio_response():
        nonlocal tts_preempted
        global is_marven_speaking
        tts_preempted = False
        is_marven_speaking = True
        streamed_any = False
        preempted_during_run = False
        try:
            async for pcm_chunk in TTS.respond_to_stream(pcm_collector(asr_queue)):
                if tts_preempted:
                    preempted_during_run = True
                    break
                await ws.send(pcm_chunk)
                streamed_any = True
                if tts_preempted:
                    preempted_during_run = True
                    break
            if not (preempted_during_run or tts_preempted) and streamed_any:
                await ws.send(json.dumps({'type': 'tts_end'}))
        finally:
            tts_preempted = False
            is_marven_speaking = False

    async def asr_and_reply():
        if TTS_SUPPORTS_AUDIO:
            await send_audio_response()
            return

        # Consume queued PCM into ASR, then reply via TTS
        transcript = await ASR.transcribe_stream(pcm_collector(asr_queue))
        print("ASR:", transcript)
        reply = await dialog_manager(transcript)
        if reply:
            await send_tts(reply)

    try:
        asr_task = None

        async for message in ws:
            if isinstance(message, bytes):
                buffer.extend(message)
                while len(buffer) >= BYTES_PER_FRAME:
                    frame = bytes(buffer[:BYTES_PER_FRAME])
                    del buffer[:BYTES_PER_FRAME]

                    speaking = vad_is_speech(frame)
                    if speaking:
                        non_speech_count = 0
                        if not is_user_speaking:
                            is_user_speaking = True
                            # If Marven is speaking, preempt immediately
                            if is_marven_speaking and not tts_preempted:
                                tts_preempted = True
                                TTS.preempt()
                                await ws.send(json.dumps({"type":"tts_stop"}))
                    else:
                        # count consecutive non-speech frames
                        non_speech_count += 1
                        if is_user_speaking and non_speech_count >= HANGOVER_FRAMES:
                            is_user_speaking = False

                    # Always feed ASR buffer (for demo we rely on button to finalize)
                    await asr_queue.put(frame)

            else:
                # text JSON
                try:
                    msg = json.loads(message)
                except:
                    continue
                if msg.get("type") == "transcribe_now":
                    # Close current ASR chunk and process
                    await asr_queue.put(None)   # sentinel -> end of stream for this request
                    if asr_task and not asr_task.done():
                        # avoid overlap
                        continue
                    asr_task = asyncio.create_task(asr_and_reply())
                elif msg.get("type") == "cancel_tts":
                    if not tts_preempted:
                        tts_preempted = True
                    TTS.preempt()
                    await ws.send(json.dumps({"type":"tts_stop"}))

    except websockets.exceptions.ConnectionClosed:
        print("Client disconnected")
    finally:
        # ensure queue is closed
        try:
            await asr_queue.put(None)
        except:
            pass

async def main():
    async with websockets.serve(handler, "localhost", 8765, max_size=2**23):
        print("Server started on ws://localhost:8765")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())

