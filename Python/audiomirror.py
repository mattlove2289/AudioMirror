import queue
import threading
import numpy as np
import customtkinter as ctk
from tkinter import messagebox
import pyaudiowpatch as pyaudio
import darkdetect

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")


# ── Audio Engine ──────────────────────────────────────────────────────────────

class AudioMirrorEngine:
    def __init__(self):
        self.is_running = False
        self.is_muted = False
        self._pa = pyaudio.PyAudio()
        self._stop_event = threading.Event()
        self._read_thread = None
        self._write_thread = None
        self._queue = queue.Queue(maxsize=4)  # Small queue = low latency, no buildup

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

    def _find_working_channels(self, device_index, sample_rate, is_input=True):
        info = self._pa.get_device_info_by_index(device_index)
        max_ch = int(info["maxInputChannels"] if is_input else info["maxOutputChannels"])
        for ch in [max_ch, 8, 4, 2, 1]:
            if ch > max_ch or ch < 1:
                continue
            try:
                kwargs = dict(format=pyaudio.paFloat32, channels=ch, rate=int(sample_rate), frames_per_buffer=512)
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

    def set_muted(self, muted):
        self.is_muted = muted

    def start(self, source_name, target_name):
        if self.is_running:
            return

        source = self.find_loopback_by_name(source_name)
        target = self.find_output_by_name(target_name)

        if source is None or target is None:
            raise RuntimeError("Device not found.")

        sample_rate = int(source["defaultSampleRate"])
        src_ch = self._find_working_channels(source["index"], sample_rate, is_input=True)
        dst_ch = self._find_working_channels(target["index"], sample_rate, is_input=False)

        if src_ch is None or dst_ch is None:
            raise RuntimeError("Could not open audio device.")

        self._stop_event.clear()
        # Clear any stale data in the queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Exception:
                break

        self._read_thread = threading.Thread(
            target=self._reader,
            args=(source["index"], sample_rate, src_ch, dst_ch),
            daemon=True
        )
        self._write_thread = threading.Thread(
            target=self._writer,
            args=(target["index"], sample_rate, dst_ch),
            daemon=True
        )

        self._read_thread.start()
        self._write_thread.start()
        self.is_running = True

    def stop(self):
        if not self.is_running:
            return
        self._stop_event.set()
        # Unblock the writer if it's waiting on the queue
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        if self._read_thread:
            self._read_thread.join(timeout=3)
        if self._write_thread:
            self._write_thread.join(timeout=3)
        self.is_running = False

    def _reader(self, device_index, sample_rate, src_ch, dst_ch):
        """Reads from loopback, converts channels, pushes to queue."""
        frames = 512

        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadPriority(
                ctypes.windll.kernel32.GetCurrentThread(), 2
            )
        except Exception:
            pass

        try:
            stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=src_ch,
                rate=sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=frames,
            )

            while not self._stop_event.is_set():
                raw = stream.read(frames, exception_on_overflow=False)

                # Channel conversion
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

                # Drop oldest chunk if queue is full to prevent latency buildup
                if self._queue.full():
                    try:
                        self._queue.get_nowait()
                    except Exception:
                        pass

                try:
                    self._queue.put_nowait(raw)
                except Exception:
                    pass

        except Exception as e:
            print(f"Reader error: {e}")
        finally:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass

    def _writer(self, device_index, sample_rate, dst_ch):
        """Pulls from queue, writes to output device."""
        frames = 512
        silence = b'\x00' * frames * dst_ch * 4

        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadPriority(
                ctypes.windll.kernel32.GetCurrentThread(), 2
            )
        except Exception:
            pass

        try:
            stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=dst_ch,
                rate=sample_rate,
                output=True,
                output_device_index=device_index,
                frames_per_buffer=frames,
            )

            while not self._stop_event.is_set():
                try:
                    raw = self._queue.get(timeout=0.1)
                except queue.Empty:
                    stream.write(silence)
                    continue

                if raw is None:
                    break

                if self.is_muted:
                    stream.write(silence)
                else:
                    stream.write(raw)

        except Exception as e:
            print(f"Writer error: {e}")
        finally:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass

    def __del__(self):
        try:
            self.stop()
            self._pa.terminate()
        except Exception:
            pass


# ── UI ────────────────────────────────────────────────────────────────────────

class AudioMirrorApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("AudioMirror")
        self.geometry("480x400")
        self.resizable(False, False)

        self.engine = AudioMirrorEngine()
        self.source_var = ctk.StringVar()
        self.target_var = ctk.StringVar()
        self._muted = False
        self._mirroring = False

        self._build_ui()
        self.refresh_devices()

    def _dark(self):
        return darkdetect.isDark()

    def _build_ui(self):
        dark = self._dark()

        bg          = "#1c1c1e" if dark else "#f2f2f7"
        card_bg     = "#2c2c2e" if dark else "#ffffff"
        border      = "#3a3a3c" if dark else "#d1d1d6"
        input_bg    = "#1c1c1e" if dark else "#f2f2f7"
        text        = "#ffffff" if dark else "#000000"
        subtext     = "#8e8e93" if dark else "#6c6c70"
        section_lbl = "#636366" if dark else "#8e8e93"
        drop_bg     = "#2c2c2e" if dark else "#ffffff"
        btn_sec_bg  = "#3a3a3c" if dark else "#e5e5ea"
        btn_sec_hov = "#48484a" if dark else "#d1d1d6"
        divider     = "#3a3a3c" if dark else "#e5e5ea"

        self._btn_sec_bg  = btn_sec_bg
        self._btn_sec_hov = btn_sec_hov
        self._subtext     = subtext

        self.configure(fg_color=bg)

        wrapper = ctk.CTkFrame(self, fg_color=bg)
        wrapper.pack(fill="both", expand=True, padx=20, pady=20)

        card = ctk.CTkFrame(wrapper, fg_color=card_bg, corner_radius=14, border_width=1, border_color=border)
        card.pack(fill="both", expand=True)

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=24)

        # ── Header ──
        ctk.CTkLabel(
            body, text="AudioMirror",
            font=ctk.CTkFont(family="SF Pro Display", size=17, weight="bold"),
            text_color=text
        ).pack(anchor="w")

        ctk.CTkLabel(
            body, text="Play audio through two devices simultaneously",
            font=ctk.CTkFont(family="SF Pro Text", size=12),
            text_color=subtext
        ).pack(anchor="w", pady=(2, 16))

        ctk.CTkFrame(body, height=1, fg_color=divider).pack(fill="x", pady=(0, 20))

        # ── Capture From (read-only) ──
        ctk.CTkLabel(
            body, text="CAPTURE FROM",
            font=ctk.CTkFont(family="SF Pro Text", size=10, weight="bold"),
            text_color=section_lbl
        ).pack(anchor="w", pady=(0, 5))

        source_box = ctk.CTkFrame(body, fg_color=input_bg, corner_radius=8, height=36)
        source_box.pack(fill="x", pady=(0, 16))
        source_box.pack_propagate(False)
        ctk.CTkLabel(
            source_box, textvariable=self.source_var,
            font=ctk.CTkFont(family="SF Pro Text", size=13),
            text_color=subtext, anchor="w",
        ).pack(side="left", padx=12, fill="y")

        # ── Mirror To ──
        ctk.CTkLabel(
            body, text="MIRROR TO",
            font=ctk.CTkFont(family="SF Pro Text", size=10, weight="bold"),
            text_color=section_lbl
        ).pack(anchor="w", pady=(0, 5))

        self.target_combo = ctk.CTkOptionMenu(
            body, variable=self.target_var, values=[],
            height=36, corner_radius=8,
            fg_color=input_bg,
            button_color=btn_sec_bg,
            button_hover_color=btn_sec_hov,
            text_color=text,
            font=ctk.CTkFont(family="SF Pro Text", size=13),
            dropdown_fg_color=drop_bg,
            dropdown_hover_color=btn_sec_bg,
            dropdown_text_color=text,
        )
        self.target_combo.pack(fill="x", pady=(0, 24))

        # ── Buttons ──
        btn_row = ctk.CTkFrame(body, fg_color="transparent")
        btn_row.pack(fill="x")
        btn_row.grid_columnconfigure(0, weight=1)
        btn_row.grid_columnconfigure(1, weight=0)
        btn_row.grid_columnconfigure(2, weight=0)

        self.toggle_btn = ctk.CTkButton(
            btn_row, text="Start Mirroring",
            command=self.toggle_mirroring,
            height=36, corner_radius=8,
            fg_color="#0a84ff", hover_color="#0070d8",
            text_color="#ffffff",
            font=ctk.CTkFont(family="SF Pro Text", size=13, weight="bold"),
        )
        self.toggle_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.mute_btn = ctk.CTkButton(
            btn_row, text="Mute Output",
            command=self.toggle_mute,
            height=36, corner_radius=8,
            fg_color=btn_sec_bg, hover_color=btn_sec_hov,
            text_color=subtext,
            font=ctk.CTkFont(family="SF Pro Text", size=13),
            state="disabled",
            width=110,
        )
        self.mute_btn.grid(row=0, column=1, padx=(0, 8))
        self.mute_btn.grid_remove()

        self.refresh_btn = ctk.CTkButton(
            btn_row, text="Refresh",
            command=self.refresh_devices,
            height=36, corner_radius=8,
            fg_color=btn_sec_bg, hover_color=btn_sec_hov,
            text_color=subtext,
            font=ctk.CTkFont(family="SF Pro Text", size=13),
            width=75,
        )
        self.refresh_btn.grid(row=0, column=2)

    def _get_filtered_target_names(self):
        source_base = self.source_var.get().replace(" [Loopback]", "").strip()
        return [
            d["name"] for d in self.engine.get_output_devices()
            if source_base not in d["name"]
        ]

    def refresh_devices(self):
        loopbacks = self.engine.get_loopback_devices()
        loopback_names = [d["name"] for d in loopbacks]

        default_lb = self.engine.get_default_loopback()
        if default_lb and default_lb["name"] in loopback_names:
            self.source_var.set(default_lb["name"])
        elif loopback_names:
            self.source_var.set(loopback_names[0])

        target_names = self._get_filtered_target_names()
        self.target_combo.configure(values=target_names)
        if target_names:
            self.target_var.set(target_names[0])

    def toggle_mirroring(self):
        if not self._mirroring:
            self._start()
        else:
            self._stop()

    def _start(self):
        source = self.source_var.get()
        target = self.target_var.get()

        if not source or not target:
            messagebox.showwarning("Missing Selection", "No devices found.")
            return

        try:
            self.engine.start(source, target)
            self._mirroring = True
            self.toggle_btn.configure(
                text="Stop Mirroring",
                fg_color="#2a2a2e", hover_color="#3a3a3e",
                text_color="#ff453a",
            )
            self.mute_btn.grid()
            self.mute_btn.configure(state="normal")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start mirroring:\n{e}")

    def _stop(self):
        try:
            self.engine.stop()
            self._mirroring = False
            self._muted = False
            self.engine.set_muted(False)
            self.toggle_btn.configure(
                text="Start Mirroring",
                fg_color="#0a84ff", hover_color="#0070d8",
                text_color="#ffffff",
            )
            self.mute_btn.grid_remove()
            self.mute_btn.configure(
                state="disabled", text="Mute Output",
                fg_color=self._btn_sec_bg, text_color=self._subtext,
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to stop mirroring:\n{e}")

    def toggle_mute(self):
        self._muted = not self._muted
        self.engine.set_muted(self._muted)
        if self._muted:
            self.mute_btn.configure(text="Unmute Output", fg_color="#3a1a1a", text_color="#ff453a")
        else:
            self.mute_btn.configure(text="Mute Output", fg_color=self._btn_sec_bg, text_color=self._subtext)


if __name__ == "__main__":
    app = AudioMirrorApp()
    app.mainloop()