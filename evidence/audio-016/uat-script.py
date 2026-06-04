"""audio-016 UAT script: print resolved mic input + tts output device + full device list."""
import sounddevice as sd

from coco.audio_device import resolve_input_device
from coco.tts import _resolve_tts_output_device

print("input  ->", resolve_input_device())
print("output ->", _resolve_tts_output_device())
print("devices:")
for i, d in enumerate(sd.query_devices()):
    print(f"  [{i}] {d['name']} in={d['max_input_channels']} out={d['max_output_channels']} sr={d['default_samplerate']}")
