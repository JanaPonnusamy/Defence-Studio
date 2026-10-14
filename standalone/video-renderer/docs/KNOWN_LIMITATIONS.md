# Known limitations (Phase R1)

## Scope - not implemented (by design)
* **No subtitles / captions** - the timeline has none; none are drawn.
* **No Tamil (or any non-Latin) text rendering.** Non-Latin overlay text is replaced by an explicit notice and flagged `OVERLAY_NON_LATIN`. No font is bundled or downloaded. Latin text uses an installed TrueType font (Arial/Segoe UI/Calibri/DejaVu or `RENDERER_FONT_PATH`), else Pillow's built-in font (`FONT_FALLBACK` warning).
* **No music, branding, intro/outro, thumbnails, audio generation or alignment.**
* **No UI.** API only.
* **No attribution/credits card.** The timeline's `attributions[]` stay in the package but are not rendered; `source_caption` / `rights_warning` overlays are. A publisher who needs credits for `attribution_required` assets must add them separately.
* **Still images only.** `VIDEO_CLIP` / video assets are rejected at package validation (unsupported mime type).

## Fidelity
* Motions `PARALLAX` and `HIGHLIGHT_REGION` fall back to `STATIC` (warning). Easing other than linear is ignored.
* Transitions: `CUT`, `FADE`, `DISSOLVE` only (`FADE` and `DISSOLVE` are both a crossfade). `FADE_IN`, `FADE_OUT`, `WIPE` and unknown values become `CUT` (warning). The VVM's default first-shot `FADE_IN` is therefore a cut. Durations are a renderer default (0.5 s), as the timeline defines none.
* Crossfade freezes the outgoing shot at its final frame while the incoming shot plays (no overlap in source time).
* Shots shorter than one output frame are dropped (warning). Very short shots (< 2 frames) cannot crossfade.
* Output is always H.264 High / yuv420p / BT.709 + AAC 48 kHz (mono narration stays mono).
* Frame accuracy: `frames = round(duration * fps)`; a narration that is not an exact frame multiple is rendered to the nearest frame, so the video can differ from the audio by up to half a frame (<= 20 ms at 25 fps) - well inside the +/-0.1 s check.

## Operational
* **ffmpeg is supplied by you** and never bundled/redistributed (see licence note). Without it, `/health` reports `NOT_AVAILABLE` and jobs fail with `FFMPEG_NOT_AVAILABLE`.
* **Single machine, no auth.** Intended for localhost. `package_path` reads a local folder (after manifest validation). Do not expose the port.
* One render at a time by default; no GPU encoding; no resumable/partial renders (a crashed job is retried from the start as a new job).
* Peak ffmpeg memory is sampled just before the pipe closes (Windows `PeakWorkingSetSize`); on non-Windows the ffmpeg figure is `null`.
* Orphaned-ffmpeg cleanup after a crash inspects the process command line via PowerShell `Get-CimInstance` (Windows) or `/proc` (Linux); on other systems the orphan simply exits on the broken pipe.
* Symlink-escape handling is unit-tested but the test is skipped on Windows accounts without symlink privilege.
* Tested only on Windows 10, Python 3.13, ffmpeg 8.1. Linux/macOS paths exist but are unverified.
* The upload endpoint buffers the zip in `storage/` (2 GB cap) and does not clean old `storage/packages/up_*` folders.

## Licence note (ffmpeg)
The renderer only *invokes* an ffmpeg you installed; it neither bundles nor links it. The build on the test machine is
`--enable-gpl --enable-version3 --enable-libx264` (GPLv3). Output files are your own work product, but redistributing that
ffmpeg binary (or an installer containing it) carries GPLv3 obligations, and libx264 has its own commercial-licensing
position. If you ever ship a product containing ffmpeg, use an LGPL build and a compatible encoder, or obtain legal advice.
`/health` and every `qc_report.json` record the detected licence flags (`gpl`, `version3`, `nonfree`). This is an engineering
note, not legal advice. Consistent with the Video Visual Module's `LICENSING_AUDIT.md` policy.
