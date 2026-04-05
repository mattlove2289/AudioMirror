import threading
import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox
import pyaudiowpatch as pyaudio


# ── Audio Engine ──────────────────────────────────────────────────────────────

class AudioMirrorEngine:
    def __init__(self):
        self.thread = None
        self.stop_event = threading.Event()
        self.is_running = False
        self._pa = pyaudio.PyAudio()

    def get_loopback_devices(self):
        devices = []
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            if "[Loopback]" in info.get("name", ""):
                devices.append(info)
        return devices

    def get_output_devices(self):
        devices = []
        seen = set()
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            name = info.get("name", "")
            if (
                info.get("maxOutputChannels", 0) > 0
                and "[Loopback]" not in name
                and "Sound Mapper" not in name
                and "Primary Sound" not in name
                and name not in seen
            ):
                seen.add(name)
                devices.append(info)
        return devices

    def get_default_loopback(self):
        try:
            return self._pa.get_default_wasapi_loopback()
        except Exception:
            loopbacks = self.get_loopback_devices()
            return loopbacks[0] if loopbacks else None

    def find_loopback_by_name(self, name):
        for d in self.get_loopback_devices():
            if d["name"] == name:
                return d
        return None

    def find_output_by_name(self, name):
        for d in self.get_output_devices():
            if d["name"] == name:
                return d
        return None

    def _find_working_channel_count(self, device_index, sample_rate, is_input=True):
        """Try channel counts from native down to 1, return first that works."""
        fmt = pyaudio.paFloat32
        info = self._pa.get_device_info_by_index(device_index)
        max_ch = int(info["maxInputChannels"] if is_input else info["maxOutputChannels"])

        for ch in [max_ch, 8, 4, 2, 1]:
            if ch > max_ch or ch < 1:
                continue
            try:
                kwargs = dict(
                    format=fmt,
                    channels=ch,
                    rate=int(sample_rate),
                    frames_per_buffer=1024,
                )
                if is_input:
                    kwargs["input"] = True
                    kwargs["input_device_index"] = device_index
                else:
                    kwargs["output"] = True
                    kwargs["output_device_index"] = device_index
                s = self._pa.open(**kwargs)
                s.stop_stream()
                s.close()
                return ch
            except Exception:
                continue
        return None

    def start(self, source_name: str, target_name: str):
        if self.is_running:
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._mirror_loop,
            args=(source_name, target_name),
            daemon=True
        )
        self.thread.start()
        self.is_running = True

    def stop(self):
        if not self.is_running:
            return
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        self.is_running = False

    def _mirror_loop(self, source_name: str, target_name: str):
        source = self.find_loopback_by_name(source_name)
        target = self.find_output_by_name(target_name)

        if source is None:
            print(f"Error: Source '{source_name}' not found.")
            self.is_running = False
            return

        if target is None:
            print(f"Error: Target '{target_name}' not found.")
            self.is_running = False
            return

        sample_rate = int(source["defaultSampleRate"])

        # Find the actual working channel counts for each device
        src_ch = self._find_working_channel_count(source["index"], sample_rate, is_input=True)
        dst_ch = self._find_working_channel_count(target["index"], sample_rate, is_input=False)

        if src_ch is None:
            print(f"Error: Could not open source device with any channel count.")
            self.is_running = False
            return

        if dst_ch is None:
            print(f"Error: Could not open target device with any channel count.")
            self.is_running = False
            return

        print(f"Source: {src_ch}ch, Target: {dst_ch}ch @ {sample_rate}hz")

        frames = 1024
        fmt = pyaudio.paFloat32
        record_stream = None
        play_stream = None

        try:
            record_stream = self._pa.open(
                format=fmt,
                channels=src_ch,
                rate=sample_rate,
                input=True,
                input_device_index=source["index"],
                frames_per_buffer=frames,
            )

            play_stream = self._pa.open(
                format=fmt,
                channels=dst_ch,
                rate=sample_rate,
                output=True,
                output_device_index=target["index"],
                frames_per_buffer=frames,
            )

            while not self.stop_event.is_set():
                raw = record_stream.read(frames, exception_on_overflow=False)

                if src_ch != dst_ch:
                    audio = np.frombuffer(raw, dtype=np.float32).reshape(-1, src_ch)
                    if dst_ch == 1:
                        audio = audio.mean(axis=1, keepdims=True)
                    elif dst_ch < src_ch:
                        # Mix down: average all source channels into dst_ch channels
                        audio = audio[:, :dst_ch] + audio[:, dst_ch:].reshape(len(audio), -1, dst_ch).mean(axis=1)
                        audio /= 2
                    else:
                        audio = np.repeat(audio[:, :1], dst_ch, axis=1)
                    raw = audio.astype(np.float32).tobytes()

                play_stream.write(raw)

        except Exception as e:
            print(f"Audio mirroring error: {e}")
        finally:
            for s in [record_stream, play_stream]:
                if s:
                    try:
                        s.stop_stream()
                        s.close()
                    except Exception:
                        pass

        self.is_running = False

    def __del__(self):
        try:
            self._pa.terminate()
        except Exception:
            pass


# ── UI ────────────────────────────────────────────────────────────────────────

class AudioMirrorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AudioMirror")
        self.root.geometry("460x300")
        self.root.resizable(False, False)

        self.engine = AudioMirrorEngine()
        self.source_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")

        self._build_ui()
        self.refresh_devices()

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=20)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="AudioMirror", font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(0, 15))

        ttk.Label(main, text="Capture From (your default output)").pack(anchor="w")
        self.source_combo = ttk.Combobox(main, textvariable=self.source_var, state="readonly", width=54)
        self.source_combo.pack(anchor="w", pady=(4, 12))

        ttk.Label(main, text="Mirror To").pack(anchor="w")
        self.target_combo = ttk.Combobox(main, textvariable=self.target_var, state="readonly", width=54)
        self.target_combo.pack(anchor="w", pady=(4, 16))

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(0, 12))

        self.start_btn = ttk.Button(btn_frame, text="Start Mirroring", command=self.start_mirroring)
        self.start_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = ttk.Button(btn_frame, text="Stop", command=self.stop_mirroring, state="disabled")
        self.stop_btn.pack(side="left")

        ttk.Button(btn_frame, text="Refresh", command=self.refresh_devices).pack(side="right")

        ttk.Separator(main).pack(fill="x", pady=8)

        sf = ttk.Frame(main)
        sf.pack(fill="x")
        ttk.Label(sf, text="Status: ").pack(side="left")
        ttk.Label(sf, textvariable=self.status_var).pack(side="left")

    def refresh_devices(self):
        loopbacks = self.engine.get_loopback_devices()
        outputs = self.engine.get_output_devices()

        loopback_names = [d["name"] for d in loopbacks]
        output_names = [d["name"] for d in outputs]

        self.source_combo["values"] = loopback_names
        self.target_combo["values"] = output_names

        default_lb = self.engine.get_default_loopback()
        if default_lb and default_lb["name"] in loopback_names:
            self.source_var.set(default_lb["name"])
        elif loopback_names:
            self.source_var.set(loopback_names[0])

        if output_names and not self.target_var.get():
            self.target_var.set(output_names[0])

        self.status_var.set("Ready")

    def start_mirroring(self):
        source = self.source_var.get()
        target = self.target_var.get()

        if not source or not target:
            messagebox.showwarning("Missing Selection", "Please select both a source and target device.")
            return

        try:
            self.engine.start(source, target)
            self.status_var.set("Mirroring ▶")
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start mirroring:\n{e}")
            self.status_var.set("Error")

    def stop_mirroring(self):
        try:
            self.engine.stop()
            self.status_var.set("Stopped ◼")
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to stop mirroring:\n{e}")
            self.status_var.set("Error")


if __name__ == "__main__":
    root = tk.Tk()
    app = AudioMirrorApp(root)
    root.mainloop()