import pyaudiowpatch as pyaudio

pa = pyaudio.PyAudio()

formats = {
    "paFloat32": pyaudio.paFloat32,
    "paInt32": pyaudio.paInt32,
    "paInt24": pyaudio.paInt24,
    "paInt16": pyaudio.paInt16,
}

for fname, fmt in formats.items():
    for ch in [1, 2, 4, 8]:
        try:
            s = pa.open(
                format=fmt,
                channels=ch,
                rate=48000,
                input=True,
                input_device_index=19,
                frames_per_buffer=1024,
            )
            s.stop_stream()
            s.close()
            print(f"✓ {fname}, {ch}ch @ 48000")
        except Exception as e:
            print(f"✗ {fname}, {ch}ch @ 48000 — {e}")