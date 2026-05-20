# Refactor: bosch_camera.py monolith → package

Status: DESIGN ONLY — no source changes yet
Source: `bosch_camera.py` — 8378 lines, 114 top-level defs/classes
Current coverage: ~24% (structural artifact of a monolith, not lack of tests)
Target coverage after refactor: ~70–85%

## Open questions (decide before Phase A)

1. **Module name**: Keep `bosch_camera` package name (backward-compat, all tests use it) OR rename to `boschcam` (shorter, PEP-8 clean)? Recommendation: keep `bosch_camera/` — zero import churn.
2. **Config schema ownership**: `DEFAULT_CONFIG` + `CONFIG_FILE` used by 12+ modules — centralize in `config.py` or `const.py`? Recommendation: `config.py` (mutable runtime state) + `const.py` (immutable strings/maps).
3. **`main()` size**: 1431 lines of argparse wiring. Split per-subcommand into small `_add_<cmd>_parser()` helpers in `cli.py`, or keep flat? Recommendation: flat but extracted into `cli.py`; per-command parsers as nested helpers.
4. **Backward-compat entry point**: `bosch_camera.py` at repo root must still work as `python bosch_camera.py …`. Keep as 3-line shim after package creation.

---

## Proposed module structure: `bosch_camera/` package

| Module | Source lines (approx.) | Responsibility | Public surface | Depends on |
|---|---|---|---|---|
| `const.py` | 82–176 | All compile-time constants: `CLOUD_API`, `VERSION`, `HW_DISPLAY_NAMES`, `LIVE_TYPE_CANDIDATES`, `DEFAULT_CONFIG` | all constants | nothing |
| `config.py` | 176–391 | `load_config`, `save_config`, `_create_default_config`, `_merge_defaults` | `load_config`, `save_config` | `const` |
| `auth.py` | 236–430 | Token decode/expiry, `get_token`, `check_token_age`, `handle_401`, `make_session` | `get_token`, `make_session`, `handle_401` | `config`, `const` |
| `api.py` | 446–648 | `_request_with_retry`, `_install_stop_handlers`, `_maybe_print_maintenance_hint`, `open_file`, `open_vlc` | `_request_with_retry`, `open_file`, `open_vlc` | `auth` |
| `cameras.py` | 500–710 | `discover_cameras`, `get_cameras`, `resolve_cam`, `api_ping`, `api_get_events`, `api_mark_events_read`, `api_get_camera` | all listed | `api`, `config`, `const` |
| `snapshot.py` | 745–994 | `snap_from_proxy`, `snap_from_local`, `snap_from_events`, `_save_and_open`, `cmd_snapshot`, `_live_snap_loop` | `snap_from_proxy`, `snap_from_local`, `snap_from_events`, `cmd_snapshot` | `api`, `cameras`, `lan_fallback` |
| `stream.py` | 1133–1843 | TLS proxy, RTSPS open, dual-stream URL builder, WebRTC/go2rtc, `cmd_live`, `cmd_test_local` | `cmd_live`, `cmd_test_local`, `_build_stream_urls` | `snapshot`, `api`, `lan_fallback` |
| `lan_fallback.py` | 2195–2400 | `_lan_rcp_write`, `_lan_rcp_write_privacy`, `_lan_rcp_write_front_light`, `_lan_tcp_ping`, `_resolve_lan_ip`, `_hint_local_on_5xx`, `cmd_ping`, `cmd_lan_ips` | `cmd_ping`, `cmd_lan_ips`, `_resolve_lan_ip`, `_lan_tcp_ping` | `api`, `cameras` |
| `privacy.py` | 2401–2527 | `cmd_privacy` | `cmd_privacy` | `api`, `lan_fallback`, `cameras` |
| `light.py` | 2528–2762 | `cmd_light` | `cmd_light` | `api`, `lan_fallback`, `cameras` |
| `pan.py` | 2763–2893 | `cmd_pan`, `cmd_autofollow` (5563–5628) | `cmd_pan`, `cmd_autofollow` | `api`, `cameras` |
| `notifications.py` | 2894–2969 | `cmd_notifications`, `cmd_notification_types` (6881–6947) | both cmd_* | `api`, `cameras` |
| `watch.py` | 2970–3386 | FCM constants, `_get_fcm_api_key`, `_send_signal_alert`, `_post_event_webhook`, `_watch_fcm_push`, `MotionEdgeTracker`, motion snapshot helpers, `cmd_watch` | `cmd_watch`, `MotionEdgeTracker` | `api`, `cameras`, `snapshot`, `recorder` |
| `recorder.py` | 3387–3704 | NVR clip management, `_nvr_*` helpers, SMB upload shim, `cmd_nvr` | `cmd_nvr`, `_nvr_is_recording`, `_start_motion_recording` | `api`, `cameras`, `smb_upload`, `watch` |
| `smb_upload.py` | 3522–3593 | `_nvr_smb_upload` (extracted from recorder) | `_nvr_smb_upload` | `config` |
| `audio.py` | 4256–4492 | `cmd_audio_alarm`, `cmd_recording`, `cmd_audio` | all cmd_* | `api`, `cameras` |
| `intrusion.py` | 4493–4611 | `cmd_intrusion` | `cmd_intrusion` | `api`, `cameras` |
| `wifi.py` | 4612–4698 | `cmd_wifi` | `cmd_wifi` | `api`, `cameras` |
| `rcp.py` | 4699–5249 | `rcp_open_connection`, `rcp_session`, `rcp_session_cached`, `rcp_read`, `rcp_parse_*`, `_rcp_setup`, `cmd_rcp` | `rcp_read`, `rcp_session`, `cmd_rcp` | `api`, `cameras`, `lan_fallback` |
| `info.py` | 1844–2194, 5250–5328, 5666–5695, 6259–6602, 6814–6880 | `cmd_info`, `cmd_config`, `cmd_status`, `cmd_events`, `cmd_token`, `cmd_rescan`, `cmd_unread`, `cmd_zones`, `cmd_privacy_masks`, `cmd_timestamp`, `cmd_rename`, `cmd_profile`, `cmd_account` | all cmd_* | `api`, `cameras`, `auth` |
| `rules.py` | 5777–6161 | `cmd_rules` | `cmd_rules` | `api`, `cameras` |
| `friends.py` | 5966–6258 | `cmd_friends`, `cmd_accept_invite`, `cmd_shared_with_friends` | all cmd_* | `api`, `cameras` |
| `maintenance.py` | 4128–4181 | `cmd_maintenance` (thin wrapper over `bosch_maintenance.py`) | `cmd_maintenance` | `api`, external `bosch_maintenance` |
| `motion.py` | 4182–4255 | `cmd_motion` | `cmd_motion` | `api`, `cameras`, `watch` |
| `menu.py` | 5329–5562 | `cmd_menu` (interactive REPL) | `cmd_menu` | all cmd_* modules |
| `siren.py` | 5629–5665 | `cmd_siren` | `cmd_siren` | `api`, `cameras` |
| `privacy_sound.py` | 5696–5776 | `cmd_privacy_sound` | `cmd_privacy_sound` | `api`, `cameras` |
| `lighting_schedule.py` | 6456–6540 | `cmd_lighting_schedule` | `cmd_lighting_schedule` | `api`, `cameras` |
| `intercom.py` | 4008–4127 | `cmd_intercom` | `cmd_intercom` | `api`, `cameras` |
| `cli.py` | 6948–8378 | All argparse wiring, `main()` entry | `main` | all cmd_* modules |
| `__init__.py` | (new, ~20 lines) | Re-export every public symbol; keeps `from bosch_camera import X` working | all public symbols | all submodules |

Existing external files (unchanged):
- `bosch_maintenance.py` — keep as-is; imported by `maintenance.py`
- `bosch_i18n.py` — keep as-is; imported by most modules
- `bosch_camera.py` (root) — becomes 3-line shim: `from bosch_camera.cli import main; main()`

---

## Test file mapping

| Existing test file | Covered module(s) after refactor |
|---|---|
| `test_config.py` | `config.py`, `const.py` |
| `test_token.py` | `auth.py` |
| `test_lan_fallback.py` | `lan_fallback.py` |
| `test_dual_stream.py` | `stream.py` |
| `test_webrtc.py` | `stream.py` |
| `test_nvr.py` | `recorder.py`, `smb_upload.py` |
| `test_motion_edge.py` | `watch.py` (MotionEdgeTracker) |
| `test_pan_presets.py` | `pan.py` |
| `test_audio_intrusion_wifi.py` | `audio.py`, `intrusion.py`, `wifi.py` |
| `test_webhook.py` | `watch.py` |
| `test_maintenance.py` | `maintenance.py` |
| `test_resolve_cam.py` | `cameras.py` |
| `test_i18n.py` | `bosch_i18n.py` (unchanged) |
| (new) `test_snapshot.py` | `snapshot.py` — currently 0 tests |
| (new) `test_rcp.py` | `rcp.py` parse helpers — currently 0 tests |
| (new) `test_info.py` | `info.py` / `cmd_info` smoke — currently 0 tests |

---

## Migration plan

### Phase A — Setup (~1 day)
1. `mkdir bosch_camera/`
2. Create `const.py` (copy constants block, lines 82–176).
3. Create `config.py` (lines 176–391).
4. Create `bosch_camera/__init__.py` — re-export all symbols from `const` + `config`.
5. Root `bosch_camera.py` keeps its full content; tests still import from it.
6. Run `pytest tests/ -x` — must be green before Phase B.
7. Risk: none (additive only).

### Phase B — Auth + API (~2 days)
1. Move `auth.py` (lines 236–430) + `api.py` (lines 430–710).
2. Add re-exports to `__init__.py`.
3. `bosch_camera.py` root: replace moved blocks with `from bosch_camera.auth import *; from bosch_camera.api import *`.
4. Run full pytest after each move.
5. Risk: `make_session` imports `requests` — verify no circular dep through `api`.

### Phase C — Subcommands (~3 days)
Move one command module per half-day, alphabetical order:
`cameras` → `snapshot` → `lan_fallback` → `stream` → `privacy` → `light` → `pan` → `watch` → `recorder` → `smb_upload` → `audio` → `intrusion` → `wifi` → `rcp` → `info` → `rules` → `friends` → `notifications` → `maintenance` → `motion` → `menu` → `siren` → `privacy_sound` → `lighting_schedule` → `intercom`.
After EACH move: `pytest tests/ -x --tb=line`. Stop on first failure, fix before continuing.
No test file changes allowed in this phase — all failures are import/re-export bugs.

### Phase D — CLI entry (~1 day)
1. Move `main()` + argparse wiring (lines 6948–8378) to `cli.py`.
2. Create `bosch_camera/__main__.py`: `from bosch_camera.cli import main; main()`.
3. Root `bosch_camera.py` becomes shim: 3 lines.
4. Update `pyproject.toml` entry point: `bosch_camera = "bosch_camera.cli:main"`.
5. Run full pytest + manual `python bosch_camera.py status`.

### Phase E — Cleanup (~1 day)
1. Remove dead stubs from root `bosch_camera.py` (now pure shim).
2. Update all `from bosch_camera import X` in tests to direct module imports (optional — re-exports still work).
3. Run `pytest tests/ --cov=bosch_camera --cov-report=term-missing`.
4. Add `# pragma: no cover` on any defensive arms that are genuinely unreachable.
5. Regenerate coverage badge in README.

Total estimated effort: **8 person-days**
Recommended sprint window: dedicate one week; no parallel feature work during Phases B–D.

---

## Risk assessment

| Risk | Severity | Mitigation |
|---|---|---|
| 322 tests use `import bosch_camera` / `from bosch_camera import X` | High | `__init__.py` re-exports every moved symbol; tests need zero changes through Phase D |
| `DEFAULT_CONFIG` + `CONFIG_FILE` referenced in 12+ places | High | Move to `const.py` first (Phase A); all later imports pick up from there |
| `MotionEdgeTracker` in `watch.py` imported by `recorder.py` (motion recording) AND tested separately | Medium | Move together as one unit; `recorder.py` imports `MotionEdgeTracker` from `watch` |
| Circular import risk: `cameras` → `api` → `auth` → `config` | Medium | Enforce strict layering: `const` → `config` → `auth` → `api` → `cameras` → commands. No upward imports. |
| `main()` is 1431 lines — largest single extraction | Medium | Purely declarative argparse wiring; split into `_add_<cmd>_parser(sub)` helpers but keep in single `cli.py` |
| `cmd_menu` (interactive REPL, 5329–5562) imports all cmd_* | Low | Move last (Phase C end); import all cmd modules explicitly |
| `smb_upload` import is soft (`importlib` / try-except) | Low | Mirror the try-except in `smb_upload.py`; `recorder.py` calls it via the same guard |
| `bosch_config.json` path computed from `__file__` in `const.py` | Low | Use `Path(__file__).parent` (already relative to module location) |

---

## Expected coverage outcome

Current state: 24% — measured against 8378-line single file; untested command bodies drag percentage.
After refactor:
- Each submodule measured independently; already-tested helpers become 100%-covered small files.
- Estimated per-module coverage: `auth` 95%, `config` 95%, `cameras` 80%, `lan_fallback` 90%, `stream`/`watch`/`recorder` 70–80%, command modules 40–60% (large interactive paths).
- **Projected aggregate: 70–85%**, primarily because `cli.py` argparse wiring and interactive `cmd_menu` will remain low (both are practically untestable without subprocess fixtures).
- New test files needed for full Gold-tier: `test_snapshot.py`, `test_rcp.py`, `test_info.py` (smoke only).
