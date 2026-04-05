import soundcard as sc


def get_default_speaker():
    """Return the current default speaker device."""
    try:
        return sc.default_speaker()
    except Exception:
        return None


def get_all_speakers():
    """Return all available speaker/output devices."""
    try:
        return sc.all_speakers()
    except Exception:
        return []


def get_speaker_names():
    """Return a list of speaker names."""
    speakers = get_all_speakers()
    return [speaker.name for speaker in speakers]


def find_speaker_by_name(name: str):
    """Find and return a speaker object by its name."""
    for speaker in get_all_speakers():
        if speaker.name == name:
            return speaker
    return None