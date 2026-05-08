"""
Audio Listener — Two modes:

  1. AudioListener        : YOUR microphone          (Ctrl+Shift+A)
  2. SystemAudioListener  : INTERVIEWER system audio (Ctrl+Shift+S)

FIXES applied (original):
  - np.fromstring (removed in numpy 2.x) → np.frombuffer
  - COM CoInitialize() for Error 0x800401f0
  - Robust float32 → int16 conversion that handles any array shape

PERF FIXES:
  - pause_threshold     : 1.2 s → 0.7 s  (mic stops waiting sooner after speech ends)
  - non_speaking_duration: 0.8 s → 0.4 s  (tighter silence detection)
  - SILENCE_SECONDS     : 1.8 s → 1.0 s  (system audio flushes faster after interviewer pauses)
"""

import threading
import time
import io
import wave
import audioop
import numpy as np
import speech_recognition as sr


# ─────────────────────────────────────────────────────────────────────────────
# 1.  MIC LISTENER
# ─────────────────────────────────────────────────────────────────────────────

class AudioListener:
    def __init__(self, on_text_callback=None, device_index=None):
        self.on_text      = on_text_callback
        self.device_index = device_index
        self.recognizer   = sr.Recognizer()
        self._running     = False
        self._thread      = None
        self._lock        = threading.Lock()

        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.energy_threshold         = 300
        # PERF: reduced from 1.2 → 0.7 — speech is sent to STT 0.5 s sooner after the
        #        speaker stops talking.  Values below ~0.5 can clip natural pauses mid-sentence.
        self.recognizer.pause_threshold          = 0.7
        self.recognizer.phrase_threshold         = 0.3
        # PERF: reduced from 0.8 → 0.4 — tighter silence end detection
        self.recognizer.non_speaking_duration    = 0.4

        self.microphone = self._init_microphone()

    def _init_microphone(self):
        try:
            if self.device_index is not None:
                mic = sr.Microphone(device_index=self.device_index)
                with mic as source:
                    pass
                return mic
        except Exception:
            pass
        return sr.Microphone()

    @property
    def is_listening(self):
        return self._running

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread  = threading.Thread(
                target=self._listen_loop, name="MicListenerThread", daemon=True
            )
            self._thread.start()

    def stop(self):
        with self._lock:
            self._running = False

    def _calibrate(self):
        try:
            with self.microphone as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=1.5)
        except Exception:
            pass

    def _listen_loop(self):
        self._calibrate()
        consecutive_errors = 0
        while self._running:
            try:
                with self.microphone as source:
                    try:
                        audio = self.recognizer.listen(
                            source, timeout=4, phrase_time_limit=45
                        )
                    except sr.WaitTimeoutError:
                        consecutive_errors = 0
                        continue
                text = self._transcribe(audio)
                consecutive_errors = 0
                if text and self.on_text:
                    self.on_text(text)
            except OSError as e:
                consecutive_errors += 1
                if self._running and self.on_text:
                    self.on_text(f"[AUDIO ERROR] Microphone error: {e}")
                time.sleep(min(2 * consecutive_errors, 10))
            except Exception as e:
                consecutive_errors += 1
                if self._running and self.on_text:
                    self.on_text(f"[AUDIO ERROR] {str(e)}")
                time.sleep(1)

    def _transcribe(self, audio):
        try:
            text = self.recognizer.recognize_google(
                audio, language="en-US", show_all=False
            )
            return text.strip() if text else None
        except sr.UnknownValueError:
            return None
        except sr.RequestError as e:
            if self.on_text:
                self.on_text(f"[AUDIO ERROR] Speech service unavailable: {e}")
            return None

    @staticmethod
    def list_devices():
        try:
            names = sr.Microphone.list_microphone_names()
            return list(enumerate(names))
        except Exception:
            return []

    @staticmethod
    def find_loopback_device():
        try:
            names = sr.Microphone.list_microphone_names()
            keywords = ["stereo mix", "wave out mix", "loopback",
                        "what u hear", "what you hear"]
            for i, name in enumerate(names):
                if any(kw in name.lower() for kw in keywords):
                    return i
        except Exception:
            pass
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 2.  SYSTEM AUDIO LISTENER
# ─────────────────────────────────────────────────────────────────────────────

class SystemAudioListener:
    """
    Captures system audio (interviewer voice from Zoom/Meet/Teams)
    using the soundcard library with WASAPI loopback.

    Requires:  pip install soundcard numpy pywin32
    """

    SAMPLE_RATE      = 16000
    CHANNELS         = 1
    CHUNK_FRAMES     = 1024
    FORMAT_WIDTH     = 2       # 16-bit PCM

    ENERGY_THRESHOLD = 80
    # PERF: reduced from 1.8 → 1.0 — system audio is flushed to STT 0.8 s sooner
    #        after the interviewer stops speaking.  1.0 s still handles natural sentence
    #        pauses without chopping mid-thought.
    SILENCE_SECONDS  = 1.0
    MIN_SPEECH_SECS  = 0.4
    TARGET_RMS       = 4000

    def __init__(self, on_text_callback=None, device_index=None):
        self.on_text    = on_text_callback
        self._dev_index = device_index
        self._running   = False
        self._thread    = None
        self._lock      = threading.Lock()
        self.recognizer = sr.Recognizer()

    @property
    def is_listening(self):
        return self._running

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread  = threading.Thread(
                target=self._listen_loop,
                name="SystemAudioThread",
                daemon=True
            )
            self._thread.start()

    def stop(self):
        with self._lock:
            self._running = False

    # ── Audio conversion ──────────────────────────────────────────────────────

    @staticmethod
    def float32_to_int16(data) -> bytes:
        """
        Convert soundcard float32 data → int16 PCM bytes.

        soundcard returns a numpy float32 array shaped (frames, channels).
        We flatten it, clip to [-1, 1], scale to int16 range.

        FIX: Uses np.asarray() + .flatten() instead of np.fromstring()
             which was removed in numpy 2.x and caused the binary mode error.
        """
        arr = np.asarray(data, dtype=np.float32)
        arr = arr.flatten()
        arr = np.clip(arr, -1.0, 1.0)
        arr = (arr * 32767.0).astype(np.int16)
        return arr.tobytes()

    @staticmethod
    def normalize_audio(data: bytes, target_rms: int = 4000) -> bytes:
        """Boost quiet audio to a level Google STT can recognize."""
        try:
            rms = audioop.rms(data, SystemAudioListener.FORMAT_WIDTH)
            if rms == 0:
                return data
            gain = min(target_rms / rms, 20.0)
            if gain < 1.0:
                return data
            return audioop.mul(data, SystemAudioListener.FORMAT_WIDTH, gain)
        except Exception:
            return data

    # ── Speaker resolution ────────────────────────────────────────────────────

    def _resolve_speaker(self, sc):
        """Return the soundcard speaker to loopback from."""
        if self._dev_index is not None:
            try:
                all_spk = sc.all_speakers()
                if 0 <= self._dev_index < len(all_spk):
                    return all_spk[self._dev_index]
            except Exception:
                pass
        return sc.default_speaker()

    # ── Main capture loop ─────────────────────────────────────────────────────

    def _listen_loop(self):
        com_initialized = False
        try:
            import pythoncom
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
            com_initialized = True
        except Exception:
            pass

        try:
            import soundcard as sc
        except ImportError:
            if self.on_text:
                self.on_text(
                    "[AUDIO ERROR] soundcard not installed.\n"
                    "Run:  pip install soundcard numpy pywin32"
                )
            return

        consecutive_errors = 0

        while self._running:
            try:
                speaker      = self._resolve_speaker(sc)
                loopback_mic = sc.get_microphone(
                    speaker.id, include_loopback=True
                )

                if self.on_text:
                    self.on_text(
                        f"🔊 System audio active\n"
                        f"Capturing: {speaker.name[:50]}\n"
                        f"Listening for interviewer speech..."
                    )

                frames        = []
                silence_count = 0
                speaking      = False
                silence_limit = int(
                    self.SAMPLE_RATE / self.CHUNK_FRAMES * self.SILENCE_SECONDS
                )
                min_frames    = int(
                    self.SAMPLE_RATE / self.CHUNK_FRAMES * self.MIN_SPEECH_SECS
                )

                with loopback_mic.recorder(
                    samplerate=self.SAMPLE_RATE,
                    channels=self.CHANNELS
                ) as recorder:

                    while self._running:
                        chunk = recorder.record(numframes=self.CHUNK_FRAMES)
                        pcm   = self.float32_to_int16(chunk)

                        try:
                            energy = audioop.rms(pcm, self.FORMAT_WIDTH)
                        except Exception:
                            energy = 0

                        if energy > self.ENERGY_THRESHOLD:
                            speaking      = True
                            silence_count = 0
                            frames.append(pcm)
                        elif speaking:
                            frames.append(pcm)
                            silence_count += 1
                            if silence_count >= silence_limit:
                                if len(frames) >= min_frames:
                                    self._transcribe_frames(frames)
                                frames        = []
                                speaking      = False
                                silence_count = 0

                consecutive_errors = 0

            except Exception as e:
                consecutive_errors += 1
                if self._running and self.on_text:
                    self.on_text(f"[AUDIO ERROR] System audio: {e}")
                time.sleep(min(2 * consecutive_errors, 10))

        if com_initialized:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass

    # ── Transcription ─────────────────────────────────────────────────────────

    def _transcribe_frames(self, frames: list):
        try:
            raw        = b"".join(frames)
            normalized = self.normalize_audio(raw, self.TARGET_RMS)

            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(self.CHANNELS)
                wf.setsampwidth(self.FORMAT_WIDTH)
                wf.setframerate(self.SAMPLE_RATE)
                wf.writeframes(normalized)
            buf.seek(0)

            audio_data = sr.AudioData(
                buf.read(), self.SAMPLE_RATE, self.FORMAT_WIDTH
            )
            text = self.recognizer.recognize_google(
                audio_data, language="en-US", show_all=False
            )
            if text and self.on_text:
                self.on_text(text.strip())

        except sr.UnknownValueError:
            pass
        except sr.RequestError as e:
            if self.on_text:
                self.on_text(f"[AUDIO ERROR] Speech API: {e}")
        except Exception as e:
            if self.on_text:
                self.on_text(f"[AUDIO ERROR] Transcription: {e}")

    # ── Static helpers ────────────────────────────────────────────────────────

    @staticmethod
    def is_available() -> bool:
        try:
            import soundcard  # noqa
            import numpy      # noqa
            return True
        except ImportError:
            return False

    @staticmethod
    def list_system_devices() -> list:
        """Returns list of (index, name, is_default, sample_rate)."""
        com_initialized = False
        try:
            import pythoncom
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
            com_initialized = True
        except Exception:
            pass

        devices = []
        try:
            import soundcard as sc
            default_name = ""
            try:
                default_name = sc.default_speaker().name
            except Exception:
                pass
            for i, speaker in enumerate(sc.all_speakers()):
                name       = speaker.name or f"Speaker {i}"
                is_default = (name == default_name)
                devices.append((i, name, is_default, 48000))
        except Exception:
            pass

        if com_initialized:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass

        return devices