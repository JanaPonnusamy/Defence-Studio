# Sample synthetic fixtures

These are **synthetic** example inputs for exercising the component offline.
The audio is generated locally (silent PCM WAV) so no binary is committed and
nothing is downloaded.

## Generate a runnable sample

```bash
python samples/make_sample.py --out-dir ./_sample_run
```

This writes, under the chosen input root:

- `audio/sample_en.wav` — 2.0s silent 16-bit PCM mono WAV
- `request_en.json` — an alignment request whose `audio_sha256` and
  `transcript_sha256` match the generated audio and the transcript below

`request_en.json` and `request_ta.json` here are templates showing the request
shape; `make_sample.py` fills in the correct hashes for a freshly generated
WAV (hashes depend on the exact audio bytes, so they must be computed, never
hand-copied).

The sample uses `config.engine = "deterministic-test"` with
`allow_synthetic: true`, i.e. the **synthetic** test aligner. Its timings are
reproducible test data, not measured alignment of real speech.
