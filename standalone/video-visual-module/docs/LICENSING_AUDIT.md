# Licensing Audit: FFmpeg (via imageio-ffmpeg), PDFium (via pypdfium2), Playwright

Date: 2026-10-12 (independent audit). Module version 1.1.0.

> **This is a technical analysis, not legal advice.** It records what is actually in the installed binaries and what the licences, read plainly, require. Before distributing anything, have a qualified person confirm the conclusions, especially the patent questions.

## 1. The ffmpeg binary actually used

`app/pipeline/media_probe.py` calls `imageio_ffmpeg.get_ffmpeg_exe()` and runs that executable as a **separate process** for video frames and probing. That function returns the first match from this list:
1. the `IMAGEIO_FFMPEG_EXE` environment variable
2. the bundled binary
3. conda
4. the system ffmpeg

`IMAGEIO_FFMPEG_EXE` is not set on this machine. The system ffmpeg in `D:\ffmpeg\bin` is **not** used.

| Item | Value (measured) |
|---|---|
| Package | `imageio-ffmpeg` 0.6.0 (`.venv`) |
| Binary | `.venv\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe` |
| Size / SHA-256 | 87,638,016 bytes / `2CE797A0F88D7F067180338FB227F7B1928EA727BD9A4D7A1D022F7C52AF71A3` |
| `-version` | `ffmpeg version 7.1-essentials_build-www.gyan.dev`, built with gcc 14.2.0 (MSYS2) |
| Build origin | gyan.dev "essentials" build: Windows, 64-bit, **static** |

### Build configuration (`-buildconf`)

- **Licence-relevant flags:**
  - `--enable-gpl` **yes**
  - `--enable-version3` **yes**
  - `--enable-nonfree` **no**
  - `--enable-static`
- **Libraries:**
  - **GPL-only components:** `libx264`, `libx265`, `libxvid`, `libvidstab`, `librubberband`. These require `--enable-gpl`.
  - **Components that need `--enable-version3`:** `libopencore-amrnb`, `libopencore-amrwb` and `libvo-amrwbenc` (Apache-2.0), and `gmp` (LGPLv3; present here via `--enable-gmp`).
  - **Others:** fontconfig, iconv, gnutls, libxml2, gmp, bzlib, lzma, zlib, libsrt, libssh, libzmq, avisynth, sdl2, libwebp, libaom, libopenjpeg, libvpx, mediafoundation, libass, libfreetype, libfribidi, libharfbuzz, libvmaf, libzimg, amf, cuda-llvm, cuvid, dxva2, d3d11va, d3d12va, ffnvcodec, libvpl, nvdec, nvenc, vaapi, libgme, libopenmpt, libmp3lame, libtheora, libgsm, libopus, libspeex, libvorbis.
- **`-L` (the licence the binary itself declares):** "ffmpeg is free software; you can redistribute it and/or modify it under the terms of the **GNU General Public License** … **either version 3** of the License, or (at your option) any later version."
- **Encoders present (`-encoders`):**
  - libx264 / libx264rgb (H.264), libx265 (HEVC), libxvid (MPEG-4 Part 2), aac
  - libmp3lame, libopencore_amrnb, libvo_amrwbenc
  - hardware H.264/HEVC encoders (nvenc, amf, qsv, mf, vaapi, d3d12va)

### Verdict

**The bundled binary is GPL-enabled, not LGPL-only.**
- It is distributed under **GPL-3.0-or-later** as a whole, because `--enable-gpl` and `--enable-version3` are both set and GPL libraries (x264, x265, xvid, vidstab, rubberband) are statically linked.
- FFmpeg's legal page says: "If those parts get used the GPL applies to all of FFmpeg."
- gyan.dev states: "All builds are 64-bit, static and licensed as GPLv3."
- It is **not** `nonfree`, so it is redistributable under the GPL.

The Phase 2 docs describe it as "LGPL/GPL", and `GET /capabilities` returns `"licence": "imageio-ffmpeg BSD-2-Clause; ffmpeg binary LGPL/GPL"`. That understates it: the binary is **GPLv3**. The code string was left unchanged, because the actual binary can be swapped via `IMAGEIO_FFMPEG_EXE`. The docs are corrected here and in `KNOWN_LIMITATIONS.md`.

## 2. What this means for the module

### Using it internally (current state)

Running GPL software, including invoking it as a subprocess, places **no** obligation on the module's own code. GPL obligations are triggered by **conveying (distributing)** the binary.

### How the module interacts with ffmpeg

- The module talks to ffmpeg only through **command-line arguments and pipes**, starting `ffmpeg.exe` as a separate program. It does not link libavcodec.
- Under the FSF's widely cited reading, a program that communicates with a separate GPL program by exec and pipes is generally an **"aggregate"**, not a derivative work. On that reading the module's Python code does not need to be GPL-licensed.
- This is the mainstream interpretation, but it is not settled law. It depends on how intimate the interaction is; here it is plain CLI use.

### LGPL "relinking"

This is **not the governing question here.** It would apply only if:
- the binary were LGPL-only, which it is not, and
- the module linked FFmpeg libraries into its own executable, which it does not.

For an executable invoked as a subprocess, the LGPL relinking clause (LGPL 2.1 §6) has nothing to bite on. The user can already replace `ffmpeg.exe`, either through `IMAGEIO_FFMPEG_EXE` or by swapping the file. Since the build is GPL, the GPLv3 obligations below apply instead.

### If the module is distributed with this binary

This covers shipping the `.venv`, a frozen build, an installer, or a Docker image containing `imageio_ffmpeg\binaries\ffmpeg-*.exe`. The distributor must meet **GPLv3 §§4–6 for that binary**:
1. **Licence text and notices.**
   - Ship the GPLv3 text alongside the binary.
   - Keep FFmpeg's and each bundled library's copyright notices.
   - State that the binary is GPLv3 and comes from the gyan.dev 7.1 essentials build.
   - **The imageio-ffmpeg wheel ships none of this.** It contains only its own BSD-2 `LICENSE` and a 45-byte `binaries/README.md`. Ship-as-is therefore does **not** meet the notice obligation today.
2. **Corresponding source** (GPLv3 §6). Either ship it with the binary, or give a written offer valid for 3 years, or use an equivalent §6 method such as hosting it at a stated location. It must cover:
   - FFmpeg 7.1 at the exact commit
   - **the source of every statically linked library**: x264, x265, xvid, vidstab, rubberband, opencore-amr, vo-amrwbenc, aom, vpx, gnutls and the rest
   - the build scripts and configuration
   
   gyan.dev links only the FFmpeg commit for its *current* builds; the 7.1 page has rotated away. Pointing to a third party's source is not reliably sufficient when you are the distributor. In practice you would have to **reassemble and host the complete source set yourself**, which is a significant effort for a static build with more than 40 libraries.
3. **No further restrictions.** The recipient's GPL rights cannot be limited, for example by an EULA that forbids reverse engineering of the binary. GPLv3 §7 and §10 apply.
4. **Installation information** (GPLv3 §6, "User Products"). This applies only if the binary ships inside a consumer hardware product. Not applicable here.
5. **imageio-ffmpeg's own licence (BSD-2).** Retain its copyright notice and disclaimer in the documentation or other materials of any binary redistribution. This is trivial.

### Patents and nonfree

- **The build is not nonfree.** It contains no fdk-aac, no libnpp and no other non-redistributable component.
- **Patents are a separate risk.** The binary contains **H.264 (x264), HEVC (x265), MPEG-4 Part 2 (xvid), AAC and AMR** encoders and decoders, which are covered by patent pools: Via LA (formerly MPEG LA), Access Advance and others.
  - FFmpeg's legal page: "we do not know" whether FFmpeg infringes; MPEG LA "is vigilant and diligent about collecting for MPEG-related technologies".
  - The module only **decodes frames to PNG** for previews and never encodes H.264 or HEVC. The patent exposure of commercially *distributing* encoders still depends on jurisdiction and volume and is not resolved by the GPL.
  - GPLv3 §11 also adds a patent clause that binds the distributor.

## 3. Other third-party components

| Component | Version | Licence | Scope | Notes |
|---|---|---|---|---|
| `pypdfium2` (+ `pypdfium2_raw`, bundled PDFium binary) | 5.14.0 | Apache-2.0 / BSD-3-Clause (bindings); CC-BY-4.0 (some data); PDFium BSD-3; bundled dependency licences | runtime (PDF previews) | The wheel ships **complete licence texts** for PDFium and its build dependencies (abseil, agg23, fast_float, freetype, icu, lcms, libjpeg-turbo, libopenjpeg, libpng, llvm-libc, simdutf, zlib, pdfium-binaries) in `BUILD_LICENSES/`, and a REUSE.toml. Permissive. Redistribution only requires keeping these notices and the Apache-2.0 NOTICE handling. No copyleft. AGPL PyMuPDF was correctly avoided. |
| `playwright` | 1.63.0 | Apache-2.0 | **dev-only** (`requirements-dev.txt`, `scripts/ui_browser_check.py`) | Bundles a `node.exe` driver (Node.js MIT plus third-party notices). It drives the installed Microsoft Edge; no Playwright browser was downloaded. Not part of a runtime distribution, so it raises no redistribution concern if dev dependencies are excluded. |
| `imageio-ffmpeg` (wrapper) | 0.6.0 | BSD-2-Clause | runtime | See §2.5. |

## 4. Recommended distribution decision

The options weighed:

| Option | Effort | Risk | Assessment |
|---|---|---|---|
| A. Ship as-is (bundled GPLv3 static build) and meet the obligations | **High.** Assemble and host corresponding source for FFmpeg 7.1 and every static library, add notices, record provenance. | Compliance failure if the source set is incomplete. Patent exposure from the H.264/HEVC encoders. | Feasible, but disproportionate for a module that only needs decoding of frames to PNG. |
| **B. Don't redistribute ffmpeg. The user installs it.** | **Low** | Lowest | **Recommended.** Distribute without the bundled binary: install imageio-ffmpeg with `pip install --no-binary imageio-ffmpeg imageio-ffmpeg`, or exclude `imageio_ffmpeg/binaries/` from the package. Document that users supply ffmpeg themselves (an LGPL build is preferred) and set `IMAGEIO_FFMPEG_EXE`. The module already degrades honestly: without ffmpeg it returns `409 PREVIEW_UNAVAILABLE` and requires an explicit `duration`. |
| C. Swap to an LGPL-only build and ship it | Medium | Lower than A. Still requires LGPL source and notice obligations, and still carries decoder patent questions. | Acceptable second choice. Use an LGPL shared build (for example BtbN `*-lgpl-shared`) with no `--enable-gpl` and no libx264/libx265. You must still ship the LGPL text and FFmpeg source, and follow the FFmpeg legal checklist. Verify with `-buildconf`, which must show no `--enable-gpl` and no `--enable-nonfree`. |

**Recommendation: B for any distribution outside this machine, with C as the fallback** if bundling is a product requirement.

For **internal, non-distributed use** (the current state), no action is required beyond accurate documentation.

**No binaries were replaced, removed or redistributed during this audit.**

## 5. Sources (retrieved 2026-10-12)

- FFmpeg licence and legal checklist: <https://ffmpeg.org/legal.html>
- gyan.dev builds (licence statement, source links, essentials library list): <https://www.gyan.dev/ffmpeg/builds/>
- imageio-ffmpeg README (`IMAGEIO_FFMPEG_EXE`, `--no-binary`): <https://github.com/imageio/imageio-ffmpeg>
- Local evidence:
  - `ffmpeg -version`, `-buildconf`, `-L` and `-encoders` output
  - `.venv` dist-info `LICENSE` and `METADATA` for imageio-ffmpeg, pypdfium2 and playwright
