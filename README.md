# STB_automation

KT / SKB / U+ STB QA 자동화 (Python + adb).

이 레포는 예전 모노레포의 `stb-rpa/` 내용을 **루트**에 둔 것입니다.

## 누가 무엇을 하나

- **사람:** 방향만 제시 (무엇을 검증할지, 어떤 플랫폼/플래그, PASS 기준의 의도).
- **에이전트:** 구현·수정·실행 스크립트 정리. 상세 규칙은 [`.cursor/skills/default-behavior-engineer/`](.cursor/skills/default-behavior-engineer/) 와 [`AGENTS.md`](AGENTS.md).

## 레이아웃 (에이전트용 지도)

| 경로 | 역할 |
|------|------|
| `my.yoon_test/Default behavior.py` | 메인 오케스트레이터 (체크리스트 1–6 + 편성 모니터). 파일명에 **공백** 있음. |
| `my.yoon_test/run_default_behavior.ps1` | 실행 런처 / env |
| `component/` | 공용 라이브러리 (`schedule_loader`, `platform_config`, `channel_mapping`, …) |
| `platforms/` | 플랫폼 차이만 — `channel_map_uplus.json` / `channel_map_skb.json` / `channel_map_kt.json` |
| `data/` | LGU 채널 카탈로그 등 |
| `skb/` `lg_uplus/` `KT/` `airtel/` | 플랫폼·레거시 스크립트 (당분간 **폴더 유지**, 로직은 점진히 `component/`+`platforms/`로) |
| `.cursor/skills/` | Cursor 에이전트 스킬 |

## 플랫폼 선택

```powershell
$env:STB_PLATFORM = "uplus"   # skb | kt
```

- **편성표:** KT는 전용 블록 없음 → `channel_map_kt.json`의 `schedule_section`이 U+ 또는 SKB를 빌려 씀.
- **채널 번호:** 플랫폼 JSON의 `channels`만 사용. KT 공식 출처는 [tv.kt.com 채널 편성표](https://tv.kt.com/tv/channel/pChInfo.asp) (`official_scrape_kt.json`).

자세한 스키마: [`platforms/README.md`](platforms/README.md).

## 빠른 실행

```powershell
cd <this-repo>
.\my.yoon_test\run_default_behavior.ps1
```

시크릿은 커밋하지 않음. `service_account.json`은 로컬에만 두고 경로를 맞춤.  
외부 메신저 알림은 사용하지 않음(콘솔/`[notify]` print만).

## 구조 정리 방향 (아직 안 함 / 의도적)

1. ~~플랫폼 채널맵 분리~~ → `platforms/` 완료  
2. 레거시 `skb/`·`lg_uplus/` 복제 스크립트 통합 → 나중에 (B)  
3. `my.yoon_test` 이름 정리 → 경로 의존 많음, 안정화 후
