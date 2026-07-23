import subprocess
import time
import sys

# 색상 및 스타일 정의
RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
BLUE = "\033[34m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
WHITE_ON_BLUE = "\033[44;97m"  # 파란 배경에 흰색 글자
BOLD = "\033[1m"
RESET = "\033[0m"

class FlowerLogAnalyzer:
    def __init__(self):
        self.target_pkg = "tv.anypoint.flower.sdk.qa.exoplayer2"
        self.last_position = -1
        self.last_pos_time = time.time()

        # AD_FLOW 이후 macro prepare/ready 최초 1회만 로깅
        self.ad_flow_detected = False
        self.macro_logged = False

        # 광고 정보 저장
        self.ad_duration = None
        self.ad_id = None
        self.ad_start_time = None

        # 트래킹 이벤트 정의
        self.tracking_events = {
            'IMPRESSION': 'Tracking event: impression',
            'START': 'Tracking event: start',
            'FIRST_Q': 'Tracking event: firstQuartile',
            'MIDPOINT': 'Tracking event: midpoint',
            'THIRD_Q': 'Tracking event: thirdQuartile',
            'COMPLETE': 'Tracking event: complete'
        }

    def extract_value(self, line, key):
        """로그 라인에서 key=value 형태의 값을 추출"""
        try:
            start = line.find(f'{key}=')
            if start == -1:
                return None
            start += len(key) + 1
            end = line.find(',', start)
            if end == -1:
                end = line.find(' ', start)
            if end == -1:
                end = len(line)
            return line[start:end].strip()
        except:
            return None

    def process_line(self, line):
        clean_line = line.decode('utf-8', errors='ignore').strip()
        if not clean_line: return

        # WindowManager 노이즈 제거
        if 'WindowManager' in clean_line and 'screenshot' in clean_line.lower():
            return

        upper_line = clean_line.upper()

        # VAST XML 파싱
        if '<Ad id=' in clean_line:
            import re
            ad_match = re.search(r'<Ad id="(\d+)"', clean_line)
            if ad_match:
                ad_id = ad_match.group(1)
                # 같은 로그 그룹에서 duration 찾기 (단순히 다음 라인 가정, 실제로는 로그 구조에 따라)
                # 여기서는 ad_id만 저장하고, duration은 별도 라인에서 찾음
                self.ad_id = ad_id
                # print(f"{CYAN}DEBUG VAST FOUND: Ad ID={ad_id} in {clean_line}{RESET}")
        elif '<Duration>' in clean_line:
            import re
            duration_match = re.search(r'<Duration>00:00:(\d+)\.000</Duration>', clean_line)
            if duration_match:
                duration = duration_match.group(1)
                self.ad_duration = duration
                # print(f"{CYAN}DEBUG DURATION FOUND: {duration}초 in {clean_line}{RESET}")
                # PLAY_LIST 출력
                if self.ad_id:
                    print(f"{BLUE}[PLAY_LIST] adId: {self.ad_id}, duration: {int(duration) * 1000}ms{RESET}")
                    
        # [A] 매크로 치환 분석 (AD_FLOW 이후 최초 1회만)
        if self.ad_flow_detected and not self.macro_logged:
            if 'BEFORE APPLYING MACRO' in upper_line:
                print(f"\n{WHITE_ON_BLUE} [MACRO PREPARE] {RESET} {clean_line}")
                return
            elif 'AFTER APPLYING MACRO' in upper_line:
                print(f"{GREEN}{BOLD} [MACRO READY]   {RESET} {clean_line}")
                self.macro_logged = True
                return

        # [B] 광고 흐름 단계별 감지 [cite: 1233, 1236, 1445]
        if 'REQUESTING LINEAR TV ADS' in upper_line:
            print(f"{MAGENTA}{BOLD}[AD_FLOW] 광고 요청 시작{RESET} {clean_line}")
            self.ad_flow_detected = True
            self.macro_logged = False  # 리셋
            # duration은 VAST 응답에서 추출
        elif 'EXECUTETAG' in upper_line and 'SUCCESSFUL' in upper_line:
            print(f"{MAGENTA}[AD_FLOW] VAST 응답 수신 성공{RESET} {clean_line}")
            # 각 광고의 ad_id와 duration 추출 및 출력
            import re
            ad_matches = re.findall(r'<Ad id="(\d+)"', clean_line)
            duration_matches = re.findall(r'<Duration>00:00:(\d+)\.000</Duration>', clean_line)
            if ad_matches and duration_matches:
                print(f"{BLUE}[PLAY_LIST] 광고 플레이리스트:{RESET}")
                for i, (ad_id, duration) in enumerate(zip(ad_matches, duration_matches)):
                    print(f"{BLUE}  {i+1}번 adId: {ad_id}, duration: {int(duration) * 1000}ms{RESET}")
                    if i == 0:  # 첫 번째 광고 정보만 저장 (기존 로직 유지)
                        self.ad_id = ad_id
                        self.ad_duration = duration
            print(f"{YELLOW}DEBUG: Found {len(ad_matches)} ads, {len(duration_matches)} durations{RESET}")
        elif 'PARSE COMPLETE' in upper_line:
            print(f"{MAGENTA}[AD_FLOW] VAST 파싱 완료{RESET} {clean_line}")
            # VAST XML 파싱 시도
            import re
            ad_matches = re.findall(r'<Ad id="(\d+)"', clean_line)
            duration_matches = re.findall(r'<Duration>00:00:(\d+)\.000</Duration>', clean_line)
            if ad_matches and duration_matches:
                print(f"{BLUE}[PLAY_LIST] 광고 플레이리스트:{RESET}")
                for i, (ad_id, duration) in enumerate(zip(ad_matches, duration_matches)):
                    print(f"{BLUE}  {i+1}번 adId: {ad_id}, duration: {int(duration) * 1000}ms{RESET}")
                    if i == 0:  # 첫 번째 광고 정보만 저장
                        self.ad_id = ad_id
                        self.ad_duration = duration

        # [C] 버퍼링/정체 감지
        if 'WATCHLINEARTVADPROGRESS CHECKING' in upper_line:
            try:
                pos_part = clean_line.split('position=')[1]
                current_position = int(pos_part.split(',')[0].split(')')[0].strip())
                current_time = time.time()
                if current_position == -1:
                    self.last_pos_time = current_time 
                    return
                if current_position == self.last_position:
                    stall_duration = current_time - self.last_pos_time
                    if stall_duration > 2.0:
                        print(f"{RED}{BOLD}[버퍼링 발생] {stall_duration:.1f}초간 화면 멈춤 (Pos: {current_position}){RESET}")
                else:
                    self.last_position = current_position
                    self.last_pos_time = current_time
            except: pass

        # [D] 광고 트래킹 이벤트 및 비콘 전송 [cite: 1643, 1699]
        for event_name, keyword in self.tracking_events.items():
            if keyword in clean_line:
                print(f"{BOLD}{YELLOW}[AD_{event_name}]{RESET} {clean_line}")
                # START 시 시작 시간 기록
                if event_name == 'START':
                    self.ad_start_time = time.time()
                # AD_COMPLETE 시 완료 메시지
                elif event_name == 'COMPLETE':
                    print(f"{GREEN}  └─ 광고 완료{RESET}")
                    # 리셋
                    self.ad_duration = None
                    self.ad_id = None
                    self.ad_start_time = None
                return

        # [E] 타겟 SDK 에러 로그
        if self.target_pkg.upper() in upper_line:
            if 'ERROR' in upper_line or 'FAIL' in upper_line:
                print(f"{RED}[SDK ERROR] {clean_line}{RESET}")

        # [F] adBreak count 로그
        if 'adbreak count:' in clean_line.lower():
            print(f"{CYAN}[AD_BREAK_COUNT] {clean_line}{RESET}")

        # [G] 재생 중단 관련 이슈 모니터링
        if 'Updating duration from' in clean_line:
            import re
            match = re.search(r'Updating duration from (\d+) to (-?\d+)', clean_line)
            if match:
                from_val, to_val = match.groups()
                if int(to_val) < 0:
                    print(f"{RED}{BOLD}[⚠️ DURATION ERROR] 영상 길이가 음수로 변함: from {from_val} to {to_val} {RESET}")
        elif 'Adjusting offset. error:' in clean_line:
            print(f"{YELLOW}[OFFSET ERROR] 오프셋 조정 실패: {clean_line}{RESET}")
        elif 'Played past the last ad section' in clean_line:
            print(f"{MAGENTA}[AD SECTION END] 광고 섹션 종료: {clean_line}{RESET}")

    def select_device(self):
        """연결된 ADB 디바이스 중 하나를 선택"""
        try:
            result = subprocess.run(["adb", "devices"], capture_output=True, text=True)
            lines = result.stdout.strip().split('\n')[1:]  # 첫 줄은 "List of devices attached" 제외
            devices = [line.split('\t')[0] for line in lines if line.strip() and 'device' in line]

            if not devices:
                print(f"{RED}연결된 ADB 디바이스가 없습니다.{RESET}")
                return None
            elif len(devices) == 1:
                print(f"{GREEN}연결된 디바이스: {devices[0]}{RESET}")
                return devices[0]
            else:
                print(f"{BLUE}연결된 디바이스 목록:{RESET}")
                for i, device in enumerate(devices):
                    print(f"{i+1}. {device}")
                while True:
                    try:
                        choice = int(input("디바이스를 선택하세요 (번호): ")) - 1
                        if 0 <= choice < len(devices):
                            return devices[choice]
                        else:
                            print("잘못된 번호입니다.")
                    except ValueError:
                        print("숫자를 입력하세요.")
        except FileNotFoundError:
            print(f"{RED}ADB가 설치되어 있지 않습니다.{RESET}")
            return None

    def run(self):
        print(f"{BLUE}=========================={RESET}")
        print(f"{BLUE}  Flower SDK 통합 분석기{RESET}")
        print(f"{BLUE}=========================={RESET}")

        device = self.select_device()
        if not device:
            return

        print(f"{GREEN}선택된 디바이스: {device}{RESET}")
        subprocess.run(["adb", "-s", device, "logcat", "-c"])
        process = subprocess.Popen(["adb", "-s", device, "logcat", "-v", "time"], stdout=subprocess.PIPE)
        try:
            for line in iter(process.stdout.readline, b''):
                self.process_line(line)
        except KeyboardInterrupt:
            process.terminate()

if __name__ == "__main__":
    analyzer = FlowerLogAnalyzer()
    analyzer.run()