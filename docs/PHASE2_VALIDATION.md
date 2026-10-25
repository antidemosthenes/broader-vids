# Phase 2 verification — October 25, 2026

**Phase 2 is complete in the reference workspace on `probe-phase-2`. Phase 3 has not started.** The gate verifies three network-resolved playable videos per platform, intact evidence hashes, paired valid scorecards and the image/call limits. This is an evidence-access and small evaluation milestone, not a calibrated recommendation system.

## Six-video trial

Each video received a separate caption-only baseline and an augmented assessment with four timestamped frames. Inputs used author/platform captions or explicitly labeled local ASR. Quality estimates include the separate duration prior and remain provisional.

| Platform and video | Seconds | Assessed text source | Caption quality | Augmented quality | Observed extra input tokens |
| --- | ---: | --- | ---: | ---: | ---: |
| YouTube: Norms and Banach Spaces | 476.94 | Author captions | 73.66 | 76.66 | 2,469 |
| YouTube: Relining Van Dyck's Charles I | 876.69 | Author captions | 86.22 | 85.22 | 1,918 |
| YouTube: Quantum mechanics and bird navigation | 380.90 | Author captions | 83.53 | 83.53 | 1,355 |
| Bilibili: Banach-space examples | 842.78 | Local base-model ASR | 75.17 | 83.17 | 1,355 |
| Bilibili: Photo-restoration software tutorial | 214.76 | Local base-model ASR | 70.30 | 70.30 | 2,378 |
| Bilibili: FFT frequency-resolution experiment | 363.73 | Native automatic `ai-zh` captions | 85.51 | 88.51 | 1,910 |

Run IDs, in the same order:

- `7f387e23a3be4bd788318ffe7deb3999`
- `1fcf1ab0977b46bc98eb3da8899eee27`
- `beb33259dc4f44b9a88212f205984863`
- `fd9a91030fc0486c91721f177661c380`
- `f246cf35ff1547728b6e9154ea7f6ce5`
- `d940181d9488447f9c491df413b87d25`

Private local artifacts are under `.localdata/evidence/RUN_ID/`: media, timestamped captions/excerpts, frames, model prompts/responses and JSON/Markdown reports. `.localdata/evidence/pilot-report.md` and `pilot-report.json` compare the runs. They are ignored by Git and excluded from distributable packages. Cached assessments are reused only after evidence-hash verification.

## What frames added

The baseline correctly left visual presentation unknown. Augmented assessments could describe mathematical diagrams, the FFT window comparison, restoration process imagery and software demonstrations. Spot checks of a YouTube mathematics frame and two Bilibili frames supported the corresponding observations. The photo tutorial's small controls and black margins were visible limitations, rather than a reason to assume poor substance.

The two independent model calls sometimes produced different numerical estimates, including a one-point decrease for the restoration example. Those differences include model variability and cannot be attributed solely to frames. The stronger result is that visual criteria became assessable with explicit frame references. No audio was submitted for voice classification; narration identity remained unknown in every scorecard. Sparse evidence did not certify authorship, asset provenance, factual accuracy or whole-video pacing.

Observed input usage was 105,004 tokens for the six baselines and 116,389 for the six augmented calls: an additional 11,385 total, approximately 1,898 per video and 10.8% over the baseline input total in this setup. These are full Codex turn counters, including CLI/system overhead, rather than isolated image-token prices. The complete paired trial used 12 model calls, 24 submitted images, 221,393 reported input tokens and 11,803 output tokens. Development-chat work and three additional audit image views are outside these CLI counters.

Recorded media transfers total 109,004,489 bytes, including an unsuccessful partial download; the six accepted videos account for 98,773,617 bytes. Direct caption payload downloads total 60,868 bytes. Extractor metadata, inline-caption requests and the reusable Whisper model cache are not included in those transfer counters. The ASR counter records about 42.8 seconds, including the FFT transcription performed before the native-caption correction; the final FFT scorecards used native captions. No API-key model billing was used.

Do not expand this paired procedure to every candidate automatically. A production policy should use the trial to decide when visual uncertainty warrants frames, avoid duplicate baseline calls outside evaluation, and retain the explicit image/resource limits. Six selected examples do not establish satisfaction, broad quality calibration or reliable AI-voice detection.

## Access, captions and recovery

The first dedicated Bilibili login-page attempt encountered a security check. After explicit user approval, the revised flow opened a normal Bilibili page in an isolated persistent Playwright profile and waited for user confirmation. The user signed in, and Bilibili's session endpoint verified authentication. No CAPTCHA solving, password collection or regular-browser profile extraction was implemented. Session cookies remain in the isolated browser profile and are passed only in memory to platform requests.

[bili-watch's login flow](https://github.com/Carlown/bili-watch/blob/master/app.py#L525-L570) informed the normal-entry/explicit-confirmation interaction. Our check additionally requires the platform to confirm the session; presence of a cookie name alone is insufficient. Its unrelated monitoring/reporting functions were not used.

The user's [bilibiliCCHelper reference](https://github.com/indefined/UserScripts/blob/master/bilibiliCCHelper/bilibiliCCHelper.user.js#L1148-L1185) showed that caption configuration and timestamped JSON can be fetched through the player's interfaces. Inspection of the [yt-dlp extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py) exposed adapter omissions: subtitle extraction had not been explicitly enabled, inline SRT was ignored, and `ai-zh` did not match the initial language filter. All three are corrected and tested. The FFT example then returned 107 native cues without rendering its player. Playwright still loads the isolated authenticated profile; Tampermonkey is not required.

Earlier claims that the first two Bilibili examples lacked captions were premature; their saved assessments retain the local-ASR provenance actually used. Native refresh preserves assessed inputs and can save supplementary captions. For an unassessed job it can replace ASR input while reusing media and frames. [Kedou](https://www.kedou.life/caption/subtitle/bilibili) advertises SRT extraction, but its backend/coverage were not verified and no account session was sent to it.

Two attempted Bilibili cases remain pending: `df55a853932142c6bbee6dd0fb17f842` could not resolve content; `e20eccd275cd44ed8c05775d2b08373e` stopped after a partial download. They were not counted as successes. The shorter FFT example replaced them. A Windows executable lock during dependency installation and a faster-whisper/PyAV decoder mismatch were corrected; ASR now uses a timeout-bounded worker with FFmpeg-decoded PCM.

## Additional subtitle source review

The user supplied [Bili SubBatch](https://greasyfork.org/en/scripts/589638-bili-subbatch-loop-bilibili/code). Version 6.9.17 declares MIT licensing. Its published source was downloaded and read as text, without installing or executing the userscript. The decoded source SHA-256 is `26b6755cfdb74a7d745d9e646959b25ca830ca1f8e64ac9b5fb9759512341a5a`.

| Behavior | SubBatch source | Current Deepvids / installed yt-dlp 2026.10.21 |
| --- | --- | --- |
| Subtitle-track discovery | WBI player, ordinary player v2, then dm view | WBI player route |
| Listed AI track without a URL | AI-subtitle status lookup for a URL | Extractor discards the track |
| Browser runtime tracks | Reads player state, checking CID when available | No runtime-state fallback |
| Caption caching | Video/CID/language keys, memory/session/persistent layers | Hashed evidence bundles and explicit caption refresh |
| Part selection | Resolves page to CID, but clamps an out-of-range page | Retain strict requested-part checks; do not adopt clamping |

The most useful additions are bounded alternate track discovery and recovery of missing AI-track URLs before paying local transcription cost. These are research findings, not newly implemented or live-verified routes. The dm-view request in this script expects JSON; its current response format and availability must be tested before adopting it. An empty successful list must remain distinct from failed access. The script's generic HTTP fallback is a browser networking adaptation, not evidence that login or security checks can be bypassed.

Most extraction logic uses platform HTTP requests and does not inherently require a rendered player. Browser-state recovery would require an actual page; our existing Playwright session remains sufficient for authenticated HTTP extraction. SubBatch's broader AI/workspace UI and remote-service configuration are outside this project change. No session or video content was sent to another service. The follow-up is recorded in PLAN.md without changing the completed pilot, scorecards or Phase 3 status.

## Verification

All 56 Python tests, three Node launcher tests and the skill validator passed. Checks cover subtitle formats, actual Bilibili subtitle-request activation, inline AI captions, time/byte/image limits, exact 90-minute boundaries, source/part URLs, credential redaction/cookie scope, local preparation, frame recovery, hash tampering, cached calls, schema/reference validation and unknown audio/visual properties. Optional tests require their extras; skips are not live-pilot evidence.

The alpha.4 packed artifact passed core-only installation, explicit hash-checked evidence/ASR installation, dependency discovery, fresh-runtime gating and a synthetic local-caption/frame workflow from an unrelated path with spaces. Execution left package file hashes unchanged. Optional setup needs registry access when its hash-verifiable wheels are not cached; an offline first-install attempt failed cleanly and the explicit registry installation then passed. Browser profiles, videos, model weights and private artifacts are not bundled. No package was published. Phase 3, profiles, Bayesian feedback and scheduling are not implemented.
