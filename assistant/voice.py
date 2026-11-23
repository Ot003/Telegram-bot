"""Speaking replies out loud.

Text-to-speech uses edge-tts (free neural voices, no API key) and ffmpeg to convert
to the OGG/Opus format Telegram shows as a voice message. (Listening to the user's
voice notes is done by Gemini itself; see Brain.transcribe.)
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
    def __init__(self, tts_voice: str):
        self.tts_voice = tts_voice

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
