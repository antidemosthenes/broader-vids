# Terminal and maintenance reference

Use the [README](../README.md) for the first installation.
This reference explains individual functions, tests, distribution, and dependencies.
Examples use PowerShell in the source folder unless stated otherwise.

## Read command examples

Start each source command with `uv run --no-sync broader-vids`.
This prefix runs the installed project engine through UV.
After a Node package installation, the equivalent prefix is `broader-vids`.
For an unpacked Node package, use `node C:\path\to\broader-vids\bin\broader-vids.cjs`.

Replace `VIDEO_URL`, `RUN_ID`, `BATCH_ID`, and file paths with your values.
A run ID identifies one saved job.
A batch ID identifies the combined YouTube and Bilibili job.
The command output prints these IDs.

The global `--data-dir` argument must precede the command.
For example:

```powershell
uv run --no-sync broader-vids --data-dir 'C:\Broader Vids Data' profile show
```

Use `--help` after a command for its accepted arguments:

```powershell
uv run --no-sync broader-vids --help
uv run --no-sync broader-vids recommend --help
uv run --no-sync broader-vids evidence prepare --help
```

## Choose a data directory

The default Windows directory is `%LOCALAPPDATA%\broader-vids`.
Other systems default to `~/.local/share/broader-vids` when `LOCALAPPDATA` is absent.
All commands for one profile must use the same directory.
Changing directories does not transfer existing data.

For the current PowerShell session, select a directory with:

```powershell
$env:BROADER_VIDS_DATA_DIR = 'C:\Broader Vids Data'
```

An explicit `--data-dir` overrides this environment setting.
Keep the data directory outside an installed plugin or npm package.
The Node launcher also stores its engine environment there.

## Prepare tools and sign-in

```powershell
uv sync --locked --extra evidence --extra asr
uv run --no-sync broader-vids doctor
codex login
uv run --no-sync broader-vids login bilibili
```

| Option or command | Meaning |
| --- | --- |
| `--extra evidence` | Install browser, download, and image tools |
| `--extra asr` | Install optional speech transcription tools |
| `doctor` | Report local tools without checking account authentication |
| `login bilibili` | Open a separate browser for manual sign-in |
| `login bilibili --confirm` | Request verification from the open sign-in window |
| `login bilibili --timeout 600` | Allow 600 seconds for manual sign-in |

### Required and optional setup items

`doctor` separates local requirements from optional tools and access checks.

| Group | Items | Effect when unavailable |
| --- | --- | --- |
| Required for setup and recommendations | Python 3.12 or later, Node.js 20 or later, UV, Codex CLI, FFmpeg, yt-dlp, and Pillow | The local check fails and lists the required items to install or repair |
| Conditional for Bilibili browser access | Playwright and Edge, Chrome, or Playwright Chromium | Browser sign-in and authenticated Bilibili access need these items |
| Conditional for full Bilibili video access | Verified Bilibili sign-in | Public discovery remains separate from full-video access |
| Optional | faster-whisper | Native captions still work, but transcription fallback is unavailable |
| Optional | ffprobe | Broader Vids duration and frame checks still work through FFmpeg |
| Manual checks | ChatGPT sign-in and internet access | AI assessment and remote access require separate verification |

The local check verifies the Node.js version and runs the selected FFmpeg executable.
FFmpeg can come from PATH or the bundled imageio-ffmpeg executable.
Installing imageio-ffmpeg does not install ffprobe.
Module checks report installed packages rather than a complete media-processing test.
The browser check detects common system browsers without starting them.
It does not inspect cached Playwright Chromium.

`local_requirements_met` describes required local items only.
An exit code of zero means those local checks passed.
An exit code of two means a required item is missing, unusable, or unsupported.
Missing optional items do not change that exit code.
`not_checked` means unverified rather than missing.
The command does not read credentials, open a browser, or test network access.
The setup skill reports required failures, conditional limitations, and optional omissions separately.

Complete sign-in in the window before you run `login bilibili --confirm`.
Keep its original terminal open during sign-in.
Use another terminal for the confirmation command.
Do not paste cookies or passwords into a command.
If a security challenge blocks access, stop that operation.

If Edge and Chrome are absent, install the Playwright browser:

```powershell
uv run --no-sync playwright install chromium
```

## Set interests

```powershell
uv run --no-sync broader-vids profile show
uv run --no-sync broader-vids profile set --interest mathematics --interest travel
```

`profile set` replaces the explicit interest list.
Repeat `--interest` for every subject you want to retain.
It does not infer interests from your watch history.

## Export or import learned preferences

In the dashboard, select **Import/Export**.
Select **Export preferences** to download a JSON file.
For import, select **Choose file**.
Broader Vids checks the file and shows its summary automatically.
Select **Import preferences** to apply it.
If preferences changed after the preview, check the refreshed summary before you select **Import preferences** again.

The terminal provides the same operations:

```powershell
uv run --no-sync broader-vids profile export 'C:\Broader Vids Data\preferences.json'
uv run --no-sync broader-vids profile import 'C:\Broader Vids Data\preferences.json'
uv run --no-sync broader-vids profile import 'C:\Broader Vids Data\preferences.json' --apply
```

The import command previews the file unless you include `--apply`.
Export refuses to overwrite an existing file unless you include `--overwrite`.
Files must use the `.json` extension and fit within 8 MiB.
Import checks the scoring version, checksum, and parameter uncertainty.
It rejects incompatible or invalid files before changing preferences.

Transfers contain interests, learned parameters, and their uncertainty.
They exclude individual ratings, video records, calibration examples, and recommendation history.
They also exclude exclusions, account data, cookies, media, and dashboard settings.
Local lists, ratings, watch states, and exclusions remain unchanged.
Existing local ratings stay visible but do not enter the imported model again.
New or edited ratings continue learning from the imported parameters.

Import saves the current parameters in `backups/preferences-<identifier>.json` under the data directory.
Use that file with the same import operation to restore the earlier parameters.
This recovery file contains no video history.
Import does not reconstruct or replace a dashboard list.

## Request recommendations

```powershell
uv run --no-sync broader-vids recommend both --use-browser --asr-model base
```

Use `youtube` or `bilibili` instead of `both` for one platform.
A new command requests a new round.
It retains existing feedback and pending recommendations.

| Argument | Meaning | Default |
| --- | --- | --- |
| `--use-browser` | Use the separate Bilibili login for metadata and evidence | Disabled |
| `--max-seconds` | Time allowance for the recommendation invocation | 1800 seconds |
| `--workers` | Maximum shared model-worker count | 4 |
| `--model` | Explicit host bridge for the invoking chat's model | Inherit current chat |
| `--effort` | Explicit host bridge for its reasoning effort | Inherit current chat |
| `--max-requests` | Metadata request allowance per platform | 160 |
| `--max-assessments` | Total model-call allowance per platform, including planning | 52 |
| `--max-mib` | New media allowance per platform, in mebibytes | 1024 |
| `--asr-model` | Local transcription model, `tiny`, `base`, or `small` | No transcription fallback |
| `--discovery-run` | Reuse a saved discovery buffer for a single platform | Collect fresh candidates |

The combined batch shares one thirty-minute allowance and its worker slots.
Its platform jobs retain separate request, media, and model allowances.
Discovery requests use a separate bounded counter within the shared time allowance.
Planning and assessment workers inherit the invoking Codex chat's model and reasoning effort through the authenticated Codex CLI.
The engine resolves only the active thread's `turn_context` model and effort, then saves the pair for the run.
Resuming retains that pair.
A new invocation reads the current selection again.
If local turn metadata is unavailable, pass both `--model` and `--effort` from the host selection.
The engine does not silently substitute global CLI defaults or a preset model.
Accepted effort names are `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`.
The selected model and CLI must support the requested effort.
Failed calls consume their allowance.
The engine does not switch to paid API access automatically.

## Continue an incomplete run

```powershell
uv run --no-sync broader-vids status
uv run --no-sync broader-vids continue BATCH_ID
uv run --no-sync broader-vids report RUN_ID
```

Use the batch ID for a combined recommendation run.
Its platform child jobs cannot continue independently.
Continuation preserves the plan, elapsed time, and remaining resource allowances.
It does not create a fresh allowance.
Resolve the reported access problem before another attempt.

`report` writes local JSON and Markdown files.
The `publish` command also writes local results.
It does not upload a website or package.

```powershell
uv run --no-sync broader-vids publish RUN_ID --allow-incomplete
```

Use `--allow-incomplete` only when you want a partial list from the assessed subset.
The report identifies that result as partial.

## Review and rate videos

```powershell
uv run --no-sync broader-vids serve
```

Open `http://127.0.0.1:8765`.
`serve --port 9000` selects another port.
The server accepts local connections only.
Press Ctrl+C in its terminal to stop it.
Closing the browser tab does not stop the server.

For a background server, identify the listener:

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen
```

Check the process command line before you stop that process.
Stop its launcher too if the launcher remains active.
Server shutdown preserves saved results and feedback.

Use terminal feedback when you prefer commands:

```powershell
uv run --no-sync broader-vids video watched VIDEO_URL
uv run --no-sync broader-vids video dismiss VIDEO_URL
uv run --no-sync broader-vids rate VIDEO_URL topic_interest low --target topology
uv run --no-sync broader-vids rate VIDEO_URL domain_interest high --target mathematics
uv run --no-sync broader-vids rate VIDEO_URL depth deeper --target topology --level introductory
uv run --no-sync broader-vids rate VIDEO_URL quality_level medium
```

| Field | Meaning |
| --- | --- |
| `topic_interest` | Interest in one specific topic |
| `domain_interest` | Interest in one broad subject |
| `quality_level` | Quality of this video |
| `depth` | Preferred treatment depth for the named topic |
| `--target` | Topic or subject that the judgment concerns |
| `--level` | Observed video level, not your preferred level |

Topic, subject, and quality accept `high`, `medium`, or `low`.
Depth accepts `deeper`, `suitable`, or `lighter`.
Use `deeper` when the video is too elementary.
Use `lighter` when the video is too advanced.
Audience levels are `unknown`, `introductory`, `intermediate`, and `advanced`.
Unknown levels do not transfer a depth adjustment to later videos.

Clear one dimension with:

```powershell
uv run --no-sync broader-vids rate VIDEO_URL topic_interest clear
```

`video unwatched` reverses watched status.
`video restore` reverses dismissal.
These actions do not clear ratings.
Use `queue` to inspect saved state:

```powershell
uv run --no-sync broader-vids queue --platform bilibili
```

The current interface omits binary vote commands.
Use the separate rating dimensions instead.
Each row has one Save control for its rating.
The Tag exclusion rules panel shows separately saved exclusions.

## Creator and tag exclusions

List all local exclusion rules:

```powershell
uv run --no-sync broader-vids exclusions list
```

Exclude the creator of a known video:

```powershell
uv run --no-sync broader-vids exclusions video add VIDEO_URL
uv run --no-sync broader-vids exclusions video remove VIDEO_URL
```

These commands use saved creator metadata.
Refresh the video's metadata first if its creator ID is unknown.
You can also use stable creator IDs directly:

```powershell
uv run --no-sync broader-vids exclusions creator add youtube CHANNEL_ID
uv run --no-sync broader-vids exclusions creator remove youtube CHANNEL_ID
uv run --no-sync broader-vids exclusions creator add bilibili ACCOUNT_ID
uv run --no-sync broader-vids exclusions tag add bilibili TAG
uv run --no-sync broader-vids exclusions tag remove bilibili TAG
uv run --no-sync broader-vids exclusions sync-bilibili
```

Replace CHANNEL_ID with the complete YouTube channel ID that starts with UC.
Replace ACCOUNT_ID with the creator's positive numeric Bilibili ID.
Replace TAG with an exact tag name.
The built-in 视频翻译 rule cannot be removed.
Imported creator rules cannot be overridden locally.
A manual removal leaves any imported ban in effect.
The manual list is separate for each platform.

Sync uses the isolated browser session without changing Bilibili account settings.
It commits only a complete import and keeps manual rules.
Each attempt permits sixty seconds and one hundred requests.
Automatic attempts share the recommendation run's existing allowance.
Failures preserve the last complete copy and report its age.
Creator rules use their own revision and do not retrain the preference model.
The dashboard checkbox saves independently of ratings and affects the next new round.
Each platform also has an Add tag form for exact metadata tags.
YouTube rules match creator-supplied keyword tags when the video metadata includes them.
These tags differ from visible #hashtags.
Repeated tag entries with different letter case do not create duplicate rules.
The Exclusions view shows rule sources and sync status.

The local dashboard remains available through serve in this preview.
Phase 5 will expose its opening action through the host's supported skill command.
Recommendations will appear directly in chat.
The dashboard order is Recommendations, Exclusions, then optional Calibration.

### Use the dashboard

1. Select Recommendations in the navigation.
2. Select YouTube or Bilibili above the list.
3. Select Rate beside a video.
4. Check the topic labels and video level.
5. Select a rating in each applicable group.
6. Select Save within that group.

Clear removes that group's saved judgment.
Depth for this video keeps the existing topic-scoped learning behavior.
Video level records your judgment of the observed difficulty.
Drafts survive video, platform, and section changes within the open tab.
Save drafts before you close or reload the page.
Failed writes retain the entered values and show a local error.

Mark watched and Dismiss are separate actions beside each video.
Restore reverses dismissal.
Earlier unwatched opens pending videos from earlier lists when available.
Current picks returns to the latest recommendation list.
Score details opens technical information without expanding list rows.
Help explains the feedback definitions.

Select Exclusions to manage Creators or Tags for the selected platform.
The Bilibili connection panel separates sync status, account identity, and the last successful sync.
Times use your browser's local time zone.
The timestamp tooltip includes seconds and the time zone.
Sync blacklist updates only that operation's busy state.
Other navigation remains available.

Calibration remains optional and starts empty for a new profile.
The current dashboard does not select examples automatically.


## Calibration and evidence maintenance

```powershell
uv run --no-sync broader-vids calibrate start
uv run --no-sync broader-vids calibrate list
uv run --no-sync broader-vids learn status
uv run --no-sync broader-vids learn fit
uv run --no-sync broader-vids learn evaluate
```

`calibrate start` selects thirty eligible, previously unrated videos from existing verified evidence.
It does not discover or assess replacement videos automatically.
`calibrate start --new` requests another cohort after the current reservation ends.
`learn fit` rebuilds the current model.
`learn evaluate` calculates or retrieves the applicable held-out result.
See [calibration and mathematics](SCORING.md).

Refresh one video's metadata without media downloads or model calls:

```powershell
uv run --no-sync broader-vids metadata VIDEO_URL --use-browser
```

Repair rated evidence that lacks current source information:

```powershell
uv run --no-sync broader-vids learn repair-evidence --max-seconds 180 --max-videos 1
```

This operation can refresh metadata and call the model with retained captions and frames.
It preserves ratings and original scorecards.
It starts no new media download.
Its limits are one to three videos and at most 600 seconds.

Label older videos from retained captions:

```powershell
uv run --no-sync broader-vids learn label-videos --max-videos 60 --max-seconds 900
```

This operation generates content topics and audience levels.
It downloads no new videos.
Existing cached labels avoid repeated model calls.
New quality assessments include these descriptors in their normal response.

Migrate an older profile after content labeling:

```powershell
uv run --no-sync broader-vids learn migrate-feedback
```

Migration maps binary interest and quality to High or Low in their current dimensions.
It leaves ambiguous overall ratings empty.
It creates a recovery backup before removing active binary data.
The engine retrains from the mapped ratings.
Mapped fields remain marked for user review until the user saves them.
Repeating migration does not duplicate ratings.

## Inspect discovery and evidence

```powershell
uv run --no-sync broader-vids topics
uv run --no-sync broader-vids discover youtube --seed 42
uv run --no-sync broader-vids discover bilibili --curated
uv run --no-sync broader-vids queries --platform youtube --export my-queries.json
uv run --no-sync broader-vids discover youtube --bank my-queries.json
```

| Argument | Meaning |
| --- | --- |
| `--seed` | Repeatable random seed for topic selection |
| `--curated` | Use the curated query bank instead of hierarchical planning |
| `--bank` | Use the specified query-bank JSON file |
| `--round` | Select a curated-bank round number |
| `--max-planning-calls` | Limit model calls for query planning, default 2 |
| `--creators` | Limit creator expansion sources, default 4 |
| `--creator-pages` | Limit pages per creator expansion, default 2 |
| `--model` | Use the model selected by the invoking host |
| `--effort` | Use the reasoning effort selected by the invoking host |

Standalone discovery defaults to 100 requests and 600 seconds.
Its shortlist is a candidate preview, not a quality-ranked recommendation list.
If local chat metadata is unavailable, supply both `--model` and `--effort` from the host's selected settings.
These arguments also apply to `evidence prepare`.
Preparation saves the settings for later assessment and continuation.
Continuation uses saved settings rather than accepting replacements.

Prepare or assess one playable video:

```powershell
uv run --no-sync broader-vids evidence prepare VIDEO_URL --use-browser --asr-model base
uv run --no-sync broader-vids evidence assess RUN_ID --mode augmented
uv run --no-sync broader-vids evidence captions RUN_ID
uv run --no-sync broader-vids evidence frame RUN_ID --at 120 --reason 'Check the diagram'
```

| Argument | Meaning |
| --- | --- |
| `--mode baseline` | Assess captions without frames |
| `--mode augmented` | Assess captions and selected frames |
| `--mode both` | Run both assessments, the default for the standalone command |
| `--media` | Use an explicit local video file |
| `--captions` | Use explicit JSON3, JSON, VTT, or SRT captions |
| `--at` | Timestamp in seconds for an additional frame |
| `--long-edge` | Maximum output frame dimension, default 768 pixels |

Use `--use-browser` for Bilibili access.
Include the requested Bilibili part as `?p=1` or its actual part number.
Standalone preparation defaults to 128 MiB and 1200 seconds.
An evidence job allows two model calls and eight submitted images.
Add a frame before the augmented assessment, within that limit.
`evidence pilot-report` compares saved caption-only and frame-assisted assessments.

## Clean temporary evidence

```powershell
uv run --no-sync broader-vids cleanup
uv run --no-sync broader-vids cleanup --apply
```

The first command previews disposable files.
The second command applies the cleanup policy.
The policy preserves user originals, active jobs, and user history.
Completed assessments normally remove downloaded full videos automatically.

## Run automated checks

```powershell
uv sync --locked --extra evidence --extra asr
npm ci --ignore-scripts
uv run --no-sync ruff check src tests scripts
uv run --no-sync ruff format --check src tests scripts
uv run --no-sync python -m unittest discover -s tests
npm test
npm run check:web
```

Tests use temporary data and controlled responses.
Optional media tests need their dependencies.
Unit tests do not require platform sign-in or subscription model calls.
Run fixtures supply explicit model settings instead of reading your current Codex chat.
Model-inheritance tests create temporary session files with controlled settings.
Dashboard browser tests require Playwright and a supported local browser.
The current Windows tests use installed Edge.
Linux tests use Playwright Chromium. Install its browser and system dependencies before running tests:

```bash
uv run --no-sync playwright install --with-deps chromium
```

The Ubuntu GitHub Actions workflow performs this installation before the test step.
Dashboard tests check drafts, failed writes, duplicate feedback, dialogs, platform navigation, and responsive themes.
Keep screenshots and other visual checks under the ignored `.cache/` directory.
`gate` reports development readiness from saved local records.
It does not replace a clean installed-plugin test.

## Build and distribute

### Install the local Codex plugin

Build the wheel and npm archive from the source folder.
Then build the clean marketplace payload:

```powershell
uv run --no-sync scripts/build_plugin.py
codex plugin marketplace add .
codex plugin add broader-vids@broader-vids-local --json
codex plugin list --json
```

The repository catalog uses `.agents/plugins/marketplace.json`.
Its source is the generated `dist/plugins/broader-vids` directory.
The build script includes only files from the verified npm archive.
It also creates a ZIP under `dist/`.
The installed cache never needs the private working checkout.
These operations install locally and do not publish anything.

Open a new desktop or CLI conversation after installation.
For installation through the ChatGPT desktop app, follow [the README](../README.md#option-2-install-in-the-chatgpt-desktop-app).
Use the installed Broader Vids setup skill for guided setup.
The host exposes the recommendation, setup, and dashboard skills through its skill menu.
Their skill names are `broader-vids`, `broader-vids-setup`, and `broader-vids-dashboard`.
Use the invocation syntax that the host shows.
The manifest declares the onboarding skill through `extensions.com.openai.onboardingSkill`.
Allow the host to access the selected external profile, required network services, and existing Codex session when prompted.
If permission is unavailable, setup reports the incomplete step and preserves completed work.
See the [official local plugin guide](https://developers.openai.com/plugins/build/plugins).

### Select an existing profile

From the plugin directory, inspect or save the data selection:

```powershell
node bin/broader-vids.cjs data show
node bin/broader-vids.cjs data use "C:\path\to\my profile"
node bin/broader-vids.cjs data reset
```

`data use` records a path without moving or copying profile data.
`data reset` removes only the saved selection.
It preserves every profile.
The selection lives in `%LOCALAPPDATA%\broader-vids\launcher.json` on Windows.
An explicit `--data-dir` overrides `BROADER_VIDS_DATA_DIR`, the saved selection, and the system default, in that order.
Source commands and installed commands honor the same selection.
Use an explicit temporary directory for installation tests.

### Open or stop the dashboard

```powershell
node bin/broader-vids.cjs dashboard open
node bin/broader-vids.cjs dashboard open --no-browser
node bin/broader-vids.cjs dashboard status
node bin/broader-vids.cjs dashboard stop
```

Open starts or reuses the verified server for the selected profile.
`--no-browser` returns its URL without opening the system browser.
The dashboard skill presents that URL in chat.
A busy preferred port causes the server to select a free port.
Stop authenticates the registered server before requesting shutdown.
It does not stop a process based only on a PID or port.
Private control records and bounded logs reside in the profile's `.dashboard/` directory.
The foreground `serve` command remains available for terminal use.

### Build package artifacts

Build local artifacts:

```powershell
uv build --wheel --out-dir python
npm pack --ignore-scripts
uv run --no-sync scripts/verify_package.py
```

UV builds the Python wheel.
npm creates the local package archive.
The package check compares source files, versions, links, and private-file exclusions.
These commands do not upload artifacts.

From an unpacked package, install its external engine:

```powershell
node bin/broader-vids.cjs setup --asr
node bin/broader-vids.cjs doctor
```

`setup` installs the dependency-free core.
`setup --evidence` also installs browser and media tools.
`setup --asr` includes those tools and transcription dependencies.
Setup requires Node.js 20 or later and UV.
It does not use an npm postinstall script.

Daily scheduling also remains unimplemented.

Public release has three separate destinations:

| Destination | Artifact | Release action |
| --- | --- | --- |
| GitHub | Reviewed source, skills, manifests, documentation, and synthetic examples | Push the approved commit or release |
| npm | Tested launcher, wheel, plugin files, and dependency locks | Publish the approved package version |
| OpenAI Plugins Directory | Desktop-only skill package ZIP and listing material | Submit, resolve review findings, and publish after approval |

Approve the exact destination and files before any public upload.
Use synthetic state for examples and screenshots.
Exclude real preferences, ratings, history, sessions, media, captions, frames, logs, and backups.
Git ignores the internal plan, local environments, state, and build archives.
The npm file list includes only the selected public files.

A public listing does not host the dashboard or upload user recommendations.
OpenAI acceptance and account eligibility require external review.
See [desktop-only submission](https://developers.openai.com/plugins/deploy/desktop-only) and [publication steps](https://developers.openai.com/plugins/deploy/submission).
No npm release or public-directory listing exists for this preview.

## Tools, dependencies, and copyright

Broader Vids uses the [MIT license](../LICENSE).
The project notice names copyright holder **antidemosthenes**, year **2026**.
Keep that notice with redistributed Broader Vids code.

Third-party authors retain their own copyright.
The following table identifies the main tools and their upstream license information.
It is not a complete list of optional transitive packages.

| Tool | Use | Upstream license or terms |
| --- | --- | --- |
| [Python](https://docs.python.org/3/license.html) | Engine runtime | PSF license and included notices |
| [UV](https://docs.astral.sh/uv/reference/policies/license/) | Python environments and packages | MIT or Apache-2.0 |
| [Node.js](https://github.com/nodejs/node/blob/main/LICENSE) | Launcher runtime | MIT and included third-party notices |
| [Codex CLI](https://developers.openai.com/codex/cli/) | Authenticated model execution | See the CLI distribution and OpenAI service terms |
| [SQLite](https://sqlite.org/copyright.html) | Local database through Python | Public domain dedication |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp#license) | Playable metadata, captions, and media | Unlicense for core source, with separate dependency notices |
| [Playwright](https://github.com/microsoft/playwright-python/blob/main/LICENSE) | Separate browser profile | Apache-2.0 |
| [Pillow](https://pillow.readthedocs.io/en/stable/about.html#license) | Frame image processing | MIT-CMU |
| [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg/blob/main/LICENSE) | FFmpeg executable access | BSD-2-Clause for the wrapper |
| [FFmpeg](https://ffmpeg.org/legal.html) | Media decoding and frame extraction | LGPL or GPL, depending on the binary build |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE) | Optional speech transcription | MIT |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2/blob/master/LICENSE) | Transcription inference | MIT |
| [PyAV](https://github.com/PyAV-Org/PyAV/blob/main/LICENSE.txt) | Transcription media access | BSD-3-Clause, with separate FFmpeg terms |
| [Hugging Face Hub](https://github.com/huggingface/huggingface_hub/blob/main/LICENSE) | Optional model download | Apache-2.0 |
| [Hatchling](https://github.com/pypa/hatch/blob/master/LICENSE.txt) | Python wheel build | MIT |
| [Ruff](https://github.com/astral-sh/ruff/blob/main/LICENSE) | Python formatting and checks | MIT |
| [PyYAML](https://github.com/yaml/pyyaml/blob/main/LICENSE) | Development skill checks | MIT |
| [Prettier](https://github.com/prettier/prettier/blob/main/LICENSE) | Browser asset formatting | MIT |

The Node archive contains the Broader Vids wheel and dependency lock files.
It does not bundle the optional Python distributions, browser binaries, or model weights.
Explicit setup downloads selected dependencies separately.
FFmpeg binaries and model weights can have terms distinct from their Python wrappers.
Retain the notices supplied with the exact versions that you redistribute.

`uv.lock` and `package-lock.json` record resolved dependency versions.
The two requirements files under `python/` also record package hashes.
Check the installed distributions' license files when preparing a release.
The Broader Vids license does not grant rights to platform videos or third-party media.

### Captionless evidence and media transfer policy

New evidence jobs can use visual-only assessment when no transcript is available.
The caller subtracts ten quality points separately from the model score and from the learned quality component.
Caption-only mode requires a transcript.
Visual-only jobs use augmented mode.
Downloadable captions differ from text burned into the picture.
Bilibili downloads retain platform-offered CDN alternatives and prefer the Akamai route observed to perform better on this installation.
Transfers use 2 MiB chunks and 64 KiB buffers of constant size.
They allow up to three bounded attempts, resume support and a slow-route cutoff.
Retries share the existing time and byte allowance.
The engine does not retry access challenges.

### Failure recovery and progress

Candidate failures do not cancel unrelated inspections.
An invalid scorecard can receive one repair attempt within the saved allowances.
The repair prompt includes the validation failure.
Exhausted per-video attempts produce an unavailable result instead of an endless retry.
Access challenges pause the affected platform.
Model authentication failures or provider limits stop shared model work.
The report preserves the first failure and separate later issues.

Preparation time starts after a preparation slot becomes available.
Its stage allowance is at most 300 seconds.
Model assessment time starts after a model slot becomes available.
Its call allowance is at most 180 seconds.
The parent deadline includes queue waits and all work.
Continuation preserves that deadline and all cumulative allowances.

Metadata retrieval uses at most two concurrent requests per platform.
Their starts remain at least one second apart.
They share one request counter and retain the existing access checks.
Video downloads omit audio unless transcription requires it.
Exact selected-stream sizes support a check before transfer.
Transfer checkpoints record observed bytes and bound unreported bytes after interruption.
Reports identify conservative byte charges as upper bounds.

Terminal progress appears on standard error during a run.
The final JSON result remains on standard output.
Reports include phase times, queue waits, assessment times, caption counts, and cache hits.
Assessment times include worker startup and access checks.
They do not measure isolated model inference time.
Paused rounds can show a provisional preview in the digest.
That preview does not publish recommendations or change pending membership.

Scorecard schemas and validators use the same topic-label length bound of 120 characters.
See the [OpenAI Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs) for supported schema constraints.
