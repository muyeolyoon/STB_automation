"""구글(IMA) 광고 logcat 이벤트 추적 — google adEvent type / tracking beacon."""

import re
import time

GOOGLE_AD_EVENT_RE = re.compile(r"google adEvent type:\s*(\w+)", re.I)
GOOGLE_AD_HINT_RE = re.compile(
    r"doubleclick\.net|googleadservices\.com|GoogleAdsLoader|GoogleAdPlayer",
    re.I,
)
GOOGLE_TRACKING_RE = re.compile(r"event/tracking\?event=(\w+)", re.I)
STOP_TRACKING_RE = re.compile(r"GoogleAdPlayer\.stopTracking", re.I)

QUARTILE_EVENTS = frozenset({"FIRST_QUARTILE", "MIDPOINT", "THIRD_QUARTILE"})
QUARTILE_TRACKING_EVENTS = frozenset({"firstquartile", "midpoint", "thirdquartile"})


# 체크 3 실시간 터미널 출력 대상 (전체 logcat 줄)
GOOGLE_AD_LIVE_LOG_RES = (
    GOOGLE_AD_EVENT_RE,
    GOOGLE_TRACKING_RE,
    STOP_TRACKING_RE,
)


def is_google_ad_live_log_line(line: str) -> bool:
    """google adEvent / tracking / stopTracking — 체크 3 실시간 출력용."""
    return any(pat.search(line) for pat in GOOGLE_AD_LIVE_LOG_RES)


def is_google_ad_term_log_line(line: str, sub: str | None = None) -> bool:
    """체크 3 / 모니터링 터미널 출력 필터."""
    if STOP_TRACKING_RE.search(line):
        return sub == "leave_during"

    m = GOOGLE_AD_EVENT_RE.search(line)
    if m:
        name = m.group(1).upper()
        if name in QUARTILE_EVENTS:
            return True
        if sub == "full_play" and name in ("COMPLETED", "ALL_ADS_COMPLETED"):
            return True
        if sub == "skip_ok" and name in ("SKIPPED", "SKIPPABLE_STATE_CHANGED"):
            return True
        if sub == "monitor" and name in (
            "STARTED",
            "COMPLETED",
            "ALL_ADS_COMPLETED",
            "SKIPPED",
            "SKIPPABLE_STATE_CHANGED",
        ):
            return True
        return False

    tm = GOOGLE_TRACKING_RE.search(line)
    if tm:
        return tm.group(1).lower() in QUARTILE_TRACKING_EVENTS

    return False


class GoogleAdEventTracker:
    def __init__(self):
        self.ad_events = []
        self.tracking_events = []
        self.stop_tracking_at = None
        self.google_hint_seen = False
        self._leave_mark = None

    def process_line(self, line: str):
        if GOOGLE_AD_HINT_RE.search(line):
            self.google_hint_seen = True
        m = GOOGLE_AD_EVENT_RE.search(line)
        if m:
            name = m.group(1).upper()
            self.ad_events.append((time.time(), name, line.strip()[:200]))
        tm = GOOGLE_TRACKING_RE.search(line)
        if tm:
            self.tracking_events.append(
                (time.time(), tm.group(1).lower(), line.strip()[:200])
            )
        if STOP_TRACKING_RE.search(line):
            self.stop_tracking_at = time.time()

    def mark_channel_leave(self):
        self._leave_mark = time.time()

    def event_names(self):
        return {e[1] for e in self.ad_events}

    def events_after(self, ts: float):
        return [e for e in self.ad_events if e[0] > ts]

    def tracking_after(self, ts: float):
        return [e for e in self.tracking_events if e[0] > ts]

    def has_started(self):
        return "STARTED" in self.event_names()

    def evaluate_full_play(self):
        names = self.event_names()
        missing_quartile = [q for q in sorted(QUARTILE_EVENTS) if q not in names]
        ok = (
            "STARTED" in names
            and not missing_quartile
            and ("COMPLETED" in names or "ALL_ADS_COMPLETED" in names)
        )
        return {
            "ok": ok,
            "missing_quartile": missing_quartile,
            "events": sorted(names),
        }

    def _significant_ad_events_after_leave(self):
        """이탈 후 판정용 — AD_PROGRESS·SKIPPABLE_STATE_CHANGED 는 무시.

        stopTracking 전 AD_PROGRESS 는 잔여 진행 이벤트.
        SKIPPABLE_STATE_CHANGED 는 이탈 직후 IMA 잔여(스킵 UI 상태)로,
        tracking beacon 지속과 무관 — 3-B FAIL 원인에서 제외.
        """
        after = self.events_after(self._leave_mark)
        significant = []
        for ts, name, snippet in after:
            if name == "SKIPPABLE_STATE_CHANGED":
                continue
            if name == "AD_PROGRESS":
                if self.stop_tracking_at is None or ts <= self.stop_tracking_at:
                    continue
            significant.append((ts, name, snippet))
        return significant

    def evaluate_leave_during(self):
        names = self.event_names()
        if self._leave_mark is None:
            return {"ok": False, "message": "채널 이탈 시점 미기록"}
        if not self.has_started():
            return {"ok": False, "message": "google ad STARTED 미감지"}
        after_ad = self._significant_ad_events_after_leave()
        after_track = self.tracking_after(self._leave_mark)
        stopped = self.stop_tracking_at is not None and self.stop_tracking_at >= (
            self._leave_mark - 2.0
        )
        # PASS: 3-B 목적은 "이탈 후 tracking 중단".
        # stopTracking 이 확인되고 tracking beacon 이 0이면, 이탈 직후 IMA 잔여
        # adEvent(FIRST_QUARTILE 등)는 FAIL 로 보지 않는다.
        ok = not after_track and (not after_ad or stopped)
        message = ""
        if not ok:
            parts = []
            if after_ad:
                after_names = sorted({e[1] for e in after_ad})
                parts.append(
                    f"이탈 후 google adEvent {len(after_ad)}건 "
                    f"({', '.join(after_names)})"
                )
            if after_track:
                parts.append(f"이탈 후 tracking {len(after_track)}건")
            message = "; ".join(parts)
        elif after_ad and stopped:
            after_names = sorted({e[1] for e in after_ad})
            message = (
                "이탈 후 tracking 중단 확인 "
                f"(잔여 adEvent 무시: {', '.join(after_names)})"
            )
        elif not stopped:
            message = (
                "이탈 후 유의미 adEvent·tracking 없음 "
                "(stopTracking 로그 미확인)"
            )
        else:
            message = "이탈 후 tracking 중단 확인"
        return {
            "ok": ok,
            "message": message,
            "stop_tracking": stopped,
            "ad_events_after_leave": len(after_ad),
            "tracking_after_leave": len(after_track),
            "events": sorted(names),
        }

    def evaluate_skip_ok(self):
        names = self.event_names()
        ok = "SKIPPABLE_STATE_CHANGED" in names and "SKIPPED" in names
        return {
            "ok": ok,
            "events": sorted(names),
        }

    def leave_diagnostics(self):
        """3-B 진단용 — 이탈 시점 기준 이벤트·tracking·stopTracking 상대 타임라인."""
        if self._leave_mark is None:
            return {"leave_mark": None, "lines": ["이탈 시점 미기록"]}
        base = self._leave_mark
        lines = []
        if self.stop_tracking_at is not None:
            lines.append(
                f"stopTracking: 이탈 {self.stop_tracking_at - base:+.1f}초"
            )
        else:
            lines.append("stopTracking: 미발생")
        for ts, name, _ in self._significant_ad_events_after_leave():
            lines.append(f"adEvent {name}: 이탈 {ts - base:+.1f}초")
        for ts, name, _ in self.tracking_after(base):
            lines.append(f"tracking {name}: 이탈 {ts - base:+.1f}초")
        if len(lines) == 1:
            lines.append("이탈 후 adEvent·tracking 없음 (정상)")
        return {"leave_mark": base, "lines": lines}
