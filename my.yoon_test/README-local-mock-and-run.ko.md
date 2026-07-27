# Default behavior + 로컬 mock 실행 가이드

> **Cursor에게:** 이 문서를 순서대로 따르세요.  
> 사용자 메시지 예: `이 파일 보고 내 STB에서 Default behavior 돌릴 수 있게 셋업하고 실행해줘`  
> STB IP·PC IP·모델명(UHD3 등)은 사용자에게 확인하세요. 추측으로 운영 auth/비밀값을 커밋하지 마세요.

대상 스크립트:

| 구분 | 경로 |
|------|------|
| 메인 | `stb-rpa/my.yoon_test/Default behavior.py` |
| 실행 | `stb-rpa/my.yoon_test/run_default_behavior.ps1` |
| auth 자동 패치 | `stb-rpa/component/auth_mock_patch.py` |

OS 전제: **Windows** + `adb` + **Python 3** + (구글 안정 fill용) **Apache** `C:\Apache24\htdocs`.

---

## 0. 한 줄 요약

- **운영 API만** 쓰면 내부 광고(체크 2·4·5·6)는 편하지만, **구글 광고(체크 3)는 fill이 잘 안 나오는 경우가 많음**.
- 그래서 랩에서는 STB의 API를 **내 PC Apache(mock)** 로 돌리는 경우가 많음.
- mock을 쓰면 **`auth`는 기기마다** 맞춰야 하고, **구글용 `ads`/`google_test`는 샘플 공유**로 충분함.
- 구글 드라이브는 **파일 배포용**이지, STB가 직접 호출하는 URL로 쓰면 안 됨 → 받아서 PC Apache에 올려야 함.

---

## 1. 왜 로컬 mock이 필요한가

| 방식 | 장점 | 단점 |
|------|------|------|
| 전부 운영 (`art-api.anypoint.tv`) | auth/ads 파일 불필요 | 구글(3) 응답(fill) 불안정 |
| 로컬 Apache mock | 구글 샘플 VAST로 체크 3 안정 | PC에 htdocs 필요, **auth 기기별 패치** |

실무 타협(이미 쓰는 형태):

- `api.endpoint` → `http://{PC_IP}/{모델}` (예: `http://192.168.10.150/UHD3`) → **auth 등**
- auth JSON 안 `endpoints.requestAds` → `http://{PC_IP}/google_test` → **구글만 샘플**

### Agent 명령으로 `requestAds`만 바꿀 수 있나?

**불가.**  
`am broadcast … CHANGE_API_ENDPOINT` 는 **`api.endpoint` 하나**만 바꿈.  
`CHANGE_REQUEST_ADS` 같은 건 Agent 앱에 없음 → QA 스크립트가 이름을 만들어도 STB가 무시함.  
`requestAds` 분리는 **auth 응답의 `endpoints.requestAds` 필드**로 함.

### 구글 드라이브에 올려두면?

- 드라이브 = **zip/템플릿 공유**만 OK.
- STB가 `http://drive.google.com/...` 를 API로 치면 **실패** (HTML/로그인/리다이렉트).
- 다른 사람: 드라이브에서 **다운 → 자기 PC(또는 공용 랩 PC) Apache `htdocs`에 풀기**.

---

## 2. 사전 준비 (체크리스트)

Cursor는 아래를 확인하고, 없으면 사용자에게 물어본 뒤 설치/설정을 진행한다.

1. [ ] 이 repo 클론됨 (`anypointmedia-QA`)
2. [ ] `adb` PATH에 있음 → `adb version`
3. [ ] Python 3 → `python --version`
4. [ ] STB와 PC가 **같은 LAN**, STB ADB 연결 가능  
   - 예: `adb connect 192.168.10.10:5555` → `adb devices` 에 `device`
5. [ ] (구글/mock 쓸 때) Apache 기동, DocumentRoot ≈ `C:\Apache24\htdocs`
6. [ ] PC의 LAN IP — **보통 자동** (`ipconfig` 감지). 강제할 때만 `STB_LOCAL_API_HOST`
7. [ ] 셋탑 모델 폴더명 (deviceTypeId 매핑)  
   - 24 → `UHD3`, 20 → `UHD4K` 등 (`auth_mock_patch.py` 의 `DEVICE_TYPE_TO_MODEL`)
8. [ ] mock 템플릿 폴더가 htdocs에 있음  
   - `C:\Apache24\htdocs\UHD3\` (또는 해당 모델)
   - `C:\Apache24\htdocs\google_test\` (구글 requestAds 샘플)
9. [ ] 편성표 Drive 접근 (기본 시트 ID는 `run_default_behavior.ps1` 에 있음). 서비스 계정/권한은 랩 관례 따름.

템플릿이 없으면: 동료 PC/`htdocs` 백업/구글 드라이브 **zip을 받아** `C:\Apache24\htdocs\` 아래에 푼다.  
**auth 실기기 identity·토큰이 들어 있는 파일을 git에 커밋하지 말 것.**

---

## 3. auth / ads 역할

| 파일·경로 | 역할 | 공유 |
|-----------|------|------|
| `{모델}/v3/device/auth` | 기기 `deviceId` / `deviceTypeId` / `uuid` / (토큰) + `endpoints.*` | **기기마다 다름** → 패치 필요 |
| `{모델}` 아래 기타·ads | 내부 API mock | 샘플 공유 가능 |
| `google_test` | `endpoints.requestAds` 대상 (구글) | **샘플 공유 OK** |

로컬 `api.endpoint`를 쓰는 한, **ads만 샘플로 두고 auth를 안 맞추면** 앞단에서 깨질 수 있음.  
→ 최소: **auth 패치 1회 + google_test 샘플**.

---

## 4. auth 자동 패치 (다른 사람도 이걸로)

운영에서 잠깐 identity를 읽어 로컬 auth에 넣는다.

```powershell
cd <repo root>   # 예: D:\python_test\anypointmedia-QA

# STB 연결
adb connect 192.168.10.10:5555
adb devices

# htdocs (필요 시). PC IP는 미설정 시 ipconfig 자동감지
$env:STB_HTDOCS_ROOT = "C:\Apache24\htdocs"
# $env:STB_LOCAL_API_HOST = "192.168.10.150"  # 강제할 때만

python stb-rpa/component/auth_mock_patch.py --device 192.168.10.10:5555
```

대략 하는 일:

1. (필요 시) U+ 운영 `https://art-api.anypoint.tv` 로 endpoint 맞춘 뒤 identity 수집  
2. logcat에서 `deviceId` / `deviceTypeId` / `uuid` 추출  
3. `C:\Apache24\htdocs\{모델}\v3\device\auth` 패치  
4. `endpoints` 중 다수는 `http://{PC}/{모델}` , **`requestAds`는 기존 `google_test` 유지**(없으면 base)

유용한 옵션:

| 옵션 | 의미 |
|------|------|
| `--no-reboot` | 재부팅 없이 수집 |
| `--keep-endpoints` | auth 안 URL 안 건드림 |
| `--model UHD3` | 모델 폴더 강제 |
| `--local-host 192.168.x.x` | endpoints에 넣을 PC IP |

패치 후 auth 확인 예:

```powershell
python -c "import json; d=json.load(open(r'C:\Apache24\htdocs\UHD3\v3\device\auth',encoding='utf-8')); print({k:d.get(k) for k in ['deviceId','deviceTypeId','uuid']}); print('requestAds', (d.get('endpoints') or {}).get('requestAds'))"
```

브라우저/PC에서 `http://{PC_IP}/UHD3/...` 와 `http://{PC_IP}/google_test/...` 가 열리는지 확인.

---

## 5. STB에 로컬 endpoint 적용

Default behavior 실행 시:

| 환경변수 | 의미 |
|----------|------|
| `APPLY_LOCAL_API_ENDPOINT=1` | 로컬 mock 적용 |
| `STB_LOCAL_API_HOST` | PC IP. **미설정 시 `ipconfig`로 자동 감지**(STB와 같은 `/24` 선호) |
| `STB_LOCAL_API_MODEL` | `UHD3` 등 |
| `STB_API_ENDPOINT` | 전체 URL 한 방에 (예: `http://192.168.10.150/UHD3`) — 넣으면 호스트 자동감지 안 씀 |
| `APPLY_LOCAL_API_ENDPOINT=0` | 질문/적용 **스킵** (이미 맞춰 둔 경우) |

Agent 쪽 실제 명령은 대략:

```text
am broadcast -a tv.anypoint.agent.app.CHANGE_TEST_PROPERTY
  --es change.command CHANGE_API_ENDPOINT
  --es api.endpoint http://{PC}/{모델}
```

변경 후 **재부팅**이 권장되는 경우가 많음 (`SKIP_REBOOT=0`).

---

## 6. Default behavior 실행

### 6-1. 로컬 mock + 재부팅부터 (권장 풀사이클)

```powershell
cd <repo root>

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:STB_DEVICE_IPS = "192.168.10.10:5555"          # ← 대상 STB
$env:SKIP_REBOOT = "0"                               # 재부팅 + 버전(체크1)
$env:APPLY_LOCAL_API_ENDPOINT = "1"
# PC IP는 자동감지. 강제: $env:STB_LOCAL_API_HOST = "192.168.10.150"
$env:STB_LOCAL_API_MODEL = "UHD3"                    # ← 모델
# 또는: $env:STB_API_ENDPOINT = "http://192.168.10.150/UHD3"

powershell -NoProfile -ExecutionPolicy Bypass `
  -File "stb-rpa\my.yoon_test\run_default_behavior.ps1" `
  -StbDevices "192.168.10.10:5555" `
  -SkipReboot "0"
```

### 6-2. 이미 endpoint 맞춰 둠 + 재부팅 생략

```powershell
$env:SKIP_REBOOT = "1"
$env:APPLY_LOCAL_API_ENDPOINT = "0"

powershell -NoProfile -ExecutionPolicy Bypass `
  -File "stb-rpa\my.yoon_test\run_default_behavior.ps1" `
  -StbDevices "192.168.10.10:5555" `
  -SkipReboot "1"
```

`run_default_behavior.ps1` 은 기존 `Default behavior.py` 프로세스를 죽이고 하나만 기동한다.

로그:

- 터미널 미러: `test_log/behavior_run_terminal.log`
- 기기 logcat: `test_log/*_behavior_run*.log`

---

## 7. 자주 쓰는 환경변수

| Env | 기본/예 | 의미 |
|-----|---------|------|
| `STB_DEVICE_IPS` | `192.168.x.x:5555` | 대상 STB |
| `SKIP_REBOOT` | `1` (ps1 기본) | `0`이면 재부팅+버전 확인 |
| `APPLY_LOCAL_API_ENDPOINT` | unset=질문 | `1` 적용 / `0` 스킵 |
| `STB_LOCAL_API_HOST` | ipconfig 자동 | PC mock IP. 강제할 때만 지정 |
| `STB_LOCAL_API_MODEL` | `UHD3` 등 | 로컬 endpoint 모델 경로 |
| `STB_ESCAPE_CHANNEL` | `3` | 이탈·복귀 채널 |
| `SKIP_GOOGLE_CHECK` | unset | `1`이면 체크 3 생략 |
| `CHECKLIST_ONLY` | unset | `1`이면 체크리스트 후 편성 모니터 생략 |
| `KIDS_WAIT_FILLIN` | `1`(기본) | 체크 6만 남았을 때 키즈 대기 중 일반 편성 중간 재확인 |
| `KIDS_WAIT_FILLIN_LEAD_SEC` | `120` | 키즈 전환 이 시간 전부터는 fill-in 중단 |
| `GOOGLE_CHAT_SPACE` | ps1에 기본값 | `0`이면 Chat 전송 끔 |

---

## 8. 체크리스트가 뭐 하는지 (아주 짧게)

| # | 내용 |
|---|------|
| 1 | 재부팅 후 Firmware / SDK / Agent 버전 (`SKIP_REBOOT=1`이면 스킵) |
| 2 | 일반 채널 내부 광고 playTime + impression API |
| 3 | 구글 A/B/C (Quartile·이탈·스킵) — **mock requestAds 권장** |
| 4 | 재생 중 이탈 후 playTime 일치 |
| 5 | play 전 이탈 → 미재생 |
| 6 | 키즈 워터마크 logcat + UI「광고 방송」OCR |

참고: logcat에 `not yet ready to play target ad` 가 보이면 **AD_SYNC**(쿨다운 있음) 후 해당 체크는 **보류·시도 미차감**으로 다음 편성 재시도.  
AD_SYNC 결과가 `target ads sync finished: ready=false, status=ERROR` 이면 **터미널 ⚠ + Google Chat 알림 + AD_SYNC 재전송**(알림 쿨다운 기본 120초, 재전송은 AD_SYNC 90초 쿨다운 존중).

Chat에 올리는 키즈 UI 캡처는 **전체화면** screencap(우측 상단만 확대한 `_chat.png` 아님). OCR 판정은 여전히 상단·우측 crop 사용.

상세: `.cursor/skills/default-behavior-engineer/reference.ko.md`

---

## 9. 다른 사람 / 다른 PC 온보딩

1. repo 받기  
2. 드라이브(또는 동료)에서 **htdocs 템플릿 zip** 받아 `C:\Apache24\htdocs\` 에 풀기 (`UHD3`, `google_test` 등)  
3. Apache 실행, 방화벽에서 STB→PC:80 허용  
4. `auth_mock_patch.py` 로 **그 STB auth** 패치  
5. §6 명령으로 Default behavior 실행  

공용 랩 PC Apache를 쓰면: 다들 `STB_API_ENDPOINT=http://{랩PC}/{모델}` 만 맞추고, auth는 랩 PC htdocs에서 기기별로 관리.

---

## 10. 문제 생기면

| 증상 | 볼 것 |
|------|--------|
| `adb devices` 비어 있음 | USB/네트워크 ADB, `adb connect`, 같은 대역 |
| auth 401 / 기기 인식 실패 | auth의 deviceId·uuid가 이 STB 것인지, `auth_mock_patch` 재실행 |
| 구글 STARTED 없음 | `endpoints.requestAds` → `google_test` 인지, Apache에서 샘플 응답 여부, 운영 fill 여부 |
| `not yet ready…` | AD_SYNC·쿨다운 로그, 다음 편성 보류가 정상인지 |
| 채널 전환 실패 | 유료가입 화면, escape 채널, 카탈로그 |
| 한글 로그 깨짐 | `run_default_behavior.ps1` 사용, `PYTHONUTF8=1` |
| 잠금/중복 실행 | `test_log/default_behavior.lock`, 기존 python `Default behavior.py` 종료 |

---

## 11. Cursor 실행 프롬프트 예시 (복붙)

```text
stb-rpa/my.yoon_test/README-local-mock-and-run.ko.md 를 읽고 따라해줘.

STB: 192.168.10.10:5555
내 PC IP: 192.168.10.150
모델: UHD3
목표: auth_mock_patch로 auth 맞춘 뒤, 로컬 endpoint + 재부팅(SKIP_REBOOT=0)으로
run_default_behavior.ps1 실행.
비밀/실기기 auth는 커밋하지 마.
막히면 추측하지 말고 물어봐.
```

값만 바꿔서 사용하면 된다.
