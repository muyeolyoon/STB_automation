# Default behavior — reference

> 한글판: [reference.ko.md](reference.ko.md)

Read from `SKILL.md` only when you need checklist/env detail.

## Checklist 1–6

| # | Key / state | PASS idea (do not invent beyond this) |
|---|-------------|----------------------------------------|
| 1 | `_run_checklist["versions"]` | After reboot: Firmware (`V.xx.xx.xxxx`) + SDK + Agent from logcat/getprop. Skipped when `SKIP_REBOOT=1` or `SKIP_VERSION_CHECK=1`. |
| 2 | `ad_playback` | Internal ad on **non-kids** channel: cue−2s ≤ Σ ImpressionLog `playTime` ≤ cue; impression API 200. Attempts + check2 bonus. |
| 3 | `google_ad` subs `full_play` / `leave_during` / `skip_ok` | 3-A Quartile+COMPLETE; 3-B leave stops tracking; 3-C SKIPPABLE→SKIPPED. Skip all with `SKIP_GOOGLE_CHECK=1`. |
| 4 | `leave_during_ad` | Leave **+30s wall after `play ====`**; then player play→stop ≈ ImpressionLog playTime sum (±`PLAYTIME_MATCH_TOLERANCE_MS`). |
| 5 | `leave_before_play` | After `register cue`, leave before play → no play / no impression. |
| 6 | `kids_check6` + UI | Kids ch `311,320-324,328`: kid=true cue / isKid / KidWatermarkManager / `kid_watermark.png`; UI OCR “광고 방송” burst. |

After checklist done (or attempts exhausted): `run_schedule_slot_monitor` — internal playTime/impression, Google quartile/tracking/skip, kids watermark+OCR. Slot with no cue/play within `SLOT_AD_START_TIMEOUT_SEC` → `[편성 스킵]`.

## Internal ad logcat phases (`AD_PLAYBACK_PHASES`)

Typical order: `register cue` → `ads will play in` → load → play start → `AnypointAdPlayerImpl.play` → callOnPlay → prepareStop → player stop → onStopped → `impression log size` → ImpressionLog → `impression-logs` POST.

## Important env flags

| Env | Role |
|-----|------|
| `STB_DEVICE_IP` / `STB_DEVICE_IPS` | Target STB(s); comma/space/semicolon |
| `SKIP_REBOOT` | Default `1` in ps1 — skip reboot **and** version check. Direct `.py` with unset env → interactive y/N (N=skip) |
| `APPLY_LOCAL_API_ENDPOINT` | `1`+model → apply non-interactively; `0` → skip; unset → ask |
| `STB_LOCAL_API_HOST` | Local PC host (default `192.168.10.150`) |
| `STB_LOCAL_API_MODEL` | Path model segment (e.g. `UHD3`) when same for all devices |
| `STB_API_ENDPOINT` | Full URL (`http://192.168.10.150/UHD3`) applied to every device |
| `VERSION_ONLY` | Reboot + versions then exit |
| `SKIP_VERSION_CHECK` | Skip check 1 only |
| `SKIP_GOOGLE_CHECK` | Skip 3-A/B/C |
| `CHECKLIST_ONLY` | Stop after checklist; no long schedule monitor |
| `STB_ESCAPE_CHANNEL` | Leave/final channel (default `3`) |
| `DRIVE_SCHEDULE_FILE_ID` / `SCHEDULE_SOURCE` / `SCHEDULE_SECTION` | Schedule load (`drive`, `uplus`) |
| `GOOGLE_CHAT_SPACE` | Optional Chat report space |
| `STB_TERMINAL_LOG` / `STB_LOG_FILE` | Terminal mirror / log name stem |
| `CHECKLIST_CHECK_MAX_ATTEMPTS` | Per-check attempt cap (default 3) |
| `CHECK2_BONUS_MAX_ATTEMPTS` | Extra check-2 tries after others done |
| `SLOT_AD_START_TIMEOUT_SEC` | No cue/play → skip slot (default 90) |
| `MONITOR_GOOGLE_AUTO_SKIP` | Auto OK key on SKIPPABLE during monitor |
| `CHANNEL_SWITCH_CLEAR_LOG` | Dangerous; default off |
| `AD_SYNC_PACKAGE` | LGU package for AD_SYNC broadcast |

## Global state to respect

- `_run_checklist`, `_check_attempt_counts`, `_checklist_extra_phase`
- `_active_ad_trackers`, `_active_google_trackers`, `_active_kids_watermark_trackers`
- `_channel_switch_lock`, `_pending_tune_targets`
- `_critical_issues` (Anypoint-only crash/ANR reporting)
- Single-instance lock: `test_log/default_behavior.lock`

## Kids prime priority

`KIDS_CHANNEL_NUMBERS` + clock minute ≥ 50 → force kids slots while check 6 pending (`_should_force_kids_prime_priority` / `_skip_non_kids_for_check6`).

## Run entry

```powershell
.\stb-rpa\my.yoon_test\run_default_behavior.ps1 -StbDevices "192.168.x.x"
# UTF-8 console; kills prior "Default behavior.py" python processes; sets schedule defaults
```

Direct:

```powershell
$env:PYTHONUTF8=1
python -u "stb-rpa\my.yoon_test\Default behavior.py"
```
