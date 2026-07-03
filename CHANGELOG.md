# Changelog

## [v10.10.5] - 2026-07-03

- **Docs:** refreshed sibling-repo version references in the README's "Related Projects" table (Home Assistant, ioBroker, MCP Server, Node-RED, Python Frontend) — several releases behind.

## [v10.10.4] - 2026-06-29

- **Gen2 PERSON-tag fix:** icon, Signal notification, and webhook payload now correctly identify person events (was using the wrong tag key for Gen2 cameras)

## [v10.10.3] - 2026-06-18

**Log hygiene: redact RTSP credentials in status/launch log lines (CLI-1, cross-port with HA `_redact_rtsp_creds`).** New `redact_rtsp_creds()` helper masks the `user:password@` userinfo to `***:***` (host/path/query kept) when an rtsp(s):// URL is printed as a status/progress line — the `live` command's "Launching ffplay/mpv …" and "RTSPS URL" lines. The deliverable URL output stays UNREDACTED on purpose: `test-local`'s "RTSP URL", `info`'s stream URLs and the no-player "Stream URL" fallback are the copyable URLs the user runs those commands to obtain. Regression tests in `tests/test_mig_cli1_rtsp_redaction.py`.

## [v10.10.2] - 2026-06-11

**Security: verify TLS for Bosch cloud and proxy calls (CWE-295, GHSA-6qh5-x5m5-vj6v).** The cloud REST API (`bosch_cloud_ssl.py`) and the live video TLS proxy (`bosch_tls.py`) now validate the Bosch private CA instead of accepting any certificate (`verify=False`). This closes an adjacent-network MITM vector that could expose OAuth tokens and stream URLs to an attacker who can intercept TLS on the local network. Local camera LAN endpoints are unchanged — they remain TOFU-pinned via `CertPinningError` as before.

## [v10.10.1] - 2026-05-29

**Fix: intrusion distance + intercom audio levels (cross-port with HA / ioBroker).** `intrusion --distance` now clamps to 1–8 m — the camera rejects values above 8 with HTTP 400, so the previous 1–10 range made the write fail. `intercom --speaker-level` now reads the current `/audio` config and PUTs the full `{audioEnabled, microphoneLevel, speakerLevel}` body, so setting the speaker level no longer wipes the microphone level (and the `speakerLevel` field casing is corrected). New regression tests in `tests/test_audio_intrusion_wifi.py` pin both fixes; the `--distance 1-8` range is reflected in the usage/help text and README.

## [v10.10.0] - 2026-05-28

- **siren** rewritten end-to-end. The documented `/v11/video_inputs/{id}/acoustic_alarm` endpoint returns HTTP 404 in production on every camera model tested (Gen1 INDOOR/OUTDOOR + Gen2 HOME_Eyes_Indoor — verified 2026-05-28 against firmware 9.40.102 and 7.91.56). Switched to `PUT /v11/video_inputs/{id}/panic_alarm` body `{"status":"ON"|"OFF"}` — the same endpoint the HA integration's `BoschPanicAlarmSwitch` uses successfully. Only Gen2 Indoor II (`HOME_Eyes_Indoor`) has working siren hardware; other models now print a model-aware skip message instead of issuing a doomed PUT.
- **siren --stop** flag added. Cancels an active panic alarm before its configured duration expires.
- **siren --set-duration N** flag added. The `panic_alarm` endpoint does NOT accept a duration field — duration is camera-side state at `PUT /v11/video_inputs/{id}/alarm_settings` field `alarmDelayInSeconds` (range 10–300 s). The CLI now fetches the current `alarm_settings`, patches the duration, PUTs the updated config, then triggers the alarm in one command.
- **rcp / rcp-version / onvif-scopes / snapshot-mjpeg** no longer crash with `SSLCertVerificationError` on Python 3.14 / macOS Homebrew. Four bare `requests.put` / `requests.get` calls inside the proxy-RCP path were missing the `verify=False` kwarg that `make_session()` sets globally.
- **config** now masks every secret-shaped field — not just `bearer_token` / `refresh_token`. Universal masker walks the full config dict and abbreviates any value whose key contains `token` / `password` / `secret` / `private`.
- **pan** HTTP 443 now suggests the privacy-mode root cause.
- **rescan** no longer crashes with `EOFError` when run from a non-TTY (CI, cron, piped).
- **unread** uses a working endpoint. The documented `/v11/video_inputs/{id}/unread_events_count` returns HTTP 404 in production; switched to the per-camera detail call.
- **shared** correctly skips Gen1 cameras. The `/shared_with_friends` endpoint returns HTTP 404 on Gen1 INDOOR + OUTDOOR; added a model guard with friendly message.
- **status** detects newly-added Bosch-app cameras. Now fetches `/v11/video_inputs` live and prints a hint when new cameras are found — without auto-rewriting the config.

## [v10.9.0] - 2026-05-25

- audio-alarm command removed: cross-version mirror of HA v13.2.0 audioAlarm cleanup.
- Bosch session-quota 444 reported as a distinct state instead of conflated with OFFLINE.

## [v10.7.7]

**Fix: event snapshots + clips downloadable again.** The shared `requests.Session` default `Accept: application/json` caused Bosch's nginx to respond `HTTP 500 sh:internal.error` on `/v11/events/{id}/snap.jpg` and `/v11/events/{id}/clip.mp4`. Default is now `Accept: */*`; binary endpoints work without each call site having to override the header. New regression suite (`tests/test_accept_header.py`) pins the session default + the imageUrl/videoClipUrl end-to-end.

## [v10.7.6]

**Security pass (cross-port from HA v12.7.2).** `defusedxml.ElementTree` replacing `xml.etree` in `bosch_maintenance.py` (XXE hardening). TOFU certificate fingerprint pinning for self-signed camera certs via new `bosch_tls.py` module (`bosch_get`/`bosch_post`/`bosch_put`). Fingerprints stored in `bosch_config.json[cam_cert_fingerprints]`; mismatch raises `CertPinningError`. `get_token.py` Keycloak calls switched to `verify=True`. 21 new tests in `tests/test_cert_pinning.py`.

## [v10.7.5]

**PTZ pan presets + webhook event delivery (cross-port from HA v12.7.0).** New `pan <cam> --preset home|left|right|back-left|back-right` flag — named pan positions (0° / -60° / +60° / -120° / +120°). New `watch --webhook URL` flag — POSTs JSON `{camera, event_type, timestamp, extra}` to user-configured HTTP endpoint on every motion / audio_alarm / person / intrusion event. 21 new regression tests in `tests/test_pan_presets.py` (14) + `tests/test_webhook.py` (7).

## [v10.7.4]

**Audio/Intrusion/WiFi commands (cross-port from HA v12.7.0).** `bosch audio [<cam>] [--mic N] [--speaker N] [--json]`: get/set microphone and speaker levels 0–100. `bosch intrusion [<cam>] [--mode indoor|outdoor] [--sensitivity 0-7] [--distance 1-10] [--json]`: get/set intrusion detection config. `bosch wifi [<cam>] [--json]`: read-only WiFi info.

## [v10.7.3]

**LAN-fallback feature set (cross-port from HA v12.4.10).** `bosch ping [<cam>] [--json]`: TCP-connect probe to each camera's LAN IP port 443. `bosch privacy <cam> on|off --local`: writes directly via LAN RCP (Gen2 only, no token needed). `bosch light <cam> on|off|intensity N --local`: same for front-light brightness. `bosch lan-ips [set|unset|sync]`: list and edit the `cam_id → LAN IP` map stored in `bosch_config.json`.

## [v10.7.2]

**`maintenance` subcommand (cross-port from HA v12.4.5).** `bosch maintenance` fetches Bosch community RSS feeds (Wartungsarbeiten + Statusmeldungen) and shows the current state (active / scheduled / past / recent). Falls back to HTML scraping when RSS is unavailable. `--json` flag for scripting.

## [v10.7.1]

**FCM cleanup — remove iOS path (aligned with HA v12.4.5).** Removed the iOS Firebase key. The OSS-sanctioned Android key handles all platforms. `deviceType` is now hardcoded to `"ANDROID"`. The `auto` push-mode dispatch chain collapses from iOS→Android→polling to Android→polling.

## [v10.7.0]

**Mini-NVR (BETA).** `watch --auto-record`: motion rising edge → ffmpeg MP4 clip, falling edge → clean stop. `nvr` subcommand: `status`, `list`, `prune`, `upload`. FIFO clip eviction (default 50 per camera). Optional SMB/NAS upload via `smbprotocol` with per-upload fresh connection cache. 13 new i18n keys across all 11 languages. +370 LOC, +46 tests.

## [v10.2.1]

**Revert privacy-mode cross-check from v10.2.0.** A/B testing proved `0x0d00 byte[1]` stays `1` independent of the user-facing privacy-mode toggle. The "Privacy MISMATCH" line produced a permanent false positive. **Kept:** the v10.2.0 `?JpegSize=1206` snap.jpg latency fix.

## [v10.2.0]

**Cross-version sync with HA integration v10.4.5 + v10.4.8.** (1) Fix: LOCAL `snap.jpg` is now ~1.4 s instead of ~6–10 s when the camera is idle — append `?JpegSize=1206` to the local snap URL. (2) New: privacy-mode cross-check in `--status` RCP block.

## [v10.1.2]

**Atomic save_config.** `save_config()` now writes to a temp file in the same directory and `os.replace()`s atomically (POSIX rename guarantee), so a crash during write can no longer leave a half-written `bosch_config.json`.

## [v10.1.1]

**Switch FCM push to official Bosch OSS Google API key.** Firebase/FCM registration now uses the official OSS key provided by Bosch. No user action required.

## [v10.1.0]

**Thread safety + TLS proxy reliability.** (1) Pre-emptive token refresh: new `_is_token_near_expiry(token_str, buffer_secs=60)`. (2) TLS proxy reconnect with exponential backoff (1 s → 2 s → 4 s, 3 failures → exit). (3) Connection pooling: singleton `requests.Session` with `HTTPAdapter(pool_connections=10, pool_maxsize=20)`. (4) Graceful shutdown via `threading.Event` SIGTERM/SIGINT handler. (5) Retry with backoff: `_request_with_retry`, 3 attempts, 1 s/2 s/4 s backoff on 5xx/Timeout/ConnectionError.

## [v10.0.0]

**Security hardening release (full pentest).** (1) `urllib3.disable_warnings()` scoped to `InsecureRequestWarning` only (was global suppression). (2) `bosch_config.json` file permissions set to `0600` (owner-only) on every save.

## [v9.0.4]

Version bump only.

## [v9.0.3]

**Fix: Mark events as read uses correct API shape.** `api_mark_events_read` now sends per-event PUTs with body `{id, isRead: true}` — events actually get marked as read on the cloud.

## [v9.0.2]

**Automatic OAuth login.** `get_token.py` now uses a local HTTP callback server (`localhost:8321`) — after browser login, Bosch Keycloak redirects back automatically and the auth code is captured without manual URL copy-paste.

## [v9.0.1]

**Info: intrusion detection + ambient light fix.** `info --full` now shows `intrusionDetectionConfig` for Gen2 cameras. Fixed ambient light field name (`ambientLightSensorLevel`).

## [v9.0.0]

**Gen2 camera support.** Gen2 model names (`HOME_Eyes_Outdoor`, `HOME_Eyes_Indoor`). Firmware update detection (`UPDATING_REGULAR` → 🔄). Proxy dump path fix.

## [v8.0.4]

**OSS OAuth credentials.** Switched to dedicated Bosch OSS OAuth client (`oss_residential_app`) — provided by Bosch for open source projects. Re-login required (`python3 get_token.py`).

## [v8.0.3]

**New commands + protocol check.** New: `accept-invite` (accept friend invitation), `shared` (show which friends have camera access). Protocol version check on startup. Dynamic hardware version display.

## [v8.0.0]

**Complete Gen1 Support.** All discovered Bosch Cloud API endpoints implemented — 100% coverage for Gen1 cameras. Includes: motion zones (`zones`), privacy masks (`privacy-masks`), lighting schedule (`lighting-schedule`), extended rules edit, friends/sharing, and all camera controls.

## [v7.4.0]

**Lighting schedule command** (`lighting-schedule`): view and modify light schedule for outdoor cameras (on/off times, motion trigger, darkness threshold).

## [v7.3.0]

**Privacy masks command** (`privacy-masks`): list, set, and clear privacy mask zones via cloud API.

## [v7.2.0]

**Motion zones command** (`zones`): list, set, and clear motion detection zones via cloud API. **Rules edit extended:** `edit` now supports `--name`, `--start`, `--end`, `--days`.

## [v7.1.0]

TCP keep-alive on TLS proxy sockets (10 s idle / 5 s interval / 3 probes). Directional select timeout for stable LOCAL streams.

## [v7.0.0]

LOCAL LAN streaming with TLS proxy. Menu: local stream entries + exit with "q". Code cleanup.

## [v5.2.0]

Fix live stream session duration (`maxSessionDuration=3600` — stream runs up to 60 min).

## [v5.1.0]

New commands: privacy-sound, rules, friends, rename, profile, account. HTTP 444 handling.

## [v4.0.0]

Intercom (listen-only), siren command, unread events, person detection icon, mark-as-read, `--push-mode` flag.

## [v3.0.0]

FCM push notifications (`watch --push`): real-time event detection via Firebase Cloud Messaging (~2s latency instead of 30s polling). Signal messenger alerts: `watch --signal`. Auto-follow command. Fixed motion sensitivity enum (`DISABLED` → `OFF`).

## [v2.0.0]

Code cleanup: removed dead code, consolidated helper functions, unified error handling.

## [v1.9.0]

Push notification architecture documented from APK analysis.

## [v1.8.0]

RCP session caching (2-step handshake cached per proxy connection, 5-min TTL). `watch --snapshot`: auto-downloads event JPEG on new events. `rcp snapshot` resolution fix.

## [v1.7.0]

`--quality` flag: `auto` (default, ~7.5 Mbps), `high` (~30 Mbps), `low` (~1.9 Mbps).

## [v1.6.0]

`watch` command: real-time event polling with `--interval` and `--duration`. `motion` command: get/set motion detection + sensitivity. `recording` command: get/set cloud recording sound on/off.

## [v1.5.0]

`maxSessionDuration` 60 → 3600s, `--hq` flag, `--inst N` flag, `rcp bitrate`.

## [v1.4.0]

`rcp frame` (YUV422 → JPEG), `rcp script` (IVA automation), `rcp iva` (rule types).

## [v1.3.0]

`rcp` command: low-level RCP protocol reads via cloud proxy.

## [v1.2.0]

`info --full`, `privacy on --minutes N`, WiFi info, proxy 404 retry, 3-state notifications.

## [v1.1.0]

`light`, `privacy`, `notifications`, `pan` commands, auto token renewal, interactive menu.

## [v1.0.0]

Initial release — status, snapshot, liveshot, live, download, events.
