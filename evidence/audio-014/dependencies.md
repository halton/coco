# audio-014 runtime dependencies

These must be installed in the .venv before running verify_audio_014 or executing edge backend:

- edge-tts>=7.0.0
- soundfile>=0.12

Install:
    .venv/bin/python -m pip install 'edge-tts>=7.0.0' 'soundfile>=0.12'

Or via project extras (note: -e .[tts-online] may not always persist; pin direct):
    .venv/bin/python -m pip install -e '.[tts-online]'

Verify:
    .venv/bin/python -c "import edge_tts, soundfile"
