# AGENTS.md — how to work in this repo

Human gives **direction only** (intent, platform, what “good” means).  
You implement. Prefer clarity for the next agent session over clever refactors.

## First reads

1. [`README.md`](README.md) — map + platforms  
2. Skill: [`.cursor/skills/default-behavior-engineer/SKILL.md`](.cursor/skills/default-behavior-engineer/SKILL.md) (or `SKILL.ko.md`)  
3. Only then touch `my.yoon_test/Default behavior.py`

## Path rules

- Repo root **is** the old `stb-rpa/` tree. Do **not** invent a nested `stb-rpa/` folder.  
- Main file: `my.yoon_test/Default behavior.py` (space in name).  
- Shared code: `component/`. Channel maps: `platforms/channel_map_*.json`.

## Defaults when human is vague

| Topic | Default |
|-------|---------|
| Platform | `STB_PLATFORM=uplus` unless they say SKB/KT |
| KT schedule | Borrow U+ (`schedule_section` in `channel_map_kt.json`); numbers from KT map |
| Edits | Surgical; extract to `component/` instead of growing the god-file |
| Checklist PASS/FAIL | Do not invent — ask if ambiguous |
| Commit/push | Only when human asks |
| Secrets | Never commit tokens / `service_account.json` |

## Do / don't

**Do:** use `platform_config` / `get_channel_number`, keep `skb/`·`lg_uplus/`·`KT/` folders until asked to merge, match existing helper names (`execute_test_*`, `term_print`, …).

**Don't:** drive-by reformat, clear logcat by default, merge kids check 6 into check 2, refactor unrelated cue scripts without a request.

## Verify before saying done

```powershell
python -m py_compile "my.yoon_test/Default behavior.py"
python -c "from component.channel_mapping import get_channel_number; assert get_channel_number('ENA')==1"
```
