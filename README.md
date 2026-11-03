# Broader Vids

Broader Vids provides an alternative recommendation algorithm for YouTube and Bilibili.
It combines your interests, exploration of unfamiliar topics, video quality, and feedback.

**The plugin learns from your dashboard ratings and calibrates its internal scoring model weights to your preferences.**
You can manually exclude creators and tags from future rounds.
Each complete round recommends ten videos per selected platform.

The plugin runs locally through Codex CLI or Codex in the ChatGPT desktop app.
Recommendations appear in chat. A local dashboard provides ratings, exclusions, and preferences.

## Install the plugin

Requirements: Windows, PowerShell, Node.js 20 or later with npm, UV, and Codex CLI authenticated with ChatGPT.
Installation and recommendations require internet access.
Choose one installation method. Neither method requires a checkout, build, or local folder selection.

### From GitHub in the ChatGPT desktop app

1. Open the plugin marketplace controls in Codex.
2. Select **Add marketplace** and its repository or Git URL option.
3. Enter `https://github.com/antidemosthenes/broader-vids`.
4. Add or sync the marketplace.
5. Select **Broader Vids** and install it.
6. Start a new conversation.

The GitHub repository supplies the catalog. Its plugin entry downloads the complete package from npm.

### From GitHub in Codex CLI

Run these commands in PowerShell:

```powershell
codex plugin marketplace add antidemosthenes/broader-vids --sparse .agents/plugins
codex plugin add broader-vids@broader-vids
```

Start a new conversation after installation.

### From npm

Run this command in PowerShell:

```powershell
npx --yes broader-vids@0.1.0 plugin install
```

This command registers an npm-backed marketplace and installs the plugin through Codex.
It installs the skills and dashboard, not only the terminal launcher.
It does not require the GitHub catalog.
Restart the desktop app or start a new Codex CLI conversation after installation.

The app's Add marketplace field accepts repository URLs, not npm package names.
Use the npm command above for that installation route.
See the [official packaging guide](https://developers.openai.com/plugins/build/plugins) for supported plugin sources.

## Set up and request recommendations

Use this procedure for either platform or both:

1. Start a new conversation. Invoke the setup skill from the table below.
2. Choose YouTube, Bilibili, or both.
3. Choose calibrated starter preferences, enter your interests, or import a preference file.
4. Complete the dependency and access checks. Setup gives repair steps for required failures.
5. Invoke the recommendation skill. Review the links and summaries in chat.
6. Open the dashboard to mark watched videos and rate their topic, subject, depth, and quality.
7. Request another round when you want more recommendations.

Existing profiles can keep their saved preferences during setup.
Saving ratings updates the local preference and quality models.
Their learned weights affect screening and final ranking in later rounds.
Creator and tag exclusions apply to the next new round.
The dashboard **Preferences** page lets you edit interests and import or export preferences and learned weights.
Before a new round, incomplete feedback prompts you to open the dashboard or continue.

Both platforms support manual creator and tag exclusions.
YouTube tag rules match available creator-supplied keyword tags, which differ from visible hashtags.
Bilibili rules match its video tags.
Search queries use English for YouTube and Simplified Chinese for Bilibili.

| Action | Skill command | Purpose |
| --- | --- | --- |
| Setup | `$broader-vids-setup` | Choose platforms and preferences, install required tools, and check access. |
| Recommendations | `$broader-vids` | Request a round for your saved platforms. |
| Dashboard | `$broader-vids-dashboard` | Rate videos, manage exclusions, and edit preferences. |
| Reset | `$broader-vids-reset` | Preview removal of preferences, weights, ratings, and histories while keeping tools and sign-in. |
| Clean | `$broader-vids-clean` | Preview removal of all managed local data, including tools and sign-in. |

Use the command names shown by your host's skill menu.
Some hosts add the plugin namespace, such as `$broader-vids:broader-vids-dashboard`.
You can also ask: “Use Broader Vids to recommend videos” or “Use Broader Vids to open my dashboard.”
Reset and Clean require confirmation. Neither action uninstalls the plugin.

## Bilibili access and exclusions

Bilibili requires manual sign-in for full-video evidence.
Setup opens a separate browser window.
Keep that window open after sign-in and return to chat to report completion.
The plugin verifies the session and closes the window.
You can switch accounts later from the dashboard.

Bilibili imports your account blacklist after verified sign-in and before new rounds.
Imported creator exclusions combine with your manual exclusions.
A failed sync preserves the last complete copy.
Use **Sync blacklist** in the dashboard to retry.

The default 视频翻译 tag exclusion can be removed in the dashboard.

See the [workflow](docs/WORKFLOW.md) for recommendation steps and the [scoring guide](docs/SCORING.md) for ranking and learning.
The [reference](docs/REFERENCE.md) covers terminal commands, troubleshooting, local builds, and maintenance.
The [architecture](docs/ARCHITECTURE.md) describes the launcher, engine, and local storage.

License: [MIT](LICENSE).
