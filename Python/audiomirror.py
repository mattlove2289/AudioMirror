import threading
import numpy as np
import customtkinter as ctk
from tkinter import messagebox
import pyaudiowpatch as pyaudio
from PIL import Image, ImageDraw
import io

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


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
        fmt = pyaudio.paFloat32
        info = self._pa.get_device_info_by_index(device_index)
        max_ch = int(info["maxInputChannels"] if is_input else info["maxOutputChannels"])
        for ch in [max_ch, 8, 4, 2, 1]:
            if ch > max_ch or ch < 1:
                continue
            try:
                kwargs = dict(format=fmt, channels=ch, rate=int(sample_rate), frames_per_buffer=1024)
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

    def start(self, source_name, target_name):
        if self.is_running:
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._mirror_loop, args=(source_name, target_name), daemon=True)
        self.thread.start()
        self.is_running = True

    def stop(self):
        if not self.is_running:
            return
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        self.is_running = False

    def _mirror_loop(self, source_name, target_name):
        source = self.find_loopback_by_name(source_name)
        target = self.find_output_by_name(target_name)

        if source is None or target is None:
            print("Error: Device not found.")
            self.is_running = False
            return

        sample_rate = int(source["defaultSampleRate"])
        src_ch = self._find_working_channel_count(source["index"], sample_rate, is_input=True)
        dst_ch = self._find_working_channel_count(target["index"], sample_rate, is_input=False)

        if src_ch is None or dst_ch is None:
            print("Error: Could not open device.")
            self.is_running = False
            return

        frames = 256
        fmt = pyaudio.paFloat32
        record_stream = None
        play_stream = None

        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadPriority(
                ctypes.windll.kernel32.GetCurrentThread(), 2
            )
        except Exception:
            pass

        try:
            record_stream = self._pa.open(
                format=fmt, channels=src_ch, rate=sample_rate,
                input=True, input_device_index=source["index"], frames_per_buffer=frames,
            )
            play_stream = self._pa.open(
                format=fmt, channels=dst_ch, rate=sample_rate,
                output=True, output_device_index=target["index"], frames_per_buffer=frames,
            )

            while not self.stop_event.is_set():
                raw = record_stream.read(frames, exception_on_overflow=False)
                if src_ch != dst_ch:
                    audio = np.frombuffer(raw, dtype=np.float32).reshape(-1, src_ch)
                    if dst_ch == 1:
                        audio = audio.mean(axis=1, keepdims=True)
                    elif dst_ch < src_ch:
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


# ── Refresh icon ──────────────────────────────────────────────────────────────

def make_refresh_icon(size=20, color="#9ca3af"):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy, r = size // 2, size // 2, size // 2 - 2
    draw.arc([cx - r, cy - r, cx + r, cy + r], start=30, end=300, fill=color, width=2)
    # Arrow head
    draw.polygon([(cx + r - 1, cy - 5), (cx + r + 4, cy - 1), (cx + r - 1, cy + 3)], fill=color)
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))


# ── UI ────────────────────────────────────────────────────────────────────────

class AudioMirrorApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("AudioMirror")
        self.geometry("500x460")
        self.resizable(False, False)
        self.configure(fg_color="#111827")

        self.engine = AudioMirrorEngine()
        self.source_var = ctk.StringVar()
        self.target_var = ctk.StringVar()

        self._build_ui()
        self.refresh_devices()

    def _build_ui(self):
        # Outer padding frame
        outer = ctk.CTkFrame(self, fg_color="#111827")
        outer.pack(fill="both", expand=True, padx=20, pady=20)

        # Card
        card = ctk.CTkFrame(outer, corner_radius=16, fg_color="#1e2433", border_width=1, border_color="#2d3448")
        card.pack(fill="both", expand=True)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=28, pady=24)

        # ── Header ──
        header_row = ctk.CTkFrame(inner, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            header_row, text="⬡",
            font=ctk.CTkFont(size=20),
            text_color="#4f8ef7"
        ).pack(side="left", padx=(0, 10))

        ctk.CTkLabel(
            header_row, text="AudioMirror",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#f9fafb"
        ).pack(side="left")

        ctk.CTkLabel(
            inner,
            text="Route your audio to a second output device",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#6b7280"
        ).pack(anchor="w", pady=(0, 16))

        # Divider
        ctk.CTkFrame(inner, height=1, fg_color="#2d3448").pack(fill="x", pady=(0, 20))

        # ── Capture From ──
        ctk.CTkLabel(
            inner, text="CAPTURE FROM",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color="#4f8ef7"
        ).pack(anchor="w", pady=(0, 6))

        self.source_combo = ctk.CTkOptionMenu(
            inner,
            variable=self.source_var,
            values=[],
            height=44,
            corner_radius=8,
            fg_color="#0d1117",
            button_color="#2d3448",
            button_hover_color="#374151",
            text_color="#e5e7eb",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            dropdown_fg_color="#1e2433",
            dropdown_hover_color="#2d3448",
            dropdown_text_color="#e5e7eb",
        )
        self.source_combo.pack(fill="x", pady=(0, 20))

        # ── Mirror To ──
        ctk.CTkLabel(
            inner, text="MIRROR TO",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color="#4f8ef7"
        ).pack(anchor="w", pady=(0, 6))

        self.target_combo = ctk.CTkOptionMenu(
            inner,
            variable=self.target_var,
            values=[],
            height=44,
            corner_radius=8,
            fg_color="#0d1117",
            button_color="#2d3448",
            button_hover_color="#374151",
            text_color="#e5e7eb",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            dropdown_fg_color="#1e2433",
            dropdown_hover_color="#2d3448",
            dropdown_text_color="#e5e7eb",
        )
        self.target_combo.pack(fill="x", pady=(0, 24))

        # ── Buttons ──
        btn_row = ctk.CTkFrame(inner, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 20))

        self.start_btn = ctk.CTkButton(
            btn_row, text="Start Mirroring",
            command=self.start_mirroring,
            height=44,
            corner_radius=8,
            fg_color="#3b82f6",
            hover_color="#2563eb",
            text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
        )
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            btn_row, text="Stop",
            command=self.stop_mirroring,
            height=44,
            corner_radius=8,
            fg_color="#2d3448",
            hover_color="#374151",
            text_color="#9ca3af",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            state="disabled",
        )
        self.stop_btn.pack(side="left", expand=True, fill="x", padx=(0, 8))

        self.refresh_icon = make_refresh_icon(18, "#9ca3af")
        self.refresh_btn = ctk.CTkButton(
            btn_row,
            text="",
            image=self.refresh_icon,
            command=self.refresh_devices,
            height=44,
            width=44,
            corner_radius=8,
            fg_color="#2d3448",
            hover_color="#374151",
        )
        self.refresh_btn.pack(side="left")

        # ── Status bar ──
        status_card = ctk.CTkFrame(inner, fg_color="#0d1117", corner_radius=8)
        status_card.pack(fill="x", pady=(0, 0))

        status_inner = ctk.CTkFrame(status_card, fg_color="transparent")
        status_inner.pack(padx=14, pady=12)

        self.status_dot = ctk.CTkLabel(
            status_inner, text="●",
            font=ctk.CTkFont(size=9),
            text_color="#374151",
        )
        self.status_dot.pack(side="left", padx=(0, 8))

        self.status_label = ctk.CTkLabel(
            status_inner, text="Ready",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#6b7280"
        )
        self.status_label.pack(side="left")

    def _set_status(self, text, color):
        self.status_label.configure(text=text, text_color=color)
        self.status_dot.configure(text_color=color)

    def refresh_devices(self):
        loopbacks = self.engine.get_loopback_devices()
        outputs = self.engine.get_output_devices()

        loopback_names = [d["name"] for d in loopbacks]
        output_names = [d["name"] for d in outputs]

        self.source_combo.configure(values=loopback_names)
        self.target_combo.configure(values=output_names)

        default_lb = self.engine.get_default_loopback()
        if default_lb and default_lb["name"] in loopback_names:
            self.source_var.set(default_lb["name"])
        elif loopback_names:
            self.source_var.set(loopback_names[0])

        if output_names and not self.target_var.get():
            self.target_var.set(output_names[0])

        self._set_status("Ready", "#6b7280")

    def start_mirroring(self):
        source = self.source_var.get()
        target = self.target_var.get()

        if not source or not target:
            messagebox.showwarning("Missing Selection", "Please select both a source and target device.")
            return

        try:
            self.engine.start(source, target)
            self._set_status("Mirroring active", "#22c55e")
            self.start_btn.configure(state="disabled", fg_color="#1a3a2e", text_color="#4ade80")
            self.stop_btn.configure(state="normal", fg_color="#3a1a1a", text_color="#ef4444")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start mirroring:\n{e}")
            self._set_status("Error", "#ef4444")

    def stop_mirroring(self):
        try:
            self.engine.stop()
            self._set_status("Stopped", "#6b7280")
            self.start_btn.configure(state="normal", fg_color="#3b82f6", text_color="#ffffff")
            self.stop_btn.configure(state="disabled", fg_color="#2d3448", text_color="#9ca3af")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to stop mirroring:\n{e}")
            self._set_status("Error", "#ef4444")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = AudioMirrorApp()
    app.mainloop()