# audio-014 runtime dependencies

These must be installed in the .venv before running verify_audio_014 or executing edge backend:

- edge-tts>=7.0.0  (installed: 7.2.8)
- soundfile>=0.12  (installed: 0.13.1)

## Verified install command (P0 fix 2026-06-04)

If pip shebang is broken (e.g. points to a legacy/missing python path such as
`/Users/halton/work/reachhy-mini/.venv/bin/python`), the `.venv/bin/pip` script
will silently fail to install into the current venv. Always invoke pip via
`-m pip` from the project venv's python:

    /Users/halton/work/coco/.venv/bin/python -m pip install --upgrade 'edge-tts>=7.0.0' 'soundfile>=0.12'

If pip itself is broken, repair it first (this rewrites pip's shebang to the
current venv python):

    /Users/halton/work/coco/.venv/bin/python -m pip install --upgrade --force-reinstall pip

Or via project extras (note: -e .[tts-online] may not always persist; pin direct):

    /Users/halton/work/coco/.venv/bin/python -m pip install -e '.[tts-online]'

## Verify installation lands in the right place

    /Users/halton/work/coco/.venv/bin/python -c "import edge_tts, soundfile, sys; \
print(edge_tts.__file__); print(soundfile.__file__); print(sys.prefix)"

Both `__file__` paths MUST be under `/Users/halton/work/coco/.venv/lib/python3.13/site-packages/`.
