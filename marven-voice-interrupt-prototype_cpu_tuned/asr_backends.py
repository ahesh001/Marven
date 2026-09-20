import os, asyncio, numpy as np
from pathlib import Path
from typing import AsyncIterator, Optional, Dict, Any

class BaseASR:
    def __init__(self, config: Dict[str, Any], sample_rate: int = 16000):
        self.config = config
        self.sample_rate = sample_rate

    async def transcribe_stream(self, pcm_iter: AsyncIterator[bytes]) -> str:
        """Consume a finite iterator of PCM bytes and return a transcript string."""
        raise NotImplementedError

# ---------- VOSK ----------
class VoskASR(BaseASR):
    def __init__(self, config, sample_rate=16000):
        super().__init__(config, sample_rate)
        try:
            from vosk import Model, KaldiRecognizer
        except ImportError as exc:
            raise RuntimeError("Vosk backend selected but package 'vosk' is missing. Run `pip install vosk` or switch `config.yaml: asr_backend`.") from exc
        model_path_cfg = config["vosk"].get("model_path")
        base_dir = Path(config.get("_base_dir", "."))
        model_path = Path(model_path_cfg) if model_path_cfg else None
        if model_path is None:
            raise RuntimeError("Vosk backend requires `vosk.model_path` in config.yaml")
        if not model_path.is_absolute():
            model_path = (base_dir / model_path).resolve()
        if not model_path.is_dir():
            raise RuntimeError(f"Vosk model not found at {model_path}")
        self.model = Model(str(model_path))
        self.recognizer = KaldiRecognizer(self.model, sample_rate)
        self.recognizer.SetWords(True)

    async def transcribe_stream(self, pcm_iter: AsyncIterator[bytes]) -> str:
        rec = self.recognizer
        async for chunk in pcm_iter:
            if not chunk:
                continue
            rec.AcceptWaveform(chunk)
        res = rec.FinalResult()
        try:
            import json
            text = json.loads(res).get("text","")
        except Exception:
            text = ""
        return text

# ---------- Faster-Whisper ----------
class FasterWhisperASR(BaseASR):
    def __init__(self, config, sample_rate=16000):
        super().__init__(config, sample_rate)
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("Faster-Whisper backend selected but package 'faster-whisper' is missing. Install it or change `asr_backend`.") from exc
        m = config.get("faster_whisper", {})
        model_size = m.get("model_size", "small")
        device = m.get("device", "auto")
        compute_type = m.get("compute_type", "auto")
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)

    async def transcribe_stream(self, pcm_iter: AsyncIterator[bytes]) -> str:
        # Collect to a single numpy array (simple prototype). For true streaming,
        # switch to incremental decoding with VAD-based chunking.
        bufs = []
        async for chunk in pcm_iter:
            bufs.append(chunk)
        if not bufs:
            return ""
        import numpy as np
        pcm = b"".join(bufs)
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, info = self.model.transcribe(audio, language="en")
        text = "".join([seg.text for seg in segments]) if segments else ""
        return text.strip()

# ---------- Whisper.cpp (stub) ----------
class WhisperCppASR(BaseASR):
    """Assumes a whisper.cpp HTTP server endpoint that accepts PCM and returns transcript.
       Adjust to your server setup.
    """
    def __init__(self, config, sample_rate=16000):
        super().__init__(config, sample_rate)
        self.url = config["whispercpp"].get("server_url", "http://127.0.0.1:8080/transcribe")

    async def transcribe_stream(self, pcm_iter: AsyncIterator[bytes]) -> str:
        import aiohttp
        # Buffer all (simple path). To stream, use chunked transfer to server if supported.
        bufs = []
        async for chunk in pcm_iter:
            bufs.append(chunk)
        data = b"".join(bufs)
        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()
            form.add_field("audio", data, filename="audio.pcm", content_type="application/octet-stream")
            async with session.post(self.url, data=form) as resp:
                out = await resp.json()
                return out.get("text","").strip()
        
def make_asr(config: Dict[str, Any], sample_rate: int):
    backend = config.get("asr_backend","vosk").lower()
    if backend == "vosk":
        return VoskASR(config, sample_rate)
    elif backend == "faster_whisper":
        return FasterWhisperASR(config, sample_rate)
    elif backend == "whispercpp":
        return WhisperCppASR(config, sample_rate)
    else:
        raise ValueError(f"Unknown asr_backend: {backend}")
