import threading
import pyaudiowpatch as pyaudio


class AudioMirrorEngine:
    def __init__(self):
        self.thread = None
        self.stop_event = threading.Event()
        self.is_running = False
        self._pa = pyaudio.PyAudio()

    def _get_all_output_devices(self):
        devices = []
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            if info.get("maxOutputChannels", 0) > 0:
                devices.append(info)
        return devices

    def _get_default_output_device(self):
        try:
            return self._pa.get_default_wasapi_loopback()
        except Exception:
            return None

    def get_default_speaker(self):
        return self._get_default_output_device()

    def get_all_speakers(self):
        return self._get_all_output_devices()

    def get_speaker_names(self):
        return [d["name"] for d in self.get_all_speakers()]

    def find_speaker_by_name(self, name: str):
        for d in self.get_all_speakers():
            if d["name"] == name:
                return d
        return None

    def start(self, target_speaker_name: str):
        if self.is_running:
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._mirror_loop,
            args=(target_speaker_name,),
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

    def _mirror_loop(self, target_speaker_name: str):
        default_loopback = self._get_default_output_device()
        target_device = self.find_speaker_by_name(target_speaker_name)

        if default_loopback is None:
            print("No loopback device found.")
            self.is_running = False
            return

        if target_device is None:
            print(f"Target speaker '{target_speaker_name}' not found.")
            self.is_running = False
            return

        # Use the loopback device's native format to avoid resampling issues
        sample_rate = int(default_loopback["defaultSampleRate"])
        channels = min(int(default_loopback["maxInputChannels"]), 2)
        frames_per_buffer = 512
        fmt = pyaudio.paFloat32

        record_stream = None
        play_stream = None

        try:
            record_stream = self._pa.open(
                format=fmt,
                channels=channels,
                rate=sample_rate,
                input=True,
                input_device_index=default_loopback["index"],
                frames_per_buffer=frames_per_buffer,
            )

            play_stream = self._pa.open(
                format=fmt,
                channels=channels,
                rate=sample_rate,
                output=True,
                output_device_index=target_device["index"],
                frames_per_buffer=frames_per_buffer,
            )

            while not self.stop_event.is_set():
                data = record_stream.read(frames_per_buffer, exception_on_overflow=False)
                play_stream.write(data)

        except Exception as e:
            print(f"Audio mirroring error: {e}")
        finally:
            if record_stream:
                try:
                    record_stream.stop_stream()
                    record_stream.close()
                except Exception:
                    pass
            if play_stream:
                try:
                    play_stream.stop_stream()
                    play_stream.close()
                except Exception:
                    pass

        self.is_running = False

    def __del__(self):
        try:
            self._pa.terminate()
        except Exception:
            pass