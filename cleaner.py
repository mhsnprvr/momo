"""Crowd-laughter removal that stays ahead of playback."""

import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

MODEL_FILENAME = "mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144.ckpt"
MODEL_URLS = [
    "https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/" + MODEL_FILENAME,
    "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/" + MODEL_FILENAME,
]
MODEL_MIN_BYTES = 800 * 1024 * 1024
MODEL_EXPECTED_BYTES = 913_000_000
SAMPLE_RATE = 44100
CHUNK_SECONDS = 20
OVERLAP_SECONDS = 2
HOP_SECONDS = CHUNK_SECONDS - OVERLAP_SECONDS
WINDOWS = sys.platform == "win32"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if WINDOWS else 0

if WINDOWS:
    _LOCAL = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "MoMo"
    SUPPORT_ROOT = _LOCAL
    CACHE_ROOT = _LOCAL / "Cache"
    LOG_ROOT = _LOCAL / "Logs"
else:
    SUPPORT_ROOT = Path.home() / "Library" / "Application Support" / "MoMo"
    CACHE_ROOT = Path.home() / "Library" / "Caches" / "MoMo"
    LOG_ROOT = Path.home() / "Library" / "Logs" / "MoMo"

log = logging.getLogger("momo.cleaner")


def ensure_model_file(directory):
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / MODEL_FILENAME
    partial = directory / f"{MODEL_FILENAME}.partial"
    if destination.exists() and destination.stat().st_size >= MODEL_MIN_BYTES:
        return
    if destination.exists():
        if partial.exists():
            destination.unlink()
        else:
            destination.replace(partial)
    failures = []
    for url in MODEL_URLS:
        result = subprocess.run(
            ["curl", "-L", "--fail", "--retry", "3", "--continue-at", "-", "-o", str(partial), url],
            check=False,
            creationflags=NO_WINDOW,
        )
        if result.returncode == 0 and partial.exists() and partial.stat().st_size >= MODEL_MIN_BYTES:
            partial.replace(destination)
            return
        failures.append(url)
    raise RuntimeError("Could not finish downloading the crowd model from " + ", ".join(failures))


def support_dir():
    path = SUPPORT_ROOT
    path.mkdir(parents=True, exist_ok=True)
    return path


def bundled_model_dir():
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    path = Path(base) / "models"
    model = path / MODEL_FILENAME
    if model.exists() and model.stat().st_size >= MODEL_MIN_BYTES:
        return path
    return None


def cache_dir_for(video_path, model_overlap):
    stat = os.stat(video_path)
    identity = f"{Path(video_path).resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{MODEL_FILENAME}|{CHUNK_SECONDS}|{OVERLAP_SECONDS}|{model_overlap}"
    digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
    path = CACHE_ROOT / digest
    path.mkdir(parents=True, exist_ok=True)
    return path


def chunk_spans(total_frames, chunk_frames, hop_frames):
    spans = []
    start = 0
    while start < total_frames:
        end = min(total_frames, start + chunk_frames)
        spans.append((start, end))
        if end == total_frames:
            break
        start += hop_frames
    return spans


def covering_chunks(frame, spans, hop_frames):
    if not spans or frame < 0:
        return []
    guess = min(len(spans) - 1, max(0, frame // hop_frames))
    found = []
    for index in (guess - 1, guess, guess + 1):
        if 0 <= index < len(spans):
            start, end = spans[index]
            if start <= frame < end:
                found.append(index)
    return found


def range_is_ready(start, end, spans, hop_frames, done):
    if end <= start:
        return True
    position = start
    while position < end:
        covered = covering_chunks(position, spans, hop_frames)
        if not covered or any(index not in done for index in covered):
            return False
        next_position = end
        for span_start, span_end in spans:
            if position < span_start < next_position:
                next_position = span_start
            if position < span_end < next_position:
                next_position = span_end
        if next_position <= position:
            return False
        position = next_position
    return True


def fit_length(audio, count):
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 1:
        audio = np.column_stack([audio, audio])
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    audio = audio[:, :2]
    if len(audio) == count:
        return audio
    if len(audio) == 0:
        return np.zeros((count, 2), dtype=np.float32)
    source_x = np.linspace(0.0, 1.0, len(audio), endpoint=False)
    target_x = np.linspace(0.0, 1.0, count, endpoint=False)
    fitted = np.column_stack([np.interp(target_x, source_x, audio[:, channel]) for channel in range(2)])
    return fitted.astype(np.float32)


class CrowdCleaner:
    def __init__(self, video_path, ffmpeg, model_overlap):
        self.video_path = str(video_path)
        self.ffmpeg = ffmpeg
        self.model_overlap = int(model_overlap)
        ffmpeg_dir = str(Path(ffmpeg).resolve().parent)
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
        self.cache = cache_dir_for(self.video_path, self.model_overlap)
        self.source_wav = self.cache / "source.wav"
        self.pcm_path = self.cache / "cleaned.f32"
        self.meta_path = self.cache / "meta.json"
        self.work = self.cache / "work"
        self.work.mkdir(exist_ok=True)
        self.sample_rate = SAMPLE_RATE
        self.chunk_frames = CHUNK_SECONDS * SAMPLE_RATE
        self.hop_frames = HOP_SECONDS * SAMPLE_RATE
        self.overlap_frames = OVERLAP_SECONDS * SAMPLE_RATE
        self.spans = []
        self.total_frames = 0
        self.done = set()
        self.pcm = None
        self.keep_stem = "other"
        self.status = "Preparing audio…"
        self.phase = "preparing"
        self.error = None
        self.interest = 0.0
        self._chunk_seconds = []
        self._separator = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name="momo-cleaner", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def set_interest(self, seconds):
        self.interest = max(0.0, float(seconds))

    def is_complete(self):
        with self._lock:
            return bool(self.spans) and len(self.done) == len(self.spans)

    def progress(self):
        phase = self.phase
        if phase == "downloading":
            partial = support_dir() / "models" / f"{MODEL_FILENAME}.partial"
            received = partial.stat().st_size if partial.exists() else 0
            return {
                "phase": phase,
                "fraction": min(0.99, received / MODEL_EXPECTED_BYTES),
                "title": "Downloading the crowd model",
                "detail": f"{received / 1_000_000:.0f} of 913 MB · only the first time",
            }
        if phase == "loading":
            return {"phase": phase, "fraction": -1.0, "title": "Loading the crowd model", "detail": "Almost ready"}
        if phase == "cleaning":
            with self._lock:
                done = len(self.done)
                total = len(self.spans)
                recent = self._chunk_seconds[-6:]
            detail = f"Part {min(done + 1, total)} of {total}"
            if recent and total > done:
                left = (total - done) * sum(recent) / len(recent)
                if left < 60:
                    detail += " · less than a minute left"
                else:
                    detail += f" · about {round(left / 60)} min left"
            return {
                "phase": phase,
                "fraction": done / total if total else 0.0,
                "title": "Removing the laugh track",
                "detail": detail,
            }
        return {"phase": phase, "fraction": -1.0, "title": "Preparing audio", "detail": "Reading the episode's sound"}

    def ready(self, start_frame, count):
        with self._lock:
            if self.pcm is None:
                return False
            end = min(self.total_frames, start_frame + count)
            return range_is_ready(max(0, start_frame), end, self.spans, self.hop_frames, self.done)

    def read(self, start_frame, count):
        with self._lock:
            if self.pcm is None or start_frame >= self.total_frames:
                return None
            end = min(self.total_frames, start_frame + count)
            if not range_is_ready(max(0, start_frame), end, self.spans, self.hop_frames, self.done):
                return None
            piece = np.array(self.pcm[max(0, start_frame) : end], dtype=np.float32)
        if start_frame < 0:
            piece = np.vstack([np.zeros((-start_frame, 2), dtype=np.float32), piece])
        if len(piece) < count:
            piece = np.vstack([piece, np.zeros((count - len(piece), 2), dtype=np.float32)])
        return piece

    def _run(self):
        try:
            self._prepare_source()
            if self._stop.is_set():
                return
            self._open_timeline()
            if len(self.done) == len(self.spans):
                self.status = "Ready"
                return
            self._load_model()
            self.phase = "cleaning"
            while not self._stop.is_set():
                index = self._next_index()
                if index is None:
                    self.status = "Ready"
                    return
                self.status = f"Removing crowd… {index + 1} of {len(self.spans)}"
                started = time.perf_counter()
                self._process_chunk(index)
                self._chunk_seconds.append(time.perf_counter() - started)
        except Exception as error:
            log.exception("Crowd removal failed")
            self.error = str(error)
            self.status = "Could not remove the crowd"

    def _prepare_source(self):
        if self.source_wav.exists() and self.source_wav.stat().st_size > 0:
            return
        self.status = "Preparing audio…"
        temporary = self.source_wav.with_suffix(".partial.wav")
        result = subprocess.run(
            [
                self.ffmpeg,
                "-y",
                "-i",
                self.video_path,
                "-map",
                "0:a:0",
                "-ac",
                "2",
                "-ar",
                str(SAMPLE_RATE),
                "-c:a",
                "pcm_f32le",
                str(temporary),
            ],
            capture_output=True,
            text=True,
            creationflags=NO_WINDOW,
        )
        if result.returncode != 0 or not temporary.exists():
            detail = (result.stderr or "").strip().splitlines()
            message = detail[-1] if detail else "This video has no audio track."
            raise RuntimeError(message)
        temporary.replace(self.source_wav)

    def _open_timeline(self):
        info = sf.info(self.source_wav)
        self.sample_rate = info.samplerate
        self.total_frames = info.frames
        self.chunk_frames = CHUNK_SECONDS * self.sample_rate
        self.hop_frames = HOP_SECONDS * self.sample_rate
        self.overlap_frames = OVERLAP_SECONDS * self.sample_rate
        self.spans = chunk_spans(self.total_frames, self.chunk_frames, self.hop_frames)
        meta = self._read_meta()
        if meta and meta.get("frames") == self.total_frames and meta.get("complete"):
            self.pcm = np.memmap(self.pcm_path, dtype=np.float32, mode="r", shape=(self.total_frames, 2))
            self.done = set(range(len(self.spans)))
            return
        self.pcm = np.memmap(self.pcm_path, dtype=np.float32, mode="w+", shape=(self.total_frames, 2))
        restored = []
        for index in range(len(self.spans)):
            if self._chunk_path(index).exists():
                restored.append(index)
        for index in restored:
            self._mix_chunk(index)
        self.done = set(restored)
        self._write_meta()

    def _load_model(self):
        from audio_separator.separator import Separator

        model_dir = bundled_model_dir() or support_dir() / "models"
        if not (model_dir / MODEL_FILENAME).exists():
            self.phase = "downloading"
            self.status = "Downloading the crowd model (about 900 MB). This happens once."
            ensure_model_file(model_dir)
        self.phase = "loading"
        self.status = "Loading the crowd model…"
        self._separator = Separator(
            log_level=logging.INFO,
            model_file_dir=str(model_dir),
            output_dir=str(self.work),
            output_format="WAV",
            output_single_stem="other",
            sample_rate=self.sample_rate,
            use_soundfile=WINDOWS,
            mdxc_params={
                "segment_size": 256,
                "override_model_segment_size": False,
                "batch_size": 1,
                "overlap": self.model_overlap,
                "pitch_shift": 0,
            },
        )
        self._separator.load_model(MODEL_FILENAME)
        instance = self._separator.model_instance
        names = [instance.primary_stem_name, instance.secondary_stem_name]
        keep = next((name for name in names if name and "crowd" not in name.lower()), "other")
        self.keep_stem = keep
        self._separator.output_single_stem = keep
        instance.output_single_stem = keep
        log.info("Keeping stem %s (available: %s)", keep, names)

    def _next_index(self):
        with self._lock:
            done = set(self.done)
        if not self.spans:
            return None
        frame = min(self.total_frames - 1, int(self.interest * self.sample_rate))
        preferred = covering_chunks(frame, self.spans, self.hop_frames)
        start = preferred[0] if preferred else 0
        order = list(range(start, len(self.spans))) + list(range(0, start))
        for index in order:
            if index not in done:
                return index
        return None

    def _process_chunk(self, index):
        start, end = self.spans[index]
        chunk_wav = self.work / f"in_{index:05d}.wav"
        self._cut_wav(start, end, chunk_wav)
        for old in self.work.glob("*.wav"):
            if old.name.startswith("clean"):
                old.unlink()
        output = self._separator.separate(
            str(chunk_wav),
            custom_output_names={self.keep_stem: f"clean_{index:05d}"},
        )
        produced = [self.work / path if not os.path.isabs(path) else Path(path) for path in output]
        produced = [path for path in produced if path.exists() and "crowd" not in path.name.lower()]
        if not produced:
            produced = list(self.work.glob(f"clean_{index:05d}*.wav"))
        if not produced:
            raise RuntimeError("The crowd model did not return cleaned audio.")
        audio, rate = sf.read(produced[0], always_2d=True, dtype="float32")
        if rate != self.sample_rate:
            raise RuntimeError(f"Cleaned audio is {rate} Hz, expected {self.sample_rate} Hz.")
        audio = fit_length(audio, end - start)
        np.save(self._chunk_path(index), audio)
        self._mix_chunk(index)
        if index + 1 < len(self.spans) and self._chunk_path(index + 1).exists():
            self._mix_chunk(index + 1)
        self.pcm.flush()
        with self._lock:
            self.done.add(index)
        self._write_meta()
        chunk_wav.unlink(missing_ok=True)
        produced[0].unlink(missing_ok=True)

    def _cut_wav(self, start, end, destination):
        result = subprocess.run(
            [
                self.ffmpeg,
                "-y",
                "-ss",
                f"{start / self.sample_rate:.6f}",
                "-t",
                f"{(end - start) / self.sample_rate:.6f}",
                "-i",
                str(self.source_wav),
                "-c:a",
                "pcm_f32le",
                str(destination),
            ],
            capture_output=True,
            text=True,
            creationflags=NO_WINDOW,
        )
        if result.returncode != 0:
            raise RuntimeError("Could not read a section of the audio.")

    def _mix_chunk(self, index):
        audio = np.load(self._chunk_path(index))
        start, end = self.spans[index]
        head = 0
        if index > 0:
            head = min(len(audio), self.spans[index - 1][1] - start)
            if head > 0 and self._chunk_path(index - 1).exists():
                previous = np.load(self._chunk_path(index - 1))
                ramp = np.linspace(0.0, 1.0, head, dtype=np.float32)[:, None]
                mixed = previous[-head:] * (1.0 - ramp) + audio[:head] * ramp
                self.pcm[start : start + head] = mixed
        if head < len(audio):
            self.pcm[start + head : end] = audio[head:]

    def _chunk_path(self, index):
        return self.cache / f"chunk_{index:05d}.npy"

    def _read_meta(self):
        if not self.meta_path.exists() or not self.pcm_path.exists():
            return None
        try:
            return json.loads(self.meta_path.read_text())
        except json.JSONDecodeError:
            return None

    def _write_meta(self):
        complete = len(self.done) == len(self.spans) and len(self.spans) > 0
        payload = {
            "version": 1,
            "frames": self.total_frames,
            "sample_rate": self.sample_rate,
            "done": sorted(self.done),
            "complete": complete,
        }
        temporary = self.meta_path.with_suffix(".json.partial")
        temporary.write_text(json.dumps(payload))
        temporary.replace(self.meta_path)


def _self_test():
    spans = chunk_spans(50 * SAMPLE_RATE, 20 * SAMPLE_RATE, 18 * SAMPLE_RATE)
    assert spans == [
        (0, 20 * SAMPLE_RATE),
        (18 * SAMPLE_RATE, 38 * SAMPLE_RATE),
        (36 * SAMPLE_RATE, 50 * SAMPLE_RATE),
    ]
    done = {0}
    assert range_is_ready(0, 17 * SAMPLE_RATE, spans, 18 * SAMPLE_RATE, done)
    assert not range_is_ready(0, 19 * SAMPLE_RATE, spans, 18 * SAMPLE_RATE, done)
    done.add(1)
    assert range_is_ready(18 * SAMPLE_RATE, 20 * SAMPLE_RATE, spans, 18 * SAMPLE_RATE, done)
    audio = fit_length(np.linspace(-1, 1, 10, dtype=np.float32), 4)
    assert audio.shape == (4, 2)
    print("cleaner self-test ok")


if __name__ == "__main__":
    _self_test()
