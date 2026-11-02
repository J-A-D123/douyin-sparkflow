# Changelog

## Unreleased — reliability and publication preparation

- Default to the ordinary Douyin chat UI for existing conversations and sends, running the account browser in the visible local desktop through authenticated, leased dispatch.
- Decode known protobuf send responses conservatively; ordinary chat additionally requires a fresh server-history match by message ID, body, sender, callback status and visibility.
- Wait for hydrated, unique conversation labels and matching selected-row/header state; require the rich editor's own send-ready state and click its corresponding send icon.
- Add a single-target preparation check that never clicks send or mutates dispatch/history state. Platform rejection, self-only visibility and unknown outcomes stop the account without automatic replay.
- Run sender and independent verifier sequentially to respect desktop PID limits; preserve auxiliary history values verbatim and distinguish them from documented send status/check fields.
- Detect login/verification pages that appear during sender identity loading, preserving their actionable failure category instead of reporting a generic identity timeout.
- Open logs at the newest entry, show record time separately from read time, and clarify the automatic pause threshold versus accumulated manual attempts.
- Fixed false success from HTTP 200 responses containing only `decision`; browser confirmation now requires explicit business success and a server message ID. Downgraded insufficient legacy receipts and excluded uncertain history from automatic replay.
- Always activate the friend inbox even when another inbox already shows rows; verify the actual browser account before opening chat or refreshing saved session cookies.
- Recognize Creator landing/login and verification screens before searching for friends; stop with actionable diagnostics instead of timing out on an obsolete XPath.
- Require visible friend-tab labels and distinguish application send pauses from platform account restrictions; isolate login-workspace test files from runtime files.
- Added durable daily scheduler reservations and one overdue dispatch after startup or wake.
- Applied daily deduplication to fixed schedules; uncertain attempts remain reserved for manual verification.
- Replaced geometry-based message confirmation; ordinary chat uses message-ID server readback, while the legacy Creator adapter uses a pre-send count baseline alongside observed send responses.
- Bounded editor/list selection, preserved drafts, and skipped hidden cached friend rows.
- Added login-service authentication, disabled debug routes by default, and removed raw network/header capture endpoints.
- Kept alphanumeric account handles distinct and rejected a different account during relogin.
- Added account-free CI, source privacy checks, sanitized diagnostics, and optional macOS AC-only idle-sleep support.
- Removed cloud account execution and private-log uploads from GitHub workflows. Preserved upstream license and attribution.
- Real-account delivery and multi-day unattended behavior remain separate acceptance gates; unit tests alone do not establish platform compatibility.

## 2026-08-27

- Changed the project license for project-owned code from MIT License to PolyForm Noncommercial License 1.0.0. Future versions are source-available for noncommercial use only; prior versions remain governed by their applicable license terms.
- Added prominent non-official project, platform-risk, account-authorization, data-responsibility, commercial-use, and intellectual-property notices to the README files.

## 2026-08-25

- Made the experimental branch default to direct Douyin business traffic; Mihomo is now an explicit advanced option for browser traffic.

- Made login-browser networking direct-first with Mihomo fallback and explicit preflight errors.

## 2026-07-11

- Reduced login-desktop resource spikes with lazy Chromium startup, idle/after-export shutdown, status caching, reduced-motion page styling, lower-frequency x11vnc polling, and Compose CPU/memory/PID limits.

- Changed QR refresh from re-reading an expired image to forcing the Douyin login page to generate a new cache-busted QR code, and stopped serving expired QR screenshots.

- Fixed a blank Douyin login desktop by preventing build-time localhost proxy variables from leaking into runtime Chromium and explicitly wiring lowercase/uppercase runtime proxy variables.

- Added an authenticated same-origin noVNC HTTP/WebSocket proxy and synchronous mobile popup handling so login tasks work from phones without exposing port 8788 publicly.

- Fixed the server image to provision Node.js 22 explicitly for protocol mode and excluded runtime logs/configuration from the Docker build context.

- Reworked the Web console into a responsive unified operations dashboard with local Lucide icons, clearer account/target status, and safer confirmation dialogs.
- Added strong send-confirmation state handling, retryable unconfirmed targets, failure categorization, account cooldowns, and focused manual retry modes.
- Improved friend-list discovery, persistent browser profiles, Cookie synchronization, and browser/protocol failure persistence.
- Expanded the login desktop service with more robust Douyin identity export and bounded diagnostic endpoints.
- Added authenticated no-cache overview data that excludes message contents, Cookie data, receipts, and server credentials.
- Added unit coverage for send-state behavior, stale-lock safety, overview redaction, authentication, and public settings.
- Made stale-lock PID probing portable across Linux and Windows and sanitized the public configuration template.
- Removed obsolete standalone account, login-workspace, and settings templates now consolidated into the dashboard.
- Fixed standard Compose scheduling by sharing persistent browser profiles with scheduler/task services, while limiting host Docker access to services that actually need it.
- Moved the GitHub Actions workflow to the repository root, corrected its working directory/tests/profile/artifact paths, and pinned actions to commit SHAs.
- Split the tracked `config.example.json` template from the ignored runtime `config.json`, with update-time config preservation.
- Made login-desktop API/public URLs honor container environment settings and synchronize the configured schedule at Web startup.
- Bound noVNC and Mihomo host ports to loopback by default and documented SSH-tunnel access.
- Added an explicit Node.js image build check for protocol mode, made the cron reader tolerate Windows UTF-8 BOM files, and removed unused legacy login-session, relogin-worker, and nested Compose entrypoints.

## 2026-05-30

- Redesigned the Web UI into dedicated overview, login workspace, accounts, send console, logs, and settings views.
- Moved the console styling and interaction scripts into `DouYinSparkFlow/webui/static/` with a denser admin-console layout and mobile drawer navigation.
- Added safer confirmations for manual operations and localized the main Web UI feedback messages.
- Removed the redundant send-console navigation label while keeping send details available from contextual actions.
- Added a persistent sun/moon toggle for switching between light and dark console themes.
- Updated the README preview image and added a screenshot-based usage guide.
- Made the screenshot usage guide entry more visible in the README.
- Added a project disclaimer to the root README.
- Added a Linux Do friendly link and GitHub star history chart.
- Refined the root README wording and structure for an open source project style.
- Reordered the disclaimer and star history chart, and refined runtime file wording.
- Added upstream project attribution and emoji-styled README sections.
- Added a masked UI preview image to the root README.
