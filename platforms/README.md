# STB platforms (SKB / U+ / KT)

채널 번호·편성 시트 section 등 **플랫폼별 차이만** 여기 JSON에 둡니다.  
동작 로직은 `component/` 및 기존 `skb/` · `lg_uplus/` · `KT/` 스크립트를 그대로 씁니다.

## 파일

| File | Platform | Notes |
|------|----------|--------|
| `channel_map_uplus.json` | U+ (LGU) | 채널명→번호. 기본값. ART 편성. |
| `channel_map_skb.json` | SKB | BigAD 편성. `channels`는 채울 것. |
| `channel_map_kt.json` | KT (지니TV) | **자체 편성표 없음.** 편성은 U+(기본) 또는 SKB 차용. 번호만 KT. |
| `official_scrape_kt.json` | (참고) | [tv.kt.com 채널 편성표](https://tv.kt.com/tv/channel/pChInfo.asp) 스크랩 원본 |

스키마 (`channel_map_*.json`):

```json
{
  "id": "uplus",
  "schedule_section": "uplus",
  "channels": { "ENA": 1, "tvN": 3 }
}
```

- `id`: 플랫폼 식별 (`skb` / `uplus` / `kt`)
- `schedule_section`: `schedule_loader`에 넘길 값 — **반드시 `skb` 또는 `uplus`**
- `channels`: 채널명 → **그 플랫폼 STB 채널 번호**

## KT

KT는 모니터링 시트에 전용 블록이 없습니다.

1. 편성(시간·채널명) → `schedule_section`으로 **U+(ART) 또는 SKB(BigAD)** 를 따름  
   - 기본: `"schedule_section": "uplus"`  
   - SKB 편성: `"schedule_section": "skb"`
2. 채널 전환 번호 → `channel_map_kt.json`의 `channels` (`STB_PLATFORM=kt`)

`channel_map_kt.json` 출처: KT 공식 [채널 편성표](https://tv.kt.com/tv/channel/pChInfo.asp) (지니 TV / 전체).  
원본: `official_scrape_kt.json`. 요금제·지역에 따라 일부 번호가 다를 수 있습니다.

## 플랫폼 선택

```text
STB_PLATFORM=uplus
STB_PLATFORM=skb
STB_PLATFORM=kt
```

`PLATFORM` 도 동일. 미설정 시 **uplus**.  
별칭: `u+`, `lgu`, `lg_uplus`, `art` → uplus / `bigad` → skb.

```python
from component.channel_mapping import get_channel_number
from component.platform_config import resolve_platform, get_schedule_section

get_channel_number("ENA")                 # env 기본 플랫폼
get_channel_number("ENA", platform="kt")  # KT 번호
get_schedule_section("kt")                # → "uplus"
```

## 기존 폴더

`skb/` / `lg_uplus/` / `KT/` 실행 경로는 유지합니다.  
채널 맵만 `platforms/channel_map_*.json`에서 관리합니다.
