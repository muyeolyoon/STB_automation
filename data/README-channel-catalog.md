# LGU 채널 카탈로그 (`lgu_channel_catalog.json`)

`receive cue` / `ProgramProviderChannel` 로그의 **`id`는 STB 채널 번호(311, 322…)가 아니라 이 카탈로그의 채널 ID**입니다.

`register cue` 의 **`ppId`도 동일한 채널 ID**입니다. A 채널 시청 중에도 B·C 채널의 register cue 가 logcat 에 같이 찍힐 수 있으므로, 스크립트는 **gspread 편성 채널명 ↔ 카탈로그 title 매칭**으로 기대 id 를 정한 뒤, **ppId/id 가 일치할 때만** cue 로 인정합니다.

예: `ProgramProviderChannel(id=998, … kid=true)` → id `998` = **캐리TV** (`forKids: true`)

## 파일 갱신

1. API 페이지 JSON을 `data/_import/catalog_paste_raw.txt`에 이어 붙여 저장 (루트 객체 `{ "totalRows": …, "data": […] }` 여러 개).
2. 병합:
   ```bash
   python stb-rpa/tools/merge_lgu_channel_catalog.py stb-rpa/data/_import/catalog_paste_raw.txt
   ```
3. `totalRows`와 `data` 길이가 API 전체 건수(377)와 맞는지 확인.

편성 시트 채널명이 카탈로그 `title`과 다르면 `channel_name_aliases.json`에 매핑을 추가하세요.  
`resolve_expected_catalog_ids()`는 title/tags와 함께 alias 후보도 조회합니다.
