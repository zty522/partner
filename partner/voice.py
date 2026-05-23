"""Voice Processing Pipeline for Partner messaging integration.

Handles the full voice message lifecycle:
  1. Receive voice from platform (SILK/AMR format)
  2. Convert to WAV (via ffmpeg or pilk)
  3. Speech-to-text (STT) via FunASR, Whisper, or cloud APIs
  4. Feed text to Partner's conversation engine
  5. Generate reply audio via TTS (edge-tts, CosyVoice, etc.)
  6. Convert reply to platform-required format (SILK for WeChat)
  7. Send voice message back

Supported STT engines:
  - FunASR (default, Chinese-optimized, free, offline)
  - Whisper (OpenAI, multilingual, offline)
  - whisper-api (OpenAI API, fast, paid)

Supported TTS engines:
  - edge-tts (default, free, high quality Chinese)
  - CosyVoice (Alibaba, open source, very natural)

Usage:
    from partner.voice import VoiceProcessor

    vp = VoiceProcessor()
    text = vp.transcribe("/tmp/voice.silk")
    audio_path = vp.synthesize("你好！", "/tmp/reply.mp3")
"""

import os
import logging
import tempfile
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class VoiceConfig:
    """Voice processing configuration."""
    stt_engine: str = "funasr"       # funasr, whisper, whisper-api
    tts_engine: str = "edge-tts"     # edge-tts, cosyvoice
    whisper_model: str = "base"       # base, small, medium, large
    tts_voice: str = "zh-CN-XiaoxiaoNeural"  # edge-tts voice name
    tts_rate: str = "+0%"            # speaking rate adjustment
    sample_rate: int = 16000         # audio sample rate
    temp_dir: str = ""               # temp directory for audio files


class VoiceProcessor:
    """Full voice processing pipeline.

    Handles conversion, transcription, and synthesis for voice messages.
    """

    def __init__(self, config: VoiceConfig = None):
        self.config = config or VoiceConfig()
        if not self.config.temp_dir:
            self.config.temp_dir = os.path.join(tempfile.gettempdir(), "partner_voice")
        os.makedirs(self.config.temp_dir, exist_ok=True)

        # Lazy-loaded models
        self._stt_model = None
        self._tts_model = None

    # ── Main Pipeline ────────────────────────────────────────────

    def transcribe(self, audio_path: str, source_format: str = "auto") -> str:
        """Transcribe audio file to text.

        Args:
            audio_path: Path to audio file
            source_format: "silk", "amr", "wav", "mp3", or "auto" (detect from extension)

        Returns:
            Transcribed text, or error message prefixed with [STT error]
        """
        if not os.path.exists(audio_path):
            return f"[STT error: file not found: {audio_path}]"

        try:
            # Step 1: Convert to WAV if needed
            wav_path = self._ensure_wav(audio_path, source_format)
            if not wav_path:
                return "[STT error: failed to convert audio to WAV]"

            # Step 2: Transcribe
            engine = self.config.stt_engine
            if engine == "funasr":
                text = self._stt_funasr(wav_path)
            elif engine == "whisper":
                text = self._stt_whisper(wav_path)
            elif engine == "whisper-api":
                text = self._stt_whisper_api(wav_path)
            else:
                text = f"[STT error: unknown engine: {engine}]"

            # Cleanup temp WAV
            if wav_path != audio_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

            return text

        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return f"[STT error: {e}]"

    def synthesize(self, text: str, output_path: str = None) -> str:
        """Synthesize text to speech audio.

        Args:
            text: Text to convert to speech
            output_path: Output file path (default: auto-generated in temp_dir)

        Returns:
            Path to generated audio file, or error message prefixed with [TTS error]
        """
        if not text.strip():
            return "[TTS error: empty text]"

        try:
            engine = self.config.tts_engine
            if engine == "edge-tts":
                return self._tts_edge_tts(text, output_path)
            elif engine == "cosyvoice":
                return self._tts_cosyvoice(text, output_path)
            else:
                return f"[TTS error: unknown engine: {engine}]"
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            return f"[TTS error: {e}]"

    # ── Audio Conversion ─────────────────────────────────────────

    def _ensure_wav(self, audio_path: str, source_format: str = "auto") -> Optional[str]:
        """Convert audio to WAV format (16kHz, mono, PCM).

        Handles SILK, AMR, MP3, and other formats.
        Returns path to WAV file (may be the same as input if already WAV).
        """
        if source_format == "auto":
            ext = Path(audio_path).suffix.lower()
            if ext == ".wav":
                return audio_path
            source_format = ext.lstrip(".")

        if source_format in ("wav",):
            return audio_path

        if source_format == "silk":
            return self._silk_to_wav(audio_path)
        elif source_format == "amr":
            return self._amr_to_wav(audio_path)
        else:
            # Try ffmpeg for other formats
            return self._ffmpeg_to_wav(audio_path)

    def _silk_to_wav(self, silk_path: str) -> Optional[str]:
        """Convert SILK audio to WAV.

        Uses pilk (Python SILK decoder) or silk-v3-decoder.
        """
        wav_path = silk_path.rsplit(".", 1)[0] + ".wav"

        # Try pilk first
        try:
            import pilk

            pcm_path = silk_path.rsplit(".", 1)[0] + ".pcm"
            pilk.decode(silk_path, pcm_path)

            # Convert PCM to WAV using ffmpeg
            cmd = [
                "ffmpeg", "-y",
                "-f", "s16le", "-ar", "24000", "-ac", "1",
                "-i", pcm_path,
                "-ar", str(self.config.sample_rate), "-ac", "1",
                wav_path,
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode != 0:
                logger.error(f"ffmpeg PCM→WAV failed: {result.stderr.decode()}")
                return None

            # Cleanup
            if os.path.exists(pcm_path):
                os.remove(pcm_path)

            return wav_path
        except ImportError:
            pass

        # Fallback: try silk-v3-decoder CLI
        try:
            cmd = ["silk-v3-decoder", silk_path, wav_path]
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode == 0:
                return wav_path
        except FileNotFoundError:
            pass

        # Fallback: try decoder binary
        decoder_paths = [
            "/usr/local/bin/silk-decoder",
            os.path.expanduser("~/silk/decoder"),
        ]
        for decoder in decoder_paths:
            if os.path.exists(decoder):
                try:
                    pcm_path = silk_path.rsplit(".", 1)[0] + ".pcm"
                    cmd = [decoder, silk_path, pcm_path, "-rate", "24000"]
                    subprocess.run(cmd, capture_output=True, timeout=30)

                    cmd = [
                        "ffmpeg", "-y",
                        "-f", "s16le", "-ar", "24000", "-ac", "1",
                        "-i", pcm_path,
                        "-ar", str(self.config.sample_rate), "-ac", "1",
                        wav_path,
                    ]
                    subprocess.run(cmd, capture_output=True, timeout=30)
                    if os.path.exists(wav_path):
                        return wav_path
                except Exception:
                    pass

        logger.error("No SILK decoder available. Install pilk: pip install pilk")
        return None

    def _amr_to_wav(self, amr_path: str) -> Optional[str]:
        """Convert AMR audio to WAV using ffmpeg."""
        wav_path = amr_path.rsplit(".", 1)[0] + ".wav"
        try:
            cmd = [
                "ffmpeg", "-y", "-i", amr_path,
                "-ar", str(self.config.sample_rate), "-ac", "1",
                wav_path,
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode == 0:
                return wav_path
            logger.error(f"ffmpeg AMR→WAV failed: {result.stderr.decode()}")
        except Exception as e:
            logger.error(f"AMR conversion error: {e}")
        return None

    def _ffmpeg_to_wav(self, input_path: str) -> Optional[str]:
        """Convert any audio format to WAV using ffmpeg."""
        wav_path = input_path.rsplit(".", 1)[0] + ".wav"
        try:
            cmd = [
                "ffmpeg", "-y", "-i", input_path,
                "-ar", str(self.config.sample_rate), "-ac", "1",
                "-acodec", "pcm_s16le",
                wav_path,
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            if result.returncode == 0:
                return wav_path
            logger.error(f"ffmpeg conversion failed: {result.stderr.decode()}")
        except Exception as e:
            logger.error(f"ffmpeg error: {e}")
        return None

    # ── STT Engines ──────────────────────────────────────────────

    def _stt_funasr(self, wav_path: str) -> str:
        """Transcribe using FunASR (Alibaba's open-source ASR).

        Uses paraformer-zh model, optimized for Chinese.
        First call downloads the model (~1GB).
        """
        try:
            if self._stt_model is None:
                from funasr import AutoModel
                logger.info("Loading FunASR model (first call may take a while)...")
                self._stt_model = AutoModel(model="paraformer-zh")
                logger.info("FunASR model loaded")

            result = self._stt_model.generate(input=wav_path)
            if result and len(result) > 0:
                text = result[0].get("text", "")
                logger.info(f"FunASR transcribed: {text[:100]}")
                return text
            return ""
        except ImportError:
            logger.warning("FunASR not installed. Falling back to whisper.")
            return self._stt_whisper(wav_path)
        except Exception as e:
            logger.error(f"FunASR error: {e}")
            return f"[STT error: FunASR: {e}]"

    def _stt_whisper(self, wav_path: str) -> str:
        """Transcribe using OpenAI Whisper (local).

        Uses the 'base' model by default (fast) or 'medium'/'large' (accurate).
        """
        try:
            if self._stt_model is None:
                import whisper
                model_name = self.config.whisper_model
                logger.info(f"Loading Whisper model '{model_name}'...")
                self._stt_model = whisper.load_model(model_name)
                logger.info("Whisper model loaded")

            result = self._stt_model.transcribe(wav_path, language="zh")
            text = result.get("text", "")
            logger.info(f"Whisper transcribed: {text[:100]}")
            return text
        except ImportError:
            logger.warning("Whisper not installed. Install with: pip install openai-whisper")
            return "[STT error: whisper not installed]"
        except Exception as e:
            logger.error(f"Whisper error: {e}")
            return f"[STT error: Whisper: {e}]"

    def _stt_whisper_api(self, wav_path: str) -> str:
        """Transcribe using OpenAI Whisper API (cloud).

        Requires OPENAI_API_KEY environment variable.
        """
        try:
            import openai
            client = openai.OpenAI()

            with open(wav_path, "rb") as f:
                result = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    language="zh",
                )
            text = result.text
            logger.info(f"Whisper API transcribed: {text[:100]}")
            return text
        except ImportError:
            return "[STT error: openai package not installed]"
        except Exception as e:
            logger.error(f"Whisper API error: {e}")
            return f"[STT error: Whisper API: {e}]"

    # ── TTS Engines ──────────────────────────────────────────────

    def _tts_edge_tts(self, text: str, output_path: str = None) -> str:
        """Synthesize speech using edge-tts (Microsoft Edge's TTS).

        Free, high quality, supports many voices and languages.
        """
        if not output_path:
            import hashlib
            text_hash = hashlib.md5(text.encode()).hexdigest()[:8]
            output_path = os.path.join(self.config.temp_dir, f"tts_{text_hash}.mp3")

        try:
            import edge_tts
            import asyncio

            async def generate():
                communicate = edge_tts.Communicate(
                    text,
                    self.config.tts_voice,
                    rate=self.config.tts_rate,
                )
                await communicate.save(output_path)

            asyncio.run(generate())

            if os.path.exists(output_path):
                logger.info(f"edge-tts generated: {output_path} ({os.path.getsize(output_path)} bytes)")
                return output_path
            return "[TTS error: edge-tts produced no output]"
        except ImportError:
            logger.warning("edge-tts not installed. Install with: pip install edge-tts")
            return "[TTS error: edge-tts not installed]"
        except Exception as e:
            logger.error(f"edge-tts error: {e}")
            return f"[TTS error: edge-tts: {e}]"

    def _tts_cosyvoice(self, text: str, output_path: str = None) -> str:
        """Synthesize speech using CosyVoice (Alibaba).

        Requires CosyVoice to be installed separately.
        See: https://github.com/FunAudioLLM/CosyVoice
        """
        if not output_path:
            import hashlib
            text_hash = hashlib.md5(text.encode()).hexdigest()[:8]
            output_path = os.path.join(self.config.temp_dir, f"cosyvoice_{text_hash}.wav")

        try:
            # Try importing CosyVoice
            from cosyvoice.cli.cosyvoice import CosyVoice
            import torchaudio

            model = CosyVoice("pretrained_models/CosyVoice-300M")
            output = model.inference_sft(text, "中文女")
            torchaudio.save(output_path, output["tts_speech"], 22050)

            if os.path.exists(output_path):
                logger.info(f"CosyVoice generated: {output_path}")
                return output_path
            return "[TTS error: CosyVoice produced no output]"
        except ImportError:
            logger.warning("CosyVoice not available. Falling back to edge-tts.")
            return self._tts_edge_tts(text, output_path)
        except Exception as e:
            logger.error(f"CosyVoice error: {e}")
            return self._tts_edge_tts(text, output_path)

    # ── Utility ──────────────────────────────────────────────────

    def cleanup_temp(self, max_age_hours: int = 24):
        """Remove temp audio files older than max_age_hours."""
        import time
        cutoff = time.time() - max_age_hours * 3600
        count = 0
        try:
            for f in os.listdir(self.config.temp_dir):
                fpath = os.path.join(self.config.temp_dir, f)
                if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
                    count += 1
            if count > 0:
                logger.info(f"Cleaned up {count} temp audio files")
        except Exception as e:
            logger.warning(f"Temp cleanup error: {e}")

    def get_available_engines(self) -> dict:
        """Check which STT and TTS engines are available."""
        available = {"stt": [], "tts": []}

        # Check STT
        try:
            import funasr
            available["stt"].append("funasr")
        except ImportError:
            pass
        try:
            import whisper
            available["stt"].append("whisper")
        except ImportError:
            pass
        try:
            import openai
            available["stt"].append("whisper-api")
        except ImportError:
            pass

        # Check TTS
        try:
            import edge_tts
            available["tts"].append("edge-tts")
        except ImportError:
            pass

        return available
