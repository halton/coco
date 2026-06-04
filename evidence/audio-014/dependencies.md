# audio-014 runtime dependencies

These must be installed in the .venv before running verify_audio_014 or executing edge backend:

- edge-tts>=7.0.0  (installed: 7.2.8)
- soundfile>=0.12  (installed: 0.13.1)

## Persistence fix (P0 round-3, 2026-06-04)

Root cause: `.venv` is managed by `uv` (pyvenv.cfg `uv = 0.11.2`) and
`./init.sh` runs `uv sync` on every invocation. `uv sync` removes any
installed package not declared in `[project] dependencies` of pyproject.toml.
edge-tts/soundfile were only listed under `[project.optional-dependencies]
tts-online`, so every `./init.sh` (and every implicit sync) silently
uninstalled them — explaining the round-2/round-3 "package vanishes between
reviews" symptom with no install between them.

Fix: promote both to main `[project] dependencies` via:

    uv add 'edge-tts>=7.0.0' 'soundfile>=0.12.0'

This updates pyproject.toml + uv.lock atomically, so subsequent `uv sync`
keeps them. Verified persistence by running `./init.sh` (full smoke including
`uv sync`) then re-importing — both still importable after smoke. See
smoke-pass.log + verify-pass.log (this PR).

## Legacy install instructions (kept for reference if uv is unavailable)

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
