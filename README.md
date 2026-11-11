# Broader Vids

Broader Vids provides an alternative recommendation algorithm for YouTube and Bilibili. It combines your interests, exploration of unfamiliar topics, and video quality.

**Your dashboard ratings calibrate the plugin’s internal scoring models to your preferences.**
You can exclude creators and tags from future rounds. Each complete round recommends ten videos per selected platform.

The plugin runs locally through Codex CLI or Codex in the ChatGPT desktop app. Recommendations appear in chat. A local dashboard manages ratings, exclusions, and preferences. Claude Code packaging is experimental. Static package checks and launcher fixtures cover it. No Claude host acceptance test has been performed.

## Requirements

Use Windows, PowerShell, Node.js 20 or later with npm, UV, and Codex CLI signed in with ChatGPT. Installation and recommendations require internet access.

## Install the plugin

Choose one of these three remote installation routes. You do not need a repository checkout or a local build.

### ChatGPT desktop app

1. Open the plugin marketplace controls in Codex.
2. Select **Add marketplace** and choose its repository or Git URL option.
3. Enter `https://github.com/antidemosthenes/broader-vids`.
4. Add or sync the marketplace. Then install **Broader Vids**.
5. Start a new conversation.

The GitHub repository provides the catalog. The catalog downloads the plugin package from npm.

### Codex CLI from GitHub

Run these commands in PowerShell:

```powershell
codex plugin marketplace add antidemosthenes/broader-vids --sparse .agents/plugins
codex plugin add broader-vids@broader-vids
```

Start a new conversation after installation.

### npm

Run this command in PowerShell:

```powershell
npx --yes broader-vids@latest plugin install
```

This command registers an npm backed marketplace and installs the plugin skills and dashboard. Start a new conversation after installation.

The desktop app accepts repository URLs in **Add marketplace**. Use the npm command for npm installation. See the [plugin packaging guide](https://developers.openai.com/plugins/build/plugins) for supported sources.

### Experimental Claude Code path

Claude Code support is experimental. Static package checks and launcher fixtures cover it. No host acceptance test has been performed. See the [Claude Code installation notes](docs/REFERENCE.md#experimental-claude-code-installation).

## Update the plugin

Follow [Update an installed plugin](docs/REFERENCE.md#update-an-installed-plugin) for update steps.

## Set up and use Broader Vids

Start a new conversation and invoke the setup skill. Choose YouTube, Bilibili, or both. Then choose starter preferences, enter interests, or import a preference file. Setup checks dependencies and access. It gives repair steps when a required check fails.

Invoke the recommendation skill to request a round. Review recommendations in chat. Open the dashboard to mark videos as watched and rate topic, subject, depth, and quality. Ratings update local preference and quality models. Their learned weights affect later rounds. Creator and tag exclusions apply to the next new round.

| Action | Skill command | Purpose |
| --- | --- | --- |
| Setup | `$broader-vids-setup` | Choose platforms and preferences. Install tools and check access. |
| Recommendations | `$broader-vids` | Request videos for your saved platforms. |
| Dashboard | `$broader-vids-dashboard` | Rate videos. Manage exclusions and preferences. |
| Reset | `$broader-vids-reset` | Preview removal of preferences, weights, ratings, and histories. Keep tools and sign-in. |
| Clean | `$broader-vids-clean` | Preview removal of all managed data, including tools and sign-in. |

Use the skill commands shown in your host's skill menu. Some hosts add the plugin namespace, such as `$broader-vids:broader-vids-dashboard`. You can also ask Broader Vids to recommend videos or open the dashboard.

Reset and Clean require confirmation. Neither action uninstalls the plugin.

## Bilibili access and exclusions

Bilibili needs manual sign-in for full video evidence. Setup opens a separate browser. Keep it open after sign-in, then report completion in chat. Broader Vids checks the session and closes the browser. You can switch accounts from the dashboard.

Broader Vids imports your account blacklist after sign-in checks and before new rounds. Imported creator exclusions combine with your manual exclusions. A failed sync keeps the last complete copy. Use **Sync blacklist** in the dashboard to retry.

## Architecture and guides

The Node launcher manages a versioned local Python engine, which stores profile data locally and serves the dashboard.

Read the [workflow](docs/WORKFLOW.md) for recommendation steps. Read the [scoring guide](docs/SCORING.md) for ranking and learning. The [reference](docs/REFERENCE.md) covers commands, troubleshooting, updates, and maintenance. The [architecture guide](docs/ARCHITECTURE.md) describes the launcher, engine, and local storage.

License: [MIT](LICENSE).
