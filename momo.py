"""MoMo plays a video and removes crowd laughter while you watch."""

import atexit
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import sounddevice as sd

from cleaner import LOG_ROOT, NO_WINDOW, SAMPLE_RATE, WINDOWS, CrowdCleaner

LOG_DIR = LOG_ROOT
UI_FONT = "Segoe UI" if WINDOWS else "Helvetica Neue"
LEAD_SECONDS = 0.12
PLAY_OVERLAP = 2
CLEAN_OVERLAP_MIN = 2
CLEAN_OVERLAP_MAX = 8
QUALITY_NOTES = {
    2: "Fastest. A faint seam, or a little crowd on a long laugh.",
    3: "Faster. Long laughs may sound slightly rough.",
    4: "About twice as fast as the smoothest setting.",
    5: "A middle setting. Smoother, and still quicker.",
    6: "Closer to the smoothest sound.",
    7: "Very smooth, and a little faster than the slowest.",
    8: "Smoothest sound. This takes the longest.",
}
VIDEO_SUFFIXES = {".mkv", ".mp4"}
SNAP_SECONDS = 0.08
OSC_OPTIONS = ",".join(
    [
        "osc-layout=box",
        "osc-seekbarstyle=knob",
        "osc-seekbarhandlesize=0.7",
        "osc-boxalpha=50",
        "osc-hidetimeout=1200",
        "osc-fadeduration=250",
        "osc-scalewindowed=1.15",
        "osc-scalefullscreen=1.15",
        "osc-vidscale=no",
        "osc-deadzonesize=0.75",
        "osc-timetotal=yes",
        "osc-title=MoMo   ·   press L for original sound",
    ]
)


def app_resource(name):
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def tool(name):
    if getattr(sys, "frozen", False):
        executable = name + ".exe" if WINDOWS else name
        bundled = os.path.join(os.path.dirname(sys.executable), executable)
        if os.path.isfile(bundled):
            return bundled
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f"MoMo could not find {name}.")
    return found


def alert(message):
    if WINDOWS:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, str(message), "MoMo", 0x40000)
        return
    script = f'display dialog {json.dumps(message)} buttons {{"OK"}} default button 1 with title "MoMo"'
    subprocess.run(["osascript", "-e", script], check=False)


def choose_videos():
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    chosen = filedialog.askopenfilenames(
        title="Open videos in MoMo",
        filetypes=[("Video", "*.mkv *.mp4"), ("MKV", "*.mkv"), ("MP4", "*.mp4")],
    )
    root.destroy()
    return list(chosen)


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_DIR / "momo.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


class WindowsPipe:
    """A named-pipe client that can read on one thread while another writes."""

    def __init__(self, path):
        import _winapi

        self._winapi = _winapi
        self._handle = _winapi.CreateFile(
            path,
            _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
            0,
            _winapi.NULL,
            _winapi.OPEN_EXISTING,
            _winapi.FILE_FLAG_OVERLAPPED,
            _winapi.NULL,
        )

    def sendall(self, data):
        overlapped, _error = self._winapi.WriteFile(self._handle, data, overlapped=True)
        overlapped.GetOverlappedResult(True)

    def recv(self, size):
        overlapped, _error = self._winapi.ReadFile(self._handle, size, overlapped=True)
        _count, error = overlapped.GetOverlappedResult(True)
        if error == self._winapi.ERROR_OPERATION_ABORTED:
            return b""
        return overlapped.getbuffer()

    def close(self):
        self._winapi.CloseHandle(self._handle)


def connect_ipc(path):
    if WINDOWS:
        return WindowsPipe(path)
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.connect(path)
    except OSError:
        connection.close()
        raise
    return connection


class Mpv:
    def __init__(self, socket_path):
        deadline = time.time() + 8
        while True:
            try:
                self._socket = connect_ipc(socket_path)
                break
            except OSError:
                if time.time() > deadline:
                    raise RuntimeError("MoMo could not connect to the video window.")
                time.sleep(0.05)
        self._lock = threading.Lock()
        self._pending = {}
        self._next_id = 1
        self._buffer = b""
        self._closed = False
        threading.Thread(target=self._read_loop, name="momo-mpv", daemon=True).start()

    def command(self, *parts, timeout=3):
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            waiting = threading.Event()
            box = {}
            self._pending[request_id] = (waiting, box)
            payload = json.dumps({"request_id": request_id, "command": list(parts)}) + "\n"
            self._socket.sendall(payload.encode())
        if not waiting.wait(timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            raise TimeoutError(parts)
        if box.get("error") not in (None, "success"):
            raise RuntimeError(box.get("error"))
        return box.get("data")

    def _read_loop(self):
        while not self._closed:
            try:
                data = self._socket.recv(65536)
            except OSError:
                return
            if not data:
                return
            self._buffer += data
            while b"\n" in self._buffer:
                line, self._buffer = self._buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                request_id = message.get("request_id")
                if request_id is None:
                    continue
                with self._lock:
                    pending = self._pending.pop(request_id, None)
                if pending:
                    waiting, box = pending
                    box.update(message)
                    waiting.set()

    def close(self):
        self._closed = True
        try:
            self._socket.close()
        except OSError:
            pass


class AudioClock:
    def __init__(self):
        self._lock = threading.Lock()
        self._base_frame = 0
        self._base_time = time.perf_counter()
        self.playing = False

    def play_at(self, frame):
        with self._lock:
            self._base_frame = max(0, int(frame))
            self._base_time = time.perf_counter()
            self.playing = True

    def follow(self, frame):
        frame = max(0, int(frame))
        with self._lock:
            now = time.perf_counter()
            if not self.playing:
                self._base_frame = frame
                self._base_time = now
                self.playing = True
                return
            heard = self._base_frame + int((now - self._base_time) * SAMPLE_RATE)
            if abs(heard - frame) > int(SNAP_SECONDS * SAMPLE_RATE):
                self._base_frame = frame
                self._base_time = now

    def pause_at(self, frame):
        with self._lock:
            self._base_frame = max(0, int(frame))
            self._base_time = time.perf_counter()
            self.playing = False

    def output_frame(self, delay_seconds):
        with self._lock:
            if not self.playing:
                return None
            elapsed = time.perf_counter() - self._base_time
            return self._base_frame + int((elapsed + delay_seconds) * SAMPLE_RATE)


def applescript_text(text):
    return " & return & ".join(json.dumps(line) for line in text.split("\n"))


def ask(message, buttons, default):
    if WINDOWS:
        return ask_with_tk(message, buttons, default)
    button_list = ", ".join(json.dumps(button) for button in buttons)
    script = f"""
    try
        set picked to button returned of (display dialog {applescript_text(message)} buttons {{{button_list}}} default button {json.dumps(default)} with title "MoMo")
        return picked
    on error number -128
        return ""
    end try
    """
    return subprocess.check_output(["osascript", "-e", script], text=True).strip()


def ask_with_tk(message, buttons, default):
    import tkinter as tk
    from tkinter import ttk

    picked = {"value": ""}
    root = tk.Tk()
    root.title("MoMo")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    frame = ttk.Frame(root, padding=(22, 18, 22, 16))
    frame.grid()
    ttk.Label(frame, text=message, wraplength=420, justify="left").grid(sticky="w")
    row = ttk.Frame(frame)
    row.grid(sticky="e", pady=(16, 0))

    def close(value):
        picked["value"] = value
        root.destroy()

    focused = None
    for column, label in enumerate(buttons):
        button = ttk.Button(
            row,
            text=label,
            default="active" if label == default else "normal",
            command=lambda label=label: close(label),
        )
        button.grid(row=0, column=column, padx=(8 if column else 0, 0))
        if label == default:
            focused = button
    root.bind("<Return>", lambda _event: close(default))
    root.bind("<Escape>", lambda _event: close(""))
    root.protocol("WM_DELETE_WINDOW", lambda: close(""))
    root.update_idletasks()
    root.geometry(f"+{(root.winfo_screenwidth() - root.winfo_width()) // 2}+{(root.winfo_screenheight() - root.winfo_height()) // 3}")
    root.lift()
    if focused is not None:
        focused.focus_force()
    root.mainloop()
    return picked["value"]


def choose_clean_overlap():
    import tkinter as tk
    from tkinter import ttk

    notes = QUALITY_NOTES
    chosen = {"value": None}
    root = tk.Tk()
    root.title("MoMo")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    frame = ttk.Frame(root, padding=(22, 18, 22, 16))
    frame.grid()

    ttk.Label(frame, text="How smooth should it sound?", font=(UI_FONT, 16, "bold")).grid(sticky="w")
    ttk.Label(
        frame,
        text="Drag left to finish sooner. Drag right for a smoother sound.",
        wraplength=380,
    ).grid(sticky="w", pady=(8, 4))
    caption = ttk.Label(frame, text=notes[8], wraplength=380)
    caption.grid(sticky="w", pady=(0, 12))

    scale = ttk.Scale(frame, from_=CLEAN_OVERLAP_MIN, to=CLEAN_OVERLAP_MAX, orient="horizontal", length=380)
    scale.set(CLEAN_OVERLAP_MAX)
    scale.grid(sticky="ew")

    def amount():
        return min(CLEAN_OVERLAP_MAX, max(CLEAN_OVERLAP_MIN, int(round(float(scale.get())))))

    def refresh(_value=None):
        caption.configure(text=notes[amount()])

    def snap(_event=None):
        scale.set(amount())
        refresh()

    scale.configure(command=refresh)
    scale.bind("<ButtonRelease-1>", snap)

    ends = ttk.Frame(frame)
    ends.grid(sticky="ew", pady=(2, 0))
    ttk.Label(ends, text="Faster").grid(row=0, column=0, sticky="w")
    ttk.Label(ends, text="Smoothest").grid(row=0, column=1, sticky="e")
    ends.columnconfigure(0, weight=1)
    ends.columnconfigure(1, weight=1)

    buttons = ttk.Frame(frame)
    buttons.grid(sticky="e", pady=(16, 0))

    def close(accepted):
        if accepted:
            chosen["value"] = amount()
        root.destroy()

    ttk.Button(buttons, text="Cancel", command=lambda: close(False)).grid(row=0, column=0, padx=(0, 8))
    start = ttk.Button(buttons, text="Start", default="active", command=lambda: close(True))
    start.grid(row=0, column=1)
    root.bind("<Return>", lambda _event: close(True))
    root.bind("<Escape>", lambda _event: close(False))
    root.protocol("WM_DELETE_WINDOW", lambda: close(False))
    root.update_idletasks()
    root.geometry(f"+{(root.winfo_screenwidth() - root.winfo_width()) // 2}+{(root.winfo_screenheight() - root.winfo_height()) // 3}")
    root.lift()
    start.focus_set()
    root.mainloop()
    return chosen["value"]


def choose_mode():
    picked = ask(
        "Play now starts in a few seconds. The sound is a bit rougher: a faint seam, or a little crowd on a long laugh.\n\n"
        "Clean first waits until the episode is cleaned, so playback does not stop. You choose how smooth the sound is.",
        ["Play now", "Clean first"],
        "Clean first",
    )
    if picked == "Clean first":
        overlap = choose_clean_overlap()
        if overlap is None:
            return None
        return "clean", overlap
    if picked == "Play now":
        return "play", PLAY_OVERLAP
    return None


def toggle_count(path):
    try:
        return path.read_text().count("\n")
    except FileNotFoundError:
        return 0


def run(video_path):
    video_path = os.path.abspath(video_path)
    ffmpeg = tool("ffmpeg")
    mpv_bin = tool("mpv")
    clock = AudioClock()
    cleaner = None
    state = {"cleaned": True}

    def callback(outdata, frames, time_info, _status):
        try:
            if cleaner is None or not state["cleaned"]:
                outdata.fill(0)
                return
            delay = LEAD_SECONDS
            if time_info is not None:
                delay = max(0.0, time_info.outputBufferDacTime - time_info.currentTime)
            start = clock.output_frame(delay)
            if start is None:
                outdata.fill(0)
                return
            audio = cleaner.read(start, frames)
            if audio is None:
                outdata.fill(0)
                return
            outdata[:] = audio
        except Exception:
            logging.exception("Audio callback failed")
            outdata.fill(0)

    stream = sd.OutputStream(
        samplerate=SAMPLE_RATE,
        channels=2,
        dtype="float32",
        latency="high",
        callback=callback,
    )
    stream.start()

    socket_dir = tempfile.mkdtemp(prefix="momo-")
    if WINDOWS:
        socket_path = rf"\\.\pipe\{os.path.basename(socket_dir)}"
    else:
        socket_path = os.path.join(socket_dir, "mpv.sock")
    toggle_path = Path(socket_dir) / "toggle"
    toggle_path.write_text("")
    environment = os.environ.copy()
    environment["MOMO_TOGGLE"] = str(toggle_path)
    platform_options = [] if WINDOWS else ["--macos-app-activation-policy=accessory"]
    process = subprocess.Popen(
        [
            mpv_bin,
            "--no-terminal",
            "--force-window=yes",
            "--title=MoMo",
            "--force-media-title=MoMo",
            "--pause=yes",
            "--mute=yes",
            "--keep-open=no",
            "--hwdec=auto",
            "--ontop=yes",
            *platform_options,
            f"--osd-font={UI_FONT}",
            "--osd-font-size=26",
            "--osd-border-size=1.2",
            "--osd-border-color=#B0000000",
            "--osd-bar-h=2",
            "--osd-bar-align-y=0.9",
            "--osd-color=#FFFFFFFF",
            f"--script-opts={OSC_OPTIONS}",
            f"--input-ipc-server={socket_path}",
            f"--script={app_resource('momo.lua')}",
            video_path,
        ],
        env=environment,
    )

    def shutdown():
        if cleaner is not None:
            cleaner.stop()
        stream.stop()
        stream.close()
        if process.poll() is None:
            process.terminate()
        shutil.rmtree(socket_dir, ignore_errors=True)

    atexit.register(shutdown)
    mpv = Mpv(socket_path)
    try:
        mpv.command("set_property", "ontop", False)
    except Exception:
        logging.exception("Could not lower the video window")
    try:
        choice = choose_mode()
    finally:
        try:
            mpv.command("set_property", "ontop", True)
        except Exception:
            logging.exception("Could not keep the video window on top")
    if choice is None:
        return
    mode, overlap = choice
    clean_first = mode == "clean"
    cleaner = CrowdCleaner(video_path, ffmpeg, overlap)
    cleaner.start()
    we_paused = True
    reported_error = False
    last_toggle = 0
    shown_card = None
    announced_ready = False

    def show_card(card):
        nonlocal shown_card
        key = (round(card["fraction"], 3), card["title"], card["detail"])
        if key == shown_card:
            return
        shown_card = key
        try:
            mpv.command("script-message", "momo-progress", f"{card['fraction']:.4f}", card["title"], card["detail"])
        except Exception:
            logging.exception("Could not show progress")

    def hide_card():
        nonlocal shown_card
        if shown_card is None:
            return
        shown_card = None
        try:
            mpv.command("script-message", "momo-progress-hide")
        except Exception:
            logging.exception("Could not hide progress")

    def badge(text, on=True):
        try:
            mpv.command("script-message", "momo-badge", text, "on" if on else "off")
        except Exception:
            logging.exception("Could not show badge")

    while process.poll() is None:
        try:
            position = mpv.command("get_property", "time-pos")
            paused = bool(mpv.command("get_property", "pause"))
        except Exception:
            if process.poll() is not None:
                break
            time.sleep(0.05)
            continue
        if position is None:
            position = 0.0
        cleaner.set_interest(position)
        frame = int(float(position) * SAMPLE_RATE)
        presses = toggle_count(toggle_path)
        if presses != last_toggle:
            last_toggle = presses
            state["cleaned"] = not state["cleaned"]
            try:
                mpv.command("set_property", "mute", state["cleaned"])
            except Exception:
                logging.exception("Could not toggle audio")
            badge("Laughs removed" if state["cleaned"] else "Original sound", state["cleaned"])
            if not state["cleaned"]:
                hide_card()
                if we_paused:
                    mpv.command("set_property", "pause", False)
                    we_paused = False

        if state["cleaned"]:
            ready = cleaner.is_complete() if clean_first else cleaner.ready(frame, int(0.25 * SAMPLE_RATE))
            if cleaner.error and not reported_error:
                reported_error = True
                hide_card()
                alert(cleaner.error)
                state["cleaned"] = False
                mpv.command("set_property", "mute", False)
                if we_paused:
                    mpv.command("set_property", "pause", False)
                    we_paused = False
            elif not ready:
                clock.pause_at(frame)
                if not paused:
                    mpv.command("set_property", "pause", True)
                    we_paused = True
                card = cleaner.progress()
                if not clean_first and card["phase"] == "cleaning":
                    card = {
                        "phase": "cleaning",
                        "fraction": -1.0,
                        "title": "Getting the next part ready",
                        "detail": "Playback continues in a few seconds",
                    }
                show_card(card)
            else:
                hide_card()
                if clean_first and not announced_ready:
                    announced_ready = True
                    badge("Laughs removed")
                if we_paused:
                    mpv.command("set_property", "pause", False)
                    we_paused = False
                if paused:
                    clock.pause_at(frame)
                else:
                    clock.follow(frame)
        else:
            clock.pause_at(frame)
        time.sleep(0.05)

    mpv.close()
    cleaner.stop()


def cleaned_output_path(video_path):
    path = Path(video_path)
    return path.with_name(f"no_crowd_{path.name}")


def save_cleaned_video(ffmpeg, source, pcm_path, destination, sample_rate, should_stop):
    partial = destination.with_name(destination.name + ".partial")
    partial.unlink(missing_ok=True)
    log_path = destination.with_name(destination.name + ".log")

    def command(include_subtitles):
        mapping = ["-map", "0:v:0", "-map", "1:a:0", "-map_metadata", "0", "-map_chapters", "0"]
        codecs = ["-c:v", "copy", "-c:a", "aac", "-b:a", "256k"]
        if include_subtitles:
            mapping[4:4] = ["-map", "0:s?"]
            codecs.extend(["-c:s", "copy"])
        container = "mp4" if destination.suffix.lower() == ".mp4" else "matroska"
        if container == "mp4":
            codecs.extend(["-movflags", "+faststart"])
        return [
            ffmpeg,
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-f",
            "f32le",
            "-ar",
            str(sample_rate),
            "-ac",
            "2",
            "-i",
            str(pcm_path),
            *mapping,
            *codecs,
            "-f",
            container,
            str(partial),
        ]

    def run_command(include_subtitles):
        with open(log_path, "w", encoding="utf-8") as stderr:
            process = subprocess.Popen(
                command(include_subtitles),
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                creationflags=NO_WINDOW,
            )
            while process.poll() is None:
                if should_stop():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    partial.unlink(missing_ok=True)
                    return None
                time.sleep(0.2)
        if process.returncode == 0:
            return True
        detail = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        message = detail[-1] if detail else f"ffmpeg stopped with code {process.returncode}."
        return message

    saved = run_command(True)
    if saved is None:
        log_path.unlink(missing_ok=True)
        return False
    if saved is not True:
        logging.info("Saving without subtitles after: %s", saved)
        partial.unlink(missing_ok=True)
        saved = run_command(False)
    log_path.unlink(missing_ok=True)
    if saved is None:
        return False
    if saved is not True:
        partial.unlink(missing_ok=True)
        raise RuntimeError(saved)
    partial.replace(destination)
    return True


class BatchJob:
    def __init__(self, path):
        self.path = Path(path)
        self.status = "waiting"
        self.fraction = 0.0
        self.detail = "Waiting"
        self.cancel = False
        self.lock = threading.Lock()

    def request_cancel(self):
        with self.lock:
            if self.status in {"done", "cancelled", "error"}:
                return
            self.cancel = True
            if self.status == "waiting":
                self.status = "cancelled"
                self.detail = "Cancelled"
            elif self.status == "saving":
                self.detail = "Stopping…"
            else:
                self.detail = "Stopping after this piece of audio…"

    def snapshot(self):
        with self.lock:
            return self.status, self.fraction, self.detail, self.cancel


def export_videos(paths):
    import tkinter as tk
    from tkinter import ttk

    jobs = [BatchJob(path) for path in paths]
    root = tk.Tk()
    root.title("MoMo")
    root.attributes("-topmost", True)
    root.minsize(560, 420)
    frame = ttk.Frame(root, padding=(22, 18, 22, 16))
    frame.grid(sticky="nsew")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)

    ttk.Label(frame, text="Clean these videos", font=(UI_FONT, 16, "bold")).grid(sticky="w")
    ttk.Label(
        frame,
        text="Each video is saved next to the original, with no_crowd_ added to the name. An existing copy is replaced. Videos are cleaned one at a time.",
        wraplength=520,
    ).grid(sticky="w", pady=(6, 12))

    caption = ttk.Label(frame, text=QUALITY_NOTES[CLEAN_OVERLAP_MIN], wraplength=520)
    caption.grid(sticky="w")
    scale = ttk.Scale(frame, from_=CLEAN_OVERLAP_MIN, to=CLEAN_OVERLAP_MAX, orient="horizontal", length=520)
    scale.set(CLEAN_OVERLAP_MIN)
    scale.grid(sticky="ew", pady=(8, 0))

    def amount():
        return min(CLEAN_OVERLAP_MAX, max(CLEAN_OVERLAP_MIN, int(round(float(scale.get())))))

    def refresh_caption(_value=None):
        caption.configure(text=QUALITY_NOTES[amount()])

    def snap(_event=None):
        scale.set(amount())
        refresh_caption()

    scale.configure(command=refresh_caption)
    scale.bind("<ButtonRelease-1>", snap)
    ends = ttk.Frame(frame)
    ends.grid(sticky="ew", pady=(2, 12))
    ttk.Label(ends, text="Faster").grid(row=0, column=0, sticky="w")
    ttk.Label(ends, text="Smoothest").grid(row=0, column=1, sticky="e")
    ends.columnconfigure(0, weight=1)
    ends.columnconfigure(1, weight=1)

    list_host = ttk.Frame(frame)
    list_host.grid(sticky="nsew", pady=(4, 0))
    frame.rowconfigure(list_host.grid_info()["row"], weight=1)
    list_host.columnconfigure(0, weight=1)
    list_host.rowconfigure(0, weight=1)
    canvas = tk.Canvas(list_host, highlightthickness=0, height=min(360, 78 * len(jobs)))
    scrollbar = ttk.Scrollbar(list_host, orient="vertical", command=canvas.yview)
    rows = ttk.Frame(canvas)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")
    window_id = canvas.create_window((0, 0), window=rows, anchor="nw")

    def fit_rows(_event=None):
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.itemconfigure(window_id, width=canvas.winfo_width())

    rows.bind("<Configure>", fit_rows)
    canvas.bind("<Configure>", fit_rows)
    wheel_step = 120 if WINDOWS else 1
    canvas.bind("<MouseWheel>", lambda event: canvas.yview_scroll(-int(event.delta / wheel_step), "units"))
    rows.columnconfigure(0, weight=1)

    row_bars = []
    for index, job in enumerate(jobs):
        row = ttk.Frame(rows, padding=(0, 6))
        row.grid(sticky="ew", row=index, column=0)
        row.columnconfigure(0, weight=1)
        ttk.Label(row, text=job.path.name).grid(row=0, column=0, sticky="w")
        cancel = ttk.Button(row, text="Cancel", command=job.request_cancel)
        cancel.grid(row=0, column=1, padx=(12, 0))
        bar = ttk.Progressbar(row, mode="determinate", maximum=100)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        detail = ttk.Label(row, text="Waiting")
        detail.grid(row=2, column=0, columnspan=2, sticky="w")
        row_bars.append((bar, detail, cancel))

    buttons = ttk.Frame(frame)
    buttons.grid(sticky="e", pady=(16, 0))
    state = {"started": False, "stop": False, "alive": False, "overlap": CLEAN_OVERLAP_MIN}

    def request_stop():
        state["stop"] = True
        for job in jobs:
            job.request_cancel()

    def close():
        if state["alive"]:
            request_stop()
            return
        root.destroy()

    def paint():
        try:
            if not root.winfo_exists():
                return
        except tk.TclError:
            return
        for job, (bar, detail, cancel) in zip(jobs, row_bars):
            status, fraction, text, _cancel = job.snapshot()
            detail.configure(text=text)
            if status in {"done", "cancelled", "error"}:
                cancel.state(["disabled"])
            if status == "running" and fraction < 0:
                if str(bar.cget("mode")) != "indeterminate":
                    bar.configure(mode="indeterminate")
                    bar.start(12)
            else:
                if str(bar.cget("mode")) != "determinate":
                    bar.stop()
                    bar.configure(mode="determinate")
                shown = 100 if status == "done" else max(0, fraction * 100)
                bar.configure(value=shown)
        if state["alive"] or not state["started"]:
            root.after(200, paint)
        else:
            ok.state(["!disabled"])
            ok.configure(text="Close")

    def work():
        ffmpeg = tool("ffmpeg")
        state["alive"] = True
        try:
            for job in jobs:
                status, _fraction, _text, cancelled = job.snapshot()
                if state["stop"] or cancelled or status == "cancelled":
                    if status != "cancelled":
                        with job.lock:
                            job.status = "cancelled"
                            job.detail = "Cancelled"
                    continue
                with job.lock:
                    job.status = "running"
                    job.detail = "Preparing audio…"
                    job.fraction = -1
                cleaner = CrowdCleaner(str(job.path), ffmpeg, state["overlap"])
                cleaner.start()
                while cleaner._thread.is_alive():
                    _status, _fraction, _text, cancelled = job.snapshot()
                    if state["stop"] or cancelled:
                        cleaner.stop()
                    card = cleaner.progress()
                    with job.lock:
                        if job.cancel:
                            job.detail = "Stopping after this piece of audio…"
                        else:
                            job.fraction = card["fraction"]
                            piece = card["detail"] or card["title"]
                            if card["fraction"] >= 0:
                                job.detail = f"{round(card['fraction'] * 100)}% · {piece}"
                            else:
                                job.detail = piece
                    time.sleep(0.25)
                cleaner.stop()
                cleaner._thread.join()
                _status, _fraction, _text, cancelled = job.snapshot()
                if state["stop"] or cancelled or cleaner.error:
                    with job.lock:
                        if cleaner.error and not job.cancel and not state["stop"]:
                            job.status = "error"
                            job.detail = cleaner.error
                        else:
                            job.status = "cancelled"
                            job.detail = "Cancelled"
                    continue
                if not cleaner.is_complete():
                    with job.lock:
                        job.status = "error"
                        job.detail = "Cleaning stopped before the video was finished."
                    continue
                with job.lock:
                    job.status = "saving"
                    job.fraction = 1
                    job.detail = "Saving the video…"
                destination = cleaned_output_path(job.path)

                def should_stop(current=job):
                    _status, _fraction, _text, cancelled = current.snapshot()
                    return state["stop"] or cancelled

                try:
                    saved = save_cleaned_video(
                        ffmpeg,
                        job.path,
                        cleaner.pcm_path,
                        destination,
                        cleaner.sample_rate,
                        should_stop,
                    )
                except Exception as error:
                    logging.exception("Could not save %s", job.path)
                    with job.lock:
                        job.status = "error"
                        job.detail = str(error)
                    continue
                with job.lock:
                    if saved:
                        job.status = "done"
                        job.fraction = 1
                        job.detail = f"Saved {destination.name}"
                    else:
                        job.status = "cancelled"
                        job.detail = "Cancelled"
        finally:
            state["alive"] = False

    def start():
        if state["started"]:
            if not state["alive"]:
                root.destroy()
            return
        if not any(job.snapshot()[0] == "waiting" for job in jobs):
            return
        state["started"] = True
        state["overlap"] = amount()
        scale.state(["disabled"])
        ok.configure(text="Working…")
        ok.state(["disabled"])
        threading.Thread(target=work, name="momo-batch", daemon=True).start()

    ok = ttk.Button(buttons, text="OK", default="active", command=start)
    ok.grid(row=0, column=0)
    root.bind("<Return>", lambda _event: start())
    root.bind("<Escape>", lambda _event: close())
    root.protocol("WM_DELETE_WINDOW", close)
    root.update_idletasks()
    canvas.configure(background=ttk.Style().lookup("TFrame", "background") or "#F0F0F0")
    width = max(root.winfo_width(), 560)
    height = max(root.winfo_height(), 420)
    root.geometry(f"{width}x{height}+{(root.winfo_screenwidth() - width) // 2}+{(root.winfo_screenheight() - height) // 3}")
    root.lift()
    ok.focus_set()
    paint()
    root.mainloop()


def videos_from_arguments(arguments):
    supplied = [argument for argument in arguments if not argument.startswith("-")]
    if not supplied:
        chosen = choose_videos()
    else:
        chosen = supplied
    videos = []
    skipped = False
    for argument in chosen:
        path = Path(argument)
        if path.suffix.lower() not in VIDEO_SUFFIXES or not path.is_file():
            skipped = True
            continue
        videos.append(str(path.resolve()))
    if skipped:
        alert("MoMo uses MKV and MP4 videos. Other files were left out.")
    return videos


def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    setup_logging()
    try:
        videos = videos_from_arguments(sys.argv[1:])
        if len(videos) == 1:
            run(videos[0])
        elif len(videos) > 1:
            export_videos(videos)
    except Exception as error:
        logging.exception("MoMo failed")
        alert(str(error))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
