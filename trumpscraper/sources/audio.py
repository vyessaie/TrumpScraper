"""Audio source: transcribe speeches / live streams into text.

Uses yt-dlp to pull audio from a URL (YouTube, Rumble, direct media, etc.) and
faster-whisper (preferred) or openai-whisper to transcribe it. Both are optional
dependencies imported lazily — install with ``pip install -r requirements-audio.txt``
and ensure ``ffmpeg`` is on PATH.

This is the "live stream" coverage: point it at a rally/press-conference URL and
the transcript flows through the same analysis pipeline as text posts.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile

from ..models import RawItem

log = logging.getLogger(__name__)


class AudioSource:
    name = "audio"

    def __init__(self, urls: list[str], whisper_model: str = "base"):
        self.urls = urls
        self.whisper_model = whisper_model

    def fetch(self) -> list[RawItem]:
        items: list[RawItem] = []
        for url in self.urls:
            try:
                audio_path, title = self._download_audio(url)
            except Exception as exc:
                log.warning("audio: download failed for %s (%s)", url, exc)
                continue
            try:
                text = self._transcribe(audio_path)
            except Exception as exc:
                log.warning("audio: transcription failed for %s (%s)", url, exc)
                continue
            finally:
                try:
                    os.remove(audio_path)
                except OSError:
                    pass
            if not text.strip():
                continue
            ext_id = hashlib.sha256(url.encode()).hexdigest()[:16]
            items.append(
                RawItem(
                    external_id=ext_id,
                    text=text.strip(),
                    source=self.name,
                    url=url,
                    author=title or "Donald Trump",
                )
            )
        return items

    def _download_audio(self, url: str) -> tuple[str, str]:
        import yt_dlp  # lazy

        tmpdir = tempfile.mkdtemp(prefix="trumpscraper_")
        outtmpl = os.path.join(tmpdir, "%(id)s.%(ext)s")
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "quiet": True,
            "noprogress": True,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}
            ],
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            base = os.path.join(tmpdir, info["id"])
            audio_path = base + ".mp3"
            return audio_path, info.get("title", "")

    def _transcribe(self, audio_path: str) -> str:
        # Prefer faster-whisper; fall back to openai-whisper.
        try:
            from faster_whisper import WhisperModel

            model = WhisperModel(self.whisper_model, device="cpu", compute_type="int8")
            segments, _ = model.transcribe(audio_path)
            return " ".join(seg.text for seg in segments)
        except ImportError:
            pass

        import whisper  # openai-whisper

        model = whisper.load_model(self.whisper_model)
        result = model.transcribe(audio_path)
        return result.get("text", "")
