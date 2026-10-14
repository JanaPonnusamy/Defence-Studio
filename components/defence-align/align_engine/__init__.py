"""Defence Studio Phase 4, Component D — standalone word-level audio alignment.

Maps a real narration transcript to timing information derived from actual
audio. This package is intentionally independent: it imports nothing from
Defence Studio Phase 1-3 services, nor from the TTS component. A future TTS
module connects to it only through the versioned JSON contracts in
``align_engine.contracts`` (see ``INTEGRATION_CONTRACT.md``).

It never synthesizes speech, generates narration, alters scripts, renders
video, or publishes content.
"""

__version__ = "0.1.0"
ENGINE_SUITE_VERSION = "defence-align/0.1.0"
