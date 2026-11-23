"""Speech: transcribe the user's voice notes and speak replies back.

- Speech-to-text uses faster-whisper (runs locally, free). Optional: install
  requirements-voice.txt. Without it the bot asks the user to type instead.
- Text-to-speech uses edge-tts (free neural voices, no API key) and ffmpeg to
  convert to the OGG/Opus format Telegram shows as a voice message.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
from pathlib import Path

from .telegram import strip_html

log = logging.getLogger(__name__)

MAX_TTS_CHARS = 2500


class VoiceUnavailable(RuntimeError):
    pass


class Voice:
    def __init__(self, tts_voice: str, whisper_model: str):
        self.tts_voice = tts_voice
        self.whisper_model_name = whisper_model
        self._whisper = None
        self._whisper_lock = asyncio.Lock()

    # ---------- speech to text ----------
    async def _get_whisper(self):
        async with self._whisper_lock:
            if self._whisper is None:
                try:
                    from faster_whisper import WhisperModel  # type: ignore
                except ImportError as e:
                    raise VoiceUnavailable(
                        "Voice input needs faster-whisper: pip install -r requirements-voice.txt"
                    ) from e
                log.info("Loading Whisper model %r (first use)…", self.whisper_model_name)
                self._whisper = await asyncio.to_thread(
                    WhisperModel, self.whisper_model_name, device="cpu", compute_type="int8"
                )
        return self._whisper

    async def transcribe(self, audio: bytes, suffix: str = ".ogg") -> str:
        model = await self._get_whisper()

        def _run() -> str:
            with tempfile.NamedTemporaryFile(suffix=suffix) as f:
                f.write(audio)
                f.flush()
                segments, _info = model.transcribe(f.name, vad_filter=True)
                return " ".join(s.text.strip() for s in segments).strip()

        return await asyncio.to_thread(_run)

    # ---------- text to speech ----------
    @staticmethod
    def speakable(text: str) -> str:
        text = strip_html(text)
        text = re.sub(r"https?://\S+", "", text)       # don't read URLs aloud
        text = re.sub(r"[*_`#>|]+", "", text)
        text = re.sub(r"\n{2,}", ". ", text)
        return text.strip()[:MAX_TTS_CHARS]

    async def synthesize(self, text: str) -> tuple[bytes, str, str]:
        """Returns (audio_bytes, filename, mime). OGG/Opus when ffmpeg is available, else MP3."""
        try:
            import edge_tts  # type: ignore
        except ImportError as e:
            raise VoiceUnavailable("Voice replies need edge-tts: pip install edge-tts") from e

        spoken = self.speakable(text)
        if not spoken:
            raise VoiceUnavailable("Nothing to say aloud.")
        with tempfile.TemporaryDirectory() as tmp:
            mp3 = Path(tmp) / "reply.mp3"
            await edge_tts.Communicate(spoken, self.tts_voice).save(str(mp3))
            if shutil.which("ffmpeg"):
                ogg = Path(tmp) / "reply.ogg"
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
                    "-c:a", "libopus", "-b:a", "48k", "-ac", "1", str(ogg),
                )
                if await proc.wait() == 0 and ogg.exists():
                    return ogg.read_bytes(), "reply.ogg", "audio/ogg"
                log.warning("ffmpeg conversion failed; sending MP3")
            return mp3.read_bytes(), "reply.mp3", "audio/mpeg"
