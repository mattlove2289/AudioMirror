import tkinter as tk
from tkinter import ttk, messagebox

from audio import get_default_speaker, get_speaker_names


class AudioMirrorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AudioMirror")
        self.root.geometry("420x260")
        self.root.resizable(False, False)

        self.is_running = False

        self.default_device_var = tk.StringVar(value="Detecting...")
        self.output_device_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")

        self._build_ui()
        self.refresh_devices()

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=20)
        main.pack(fill="both", expand=True)

        title = ttk.Label(main, text="AudioMirror", font=("Segoe UI", 16, "bold"))
        title.pack(anchor="w", pady=(0, 15))

        ttk.Label(main, text="Default Output").pack(anchor="w")
        self.default_label = ttk.Label(
            main,
            textvariable=self.default_device_var,
            font=("Segoe UI", 10)
        )
        self.default_label.pack(anchor="w", pady=(2, 12))

        ttk.Label(main, text="Mirror To").pack(anchor="w")
        self.output_combo = ttk.Combobox(
            main,
            textvariable=self.output_device_var,
            state="readonly",
            width=50
        )
        self.output_combo.pack(anchor="w", pady=(4, 16))

        button_frame = ttk.Frame(main)
        button_frame.pack(fill="x", pady=(0, 16))

        self.start_button = ttk.Button(
            button_frame,
            text="Start Mirroring",
            command=self.start_mirroring
        )
        self.start_button.pack(side="left", padx=(0, 10))

        self.stop_button = ttk.Button(
            button_frame,
            text="Stop",
            command=self.stop_mirroring,
            state="disabled"
        )
        self.stop_button.pack(side="left")

        ttk.Separator(main).pack(fill="x", pady=10)

        status_frame = ttk.Frame(main)
        status_frame.pack(fill="x")

        ttk.Label(status_frame, text="Status: ").pack(side="left")
        ttk.Label(status_frame, textvariable=self.status_var).pack(side="left")

        refresh_button = ttk.Button(
            main,
            text="Refresh Devices",
            command=self.refresh_devices
        )
        refresh_button.pack(anchor="e", pady=(10, 0))

    def refresh_devices(self):
        default_speaker = get_default_speaker()
        speaker_names = get_speaker_names()

        if default_speaker:
            self.default_device_var.set(default_speaker.name)
        else:
            self.default_device_var.set("No default device found")

        self.output_combo["values"] = speaker_names

        if speaker_names:
            if not self.output_device_var.get():
                self.output_device_var.set(speaker_names[0])

        self.status_var.set("Ready")

    def start_mirroring(self):
        selected_output = self.output_device_var.get()

        if not selected_output:
            messagebox.showwarning("No Output Selected", "Please select an output device.")
            return

        if selected_output == self.default_device_var.get():
            messagebox.showwarning(
                "Same Device Selected",
                "Please choose a different output device to mirror to."
            )
            return

        self.is_running = True
        self.status_var.set("Mirroring")
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")

    def stop_mirroring(self):
        self.is_running = False
        self.status_var.set("Stopped")
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")