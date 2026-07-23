## STB 리모컨 앱 (ADB)

`adb shell input keyevent` 기반의 간단한 **GUI 리모컨**입니다. 여러 대 디바이스에 동시에 keyevent를 보낼 수 있습니다.

### 준비

- PC에 `adb` 설치 및 PATH 등록 (Android SDK Platform Tools)
- STB/Android TV에서 ADB 연결 가능 상태(USB 또는 무선 ADB)

### 실행

**Python으로 실행** (프로젝트 루트에서):

```bash
python stb-rpa/remote_control_app.py
```

**실행 파일(.exe)로 실행**

- 빌드된 exe: `stb-rpa/dist/STB_Remote_Control.exe` 를 더블클릭하거나 명령줄에서 실행하면 됩니다.
- exe는 **adb**를 PATH에서 찾습니다. 사용하는 PC에 adb가 설치되어 있고 PATH에 있어야 합니다.

**실행 파일 만들기 (빌드)**

1. PyInstaller 설치: `pip install pyinstaller`
2. `stb-rpa` 폴더로 이동 후 아래 중 하나로 빌드:
   - **배치 파일**: `build_remote_control_exe.bat` 실행
   - **직접 명령**: `python -m PyInstaller --clean remote_control_app.spec`
3. 생성 위치: `stb-rpa/dist/STB_Remote_Control.exe` (단일 파일, 콘솔 창 없음)

### 사용 방법

- **디바이스 추가**: IP만 입력 시 `:5555`가 자동으로 붙습니다. 예: `192.168.10.8` → `192.168.10.8:5555`. IP:PORT 또는 USB serial 입력 후 `추가`
- **선택 연결**: 선택된 디바이스(선택이 없으면 전체)에 `adb connect` 수행
- **버튼 조작**: POWER/HOME/BACK/MENU, DPAD, VOL, 채널번호 전송
- **Custom keycode**: 임의 keycode를 직접 입력해서 전송

### 참고 keycode

- `3`: HOME
- `4`: BACK
- `23`: DPAD_CENTER(OK)
- `24/25`: VOLUME UP/DOWN
- `26`: POWER

