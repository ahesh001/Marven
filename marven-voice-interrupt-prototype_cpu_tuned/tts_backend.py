import asyncio
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import AsyncIterator, Dict, Any, List, Optional
from uuid import uuid4

import numpy as np
import soundfile as sf


class SilentTTS:
    """Optional no-op provider when voice playback is disabled."""

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def preempt(self) -> None:
        pass

    async def synthesize_stream(self, _text: str):
        if False:
            yield b""
        return


class EspeakNGTTS:
    """TTS provider that streams audio synthesized by an installed espeak-ng executable."""

    def __init__(self, config: Dict[str, Any], sample_rate: int = 16000) -> None:
        self.sample_rate = sample_rate
        self.tts_cfg = config.get("tts", {}) if isinstance(config, dict) else {}
        self.chunk_ms = int(self.tts_cfg.get("chunk_ms", 120))
        self.voice = str(self.tts_cfg.get("voice", "en")).strip()
        self.rate_wpm = int(self.tts_cfg.get("rate_wpm", 175))
        self.pitch = int(self.tts_cfg.get("pitch", 50))
        self.volume = int(self.tts_cfg.get("volume", 100))
        self.espeak_sample_rate = int(self.tts_cfg.get("espeak_sample_rate", 22050))
        self.espeak_path = self._resolve_executable(self.tts_cfg.get("espeak_path"))
        self._stop_flag = asyncio.Event()
        self._proc_lock = threading.Lock()
        self._active_proc: Optional[subprocess.Popen] = None

    def _resolve_executable(self, override: Any) -> Path:
        """Locate the espeak-ng binary, preferring an explicit config override."""

        def _unique(paths: List[Path]) -> List[Path]:
            seen = set()
            ordered: List[Path] = []
            for path in paths:
                if not path:
                    continue
                normalized = Path(path)
                key = str(normalized.resolve()) if normalized.exists() else str(normalized)
                if key in seen:
                    continue
                seen.add(key)
                ordered.append(normalized)
            return ordered

        candidates: List[Path] = []
        if override:
            candidates.append(Path(str(override)))

        env_bases = [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramW6432"),
            os.environ.get("ProgramFiles(x86)"),
        ]
        for base in env_bases:
            if base:
                candidates.append(Path(base) / "eSpeak NG" / "espeak-ng.exe")

        # Last-resort fallbacks (common install locations)
        candidates.append(Path("C:/Program Files/eSpeak NG/espeak-ng.exe"))
        candidates.append(Path("C:/Program Files (x86)/eSpeak NG/espeak-ng.exe"))

        for candidate in _unique(candidates):
            if candidate.exists():
                return candidate

        raise RuntimeError(
            "Could not locate espeak-ng executable. Set `tts.espeak_path` in config.yaml "
            "to the full path of espeak-ng.exe (e.g. C:/Program Files/eSpeak NG/espeak-ng.exe)."
        )

    def preempt(self) -> None:
        """Signal any active synthesis loop to stop sending audio chunks."""
        self._stop_flag.set()
        proc: Optional[subprocess.Popen]
        with self._proc_lock:
            proc = self._active_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _render(self, text: str) -> np.ndarray:
        if not text:
            return np.zeros(0, dtype=np.int16)

        cmd = [str(self.espeak_path), "--stdout"]
        if self.voice:
            cmd.extend(["-v", self.voice])
        cmd.extend(["-s", str(self.rate_wpm), "-p", str(self.pitch), "-a", str(self.volume), "--stdin"])

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        with self._proc_lock:
            self._active_proc = proc
        payload = text if text.endswith("\n") else f"{text}\n"
        try:
            stdout, stderr = proc.communicate(input=payload.encode("utf-8"))
        finally:
            with self._proc_lock:
                self._active_proc = None

        if self._stop_flag.is_set():
            return np.zeros(0, dtype=np.int16)

        if proc.returncode != 0:
            stderr_msg = stderr.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(
                f"espeak-ng exited with status {proc.returncode}. "
                f"Stderr: {stderr_msg or '(empty)'}"
            )

        if not stdout:
            return np.zeros(0, dtype=np.int16)

        pcm16 = np.frombuffer(stdout, dtype=np.int16).astype(np.int16)
        if pcm16.size == 0:
            return pcm16

        if self.espeak_sample_rate == self.sample_rate:
            return pcm16.copy()

        float_audio = pcm16.astype(np.float32) / 32767.0
        ratio = self.sample_rate / float(self.espeak_sample_rate)
        if ratio <= 0.0:
            return np.zeros(0, dtype=np.int16)

        x_old = np.arange(len(float_audio), dtype=np.float32)
        x_new = np.arange(0, len(float_audio), 1.0 / ratio, dtype=np.float32)
        if x_new.size == 0:
            return np.zeros(0, dtype=np.int16)
        resampled = np.interp(x_new, x_old, float_audio).astype(np.float32)
        return np.clip(resampled, -1.0, 1.0)
    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield PCM16 chunks for the requested text."""
        self._stop_flag.clear()
        if not text:
            return

        loop = asyncio.get_running_loop()
        try:
            pcm = await loop.run_in_executor(None, self._render, text)
        except Exception:
            self._stop_flag.clear()
            raise

        if pcm.size == 0:
            self._stop_flag.clear()
            return

        # If resampling converted to float (-1..1), bring back to PCM16
        if pcm.dtype != np.int16:
            pcm16 = (np.clip(pcm, -1.0, 1.0) * 32767.0).astype(np.int16)
        else:
            pcm16 = pcm

        samples_per_chunk = max(1, int(self.sample_rate * (self.chunk_ms / 1000.0)))
        idx = 0
        try:
            while idx < len(pcm16):
                if self._stop_flag.is_set():
                    break
                chunk = pcm16[idx : idx + samples_per_chunk]
                idx += samples_per_chunk
                if chunk.size == 0:
                    continue
                yield chunk.tobytes()
                await asyncio.sleep(self.chunk_ms / 1000.0 * 0.6)
        finally:
            self._stop_flag.clear()


class CoquiTTS:
    def __init__(self, config: Dict[str, Any], sample_rate: int = 16000):
        try:
            from TTS.api import TTS  # heavy import, keep lazy
        except ImportError as exc:
            raise RuntimeError(
                "Coqui TTS selected but package `TTS` is missing. Run `pip install TTS` (see "
                "requirements.txt) or set `tts.provider` to disable TTS."
            ) from exc

        self.sample_rate = sample_rate
        self.tts_cfg = config.get("tts", {})
        model_name = self.tts_cfg.get("model", "tts_models/en/vctk/vits")
        self.speaker = self.tts_cfg.get("speaker")
        self.model = TTS(model_name)

        self._stop_flag = asyncio.Event()
        self.chunk_ms = int(self.tts_cfg.get("chunk_ms", 160))

    def preempt(self) -> None:
        """Signal any active synthesis loop to stop."""
        self._stop_flag.set()

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield PCM16 chunks for the requested text, respecting preemption."""
        self._stop_flag.clear()
        if not text:
            return

        import re as _re

        segments = []
        parts = [_s.strip() for _s in _re.split(r"([.!?]+\s+)", text or "")]
        for i in range(0, len(parts), 2):
            seg = parts[i]
            if i + 1 < len(parts):
                seg += parts[i + 1]
            if seg.strip():
                segments.append(seg.strip())

        for sent in segments:
            if self._stop_flag.is_set():
                break
            wav = self.model.tts(text=sent, speaker=self.speaker)
            if wav is None:
                continue
            if isinstance(wav, list):
                wav = np.concatenate([np.asarray(w, dtype=np.float32) for w in wav])
            else:
                wav = np.asarray(wav, dtype=np.float32)

            model_sr = getattr(self.model, "output_sample_rate", 22050)
            audio = wav
            if model_sr != self.sample_rate:
                ratio = self.sample_rate / float(model_sr)
                x_old = np.arange(len(wav))
                x_new = np.arange(0, len(wav), 1 / ratio)
                audio = np.interp(x_new, x_old, wav).astype(np.float32)

            samples_per_chunk = max(1, int(self.sample_rate * (self.chunk_ms / 1000.0)))
            idx = 0
            while idx < len(audio):
                if self._stop_flag.is_set():
                    break
                chunk = audio[idx : idx + samples_per_chunk]
                idx += samples_per_chunk
                pcm16 = (np.clip(chunk, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
                yield pcm16
                await asyncio.sleep(self.chunk_ms / 1000.0 * 0.6)


class NemotronVoiceChat:
    def __init__(self, config: Dict[str, Any], sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.tts_cfg = config.get("tts", {})
        self.nemo_dir = Path(self.tts_cfg.get("nemo_dir", ""))
        self.checkpoint_dir = Path(self.tts_cfg.get("checkpoint_dir", ""))
        self.device = str(self.tts_cfg.get("device", "cuda"))
        self.python_executable = str(self.tts_cfg.get("python_executable", sys.executable))
        self.temp_dir = Path(self.tts_cfg.get("temp_dir", tempfile.gettempdir()))
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self._stop_flag = asyncio.Event()

        if not self.nemo_dir.exists():
            raise RuntimeError(
                "Nemotron VoiceChat selected but `tts.nemo_dir` does not exist. "
                "Set `tts.nemo_dir` to the local NVIDIA NeMo/Speech repository root."
            )
        self.script_path = self.nemo_dir / "examples" / "speechlm2" / "offline_voicechat_infer.py"
        if not self.script_path.exists():
            raise RuntimeError(
                "Could not find offline_voicechat_infer.py in the NVIDIA NeMo repo. "
                "Ensure `tts.nemo_dir` points to the Speech repo and that the "
                "`nemotron-labs-voicechat` branch is checked out."
            )
        if not self.checkpoint_dir.exists():
            raise RuntimeError(
                "Nemotron VoiceChat selected but `tts.checkpoint_dir` does not exist. "
                "Set `tts.checkpoint_dir` to the downloaded Hugging Face checkpoint directory."
            )

    def preempt(self) -> None:
        self._stop_flag.set()

    async def _collect_pcm(self, pcm_iter: AsyncIterator[bytes]) -> bytes:
        bufs = []
        async for chunk in pcm_iter:
            if self._stop_flag.is_set():
                break
            if chunk:
                bufs.append(chunk)
        return b"".join(bufs)

    def _write_wav(self, path: Path, pcm_bytes: bytes) -> None:
        if not pcm_bytes:
            raise RuntimeError("No audio data available for Nemotron inference.")
        audio = np.frombuffer(pcm_bytes, dtype=np.int16)
        sf.write(str(path), audio.astype(np.int16), self.sample_rate, subtype="PCM_16")

    def _read_wav_chunks(self, path: Path, chunk_ms: int = 160) -> AsyncIterator[bytes]:
        audio, sr = sf.read(str(path), dtype="int16")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != self.sample_rate:
            ratio = self.sample_rate / float(sr)
            x_old = np.arange(len(audio), dtype=np.float32)
            x_new = np.arange(0, len(audio), 1.0 / ratio, dtype=np.float32)
            audio = np.interp(x_new, x_old, audio.astype(np.float32)).astype(np.float32)
        if audio.dtype != np.int16:
            audio = np.clip(audio, -1.0, 1.0)
            audio = (audio * 32767.0).astype(np.int16)

        pcm16 = np.asarray(audio, dtype=np.int16)
        samples_per_chunk = max(1, int(self.sample_rate * (chunk_ms / 1000.0)))
        idx = 0
        while idx < len(pcm16):
            if self._stop_flag.is_set():
                break
            chunk = pcm16[idx : idx + samples_per_chunk]
            idx += samples_per_chunk
            yield chunk.tobytes()

    async def respond_to_stream(self, pcm_iter: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        self._stop_flag.clear()
        pcm_bytes = await self._collect_pcm(pcm_iter)
        if not pcm_bytes:
            return

        input_wav = self.temp_dir / f"nemotron_input_{uuid4().hex}.wav"
        output_dir = self.temp_dir / f"nemotron_output_{uuid4().hex}"
        output_dir.mkdir(parents=True, exist_ok=True)

        self._write_wav(input_wav, pcm_bytes)
        cmd = [
            self.python_executable,
            str(self.script_path),
            "--checkpoint",
            str(self.checkpoint_dir),
            "--wav",
            str(input_wav),
            "--output-dir",
            str(output_dir),
            "--device",
            self.device,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"Nemotron VoiceChat inference failed (exit {proc.returncode}).\n"
                f"stdout:\n{stdout.decode('utf-8', errors='ignore')}\n"
                f"stderr:\n{stderr.decode('utf-8', errors='ignore')}"
            )

        output_wav = output_dir / f"{input_wav.stem}_output.wav"
        if not output_wav.exists():
            raise RuntimeError(f"Expected output wav not found: {output_wav}")

        try:
            async for chunk in self._read_wav_chunks(output_wav, chunk_ms=int(self.tts_cfg.get("chunk_ms", 160))):
                if self._stop_flag.is_set():
                    break
                yield chunk
        finally:
            try:
                input_wav.unlink()
                for file in output_dir.iterdir():
                    file.unlink()
                output_dir.rmdir()
            except Exception:
                pass


def make_tts(config: Dict[str, Any], sample_rate: int):
    """Factory helper referenced by the server."""
    tts_cfg = config.get("tts", {}) if isinstance(config, dict) else {}
    provider = (tts_cfg.get("provider") or "coqui").strip().lower()
    if provider in {"disabled", "none", "off"}:
        return SilentTTS()
    if provider in {"espeak", "espeak-ng", "espeakng"}:
        return EspeakNGTTS(config, sample_rate)
    if provider in {"nemotron", "nemotron_voicechat"}:
        return NemotronVoiceChat(config, sample_rate)
    if provider not in {"coqui"}:
        raise ValueError(f"Unknown TTS provider: {provider}")
    return CoquiTTS(config, sample_rate)
