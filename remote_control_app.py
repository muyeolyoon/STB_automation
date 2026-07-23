import threading
import tkinter as tk
from tkinter import ttk

from component.remote_control import (
    DEFAULT_ADB,
    adb_connect,
    adb_devices,
    adb_keyevent_many,
    send_channel_number,
)


KEY = {
    "POWER": 26,
    "HOME": 3,
    "BACK": 4,
    "MENU": 1,
    "UP": 19,
    "DOWN": 20,
    "LEFT": 21,
    "RIGHT": 22,
    "OK": 23,
    "VOL_UP": 24,
    "VOL_DOWN": 25,
    "CH_UP": 166,
    "CH_DOWN": 167,
    "ENTER": 66,
}


class RemoteControlApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("STB Remote Control (ADB)")
        self.geometry("900x600")
        self.minsize(860, 560)

        self.adb_path = tk.StringVar(value=DEFAULT_ADB)
        self.device_entry = tk.StringVar(value="")
        self.channel_entry = tk.StringVar(value="")
        self.custom_keycode = tk.StringVar(value="")

        self._build_ui()
        self._refresh_devices_from_adb()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # Left: device manager
        left = ttk.Frame(root)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        root.columnconfigure(0, weight=3)
        root.columnconfigure(1, weight=5)
        root.rowconfigure(0, weight=1)

        ttk.Label(left, text="ADB 경로").grid(row=0, column=0, sticky="w")
        ttk.Entry(left, textvariable=self.adb_path).grid(row=1, column=0, sticky="ew")

        ttk.Label(left, text="디바이스 추가 (IP만 입력 시 :5555 자동, 예: 192.168.10.8 또는 USB serial)").grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )
        add_row = ttk.Frame(left)
        add_row.grid(row=3, column=0, sticky="ew")
        add_row.columnconfigure(0, weight=1)
        ttk.Entry(add_row, textvariable=self.device_entry).grid(row=0, column=0, sticky="ew")
        ttk.Button(add_row, text="추가", command=self._add_device).grid(row=0, column=1, padx=(8, 0))

        btn_row = ttk.Frame(left)
        btn_row.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(btn_row, text="선택 연결", command=self._connect_selected).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(btn_row, text="목록 새로고침", command=self._refresh_devices_from_adb).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(btn_row, text="선택 삭제", command=self._remove_selected).grid(row=0, column=2)

        self.tree = ttk.Treeview(left, columns=("device", "status"), show="headings", height=12)
        self.tree.heading("device", text="Device")
        self.tree.heading("status", text="Status")
        self.tree.column("device", width=240, stretch=True)
        self.tree.column("status", width=120, stretch=False)
        self.tree.grid(row=5, column=0, sticky="nsew", pady=(10, 0))
        left.rowconfigure(5, weight=1)
        left.columnconfigure(0, weight=1)

        self.log = tk.Text(left, height=10, wrap="word")
        self.log.grid(row=6, column=0, sticky="nsew", pady=(10, 0))
        left.rowconfigure(6, weight=1)

        # Right: remote buttons
        right = ttk.Frame(root)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)

        top_keys = ttk.Frame(right)
        top_keys.grid(row=0, column=0, sticky="ew", pady=(2, 0))
        top_keys.columnconfigure((0, 1, 2, 3), weight=1, uniform="top_key")
        for i, (label, keyname) in enumerate([("POWER", "POWER"), ("HOME", "HOME"), ("BACK", "BACK"), ("MENU", "MENU")]):
            ttk.Button(top_keys, text=label, command=lambda k=keyname: self._send_key(k)).grid(
                row=0, column=i, padx=3, pady=2, sticky="ew"
            )

        dpad = ttk.LabelFrame(right, text="DPAD")
        dpad.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        for c in range(3):
            dpad.columnconfigure(c, weight=1)
        ttk.Button(dpad, text="▲", command=lambda: self._send_key("UP")).grid(row=0, column=1, padx=6, pady=6, sticky="ew")
        ttk.Button(dpad, text="◀", command=lambda: self._send_key("LEFT")).grid(row=1, column=0, padx=6, pady=6, sticky="ew")
        ttk.Button(dpad, text="OK", command=lambda: self._send_key("OK")).grid(row=1, column=1, padx=6, pady=6, sticky="ew")
        ttk.Button(dpad, text="▶", command=lambda: self._send_key("RIGHT")).grid(row=1, column=2, padx=6, pady=6, sticky="ew")
        ttk.Button(dpad, text="▼", command=lambda: self._send_key("DOWN")).grid(row=2, column=1, padx=6, pady=6, sticky="ew")

        bottom = ttk.Frame(right)
        bottom.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        bottom.columnconfigure(0, weight=1)

        vol_ch = ttk.LabelFrame(bottom, text="Volume / Channel")
        vol_ch.grid(row=0, column=0, sticky="ew")
        for c in range(4):
            vol_ch.columnconfigure(c, weight=1)
        ttk.Button(vol_ch, text="VOL +", command=lambda: self._send_key("VOL_UP")).grid(
            row=0, column=0, padx=4, pady=6, sticky="ew"
        )
        ttk.Button(vol_ch, text="VOL -", command=lambda: self._send_key("VOL_DOWN")).grid(
            row=0, column=1, padx=4, pady=6, sticky="ew"
        )
        ttk.Button(vol_ch, text="CH +", command=lambda: self._send_key("CH_UP")).grid(
            row=0, column=2, padx=4, pady=6, sticky="ew"
        )
        ttk.Button(vol_ch, text="CH -", command=lambda: self._send_key("CH_DOWN")).grid(
            row=0, column=3, padx=4, pady=6, sticky="ew"
        )

        ch = ttk.LabelFrame(bottom, text="Channel number")
        ch.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ch.columnconfigure(0, weight=1)
        ttk.Label(ch, text="채널 번호").grid(row=0, column=0, sticky="w")
        ttk.Entry(ch, textvariable=self.channel_entry).grid(row=1, column=0, sticky="ew", pady=(2, 0))
        ttk.Button(ch, text="전송", command=self._send_channel).grid(row=2, column=0, pady=(8, 0), sticky="ew")

        custom = ttk.LabelFrame(right, text="Custom keycode")
        custom.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        custom.columnconfigure(0, weight=1)
        ttk.Entry(custom, textvariable=self.custom_keycode).grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=6)
        ttk.Button(custom, text="Keyevent 전송", command=self._send_custom_keycode).grid(row=0, column=1, pady=6)

    def _log(self, msg: str) -> None:
        self.log.insert("end", msg.rstrip() + "\n")
        self.log.see("end")

    def _selected_devices(self):
        sel = self.tree.selection()
        if not sel:
            # none selected -> all
            return [self.tree.set(iid, "device") for iid in self.tree.get_children()]
        return [self.tree.set(iid, "device") for iid in sel]

    def _set_status(self, device: str, status: str) -> None:
        for iid in self.tree.get_children():
            if self.tree.set(iid, "device") == device:
                self.tree.set(iid, "status", status)
                return

    def _add_device(self) -> None:
        d = self.device_entry.get().strip()
        if not d:
            return
        # IP처럼 보이는데 포트가 없으면 기본 5555 붙임
        if "." in d and ":" not in d:
            d = d + ":5555"
        for iid in self.tree.get_children():
            if self.tree.set(iid, "device") == d:
                self._log(f"[INFO] 이미 목록에 있음: {d}")
                self.device_entry.set("")
                return
        self.tree.insert("", "end", values=(d, "added"))
        self.device_entry.set("")

    def _remove_selected(self) -> None:
        for iid in self.tree.selection():
            self.tree.delete(iid)

    def _refresh_devices_from_adb(self) -> None:
        adb = self.adb_path.get().strip() or DEFAULT_ADB

        device_ids, res = adb_devices(adb_path=adb)
        if not res.ok and res.stderr:
            self._log(f"[ERROR] {res.stderr}")
            return

        existing = {self.tree.set(iid, "device"): iid for iid in self.tree.get_children()}
        for d in device_ids:
            if d in existing:
                self.tree.set(existing[d], "status", "connected")
            else:
                self.tree.insert("", "end", values=(d, "connected"))

        self._log(f"[INFO] adb devices: {len(device_ids)}대 (state=device)")

    def _connect_selected(self) -> None:
        adb = self.adb_path.get().strip() or DEFAULT_ADB
        targets = self._selected_devices()
        if not targets:
            self._log("[WARN] 디바이스가 없습니다.")
            return

        def work():
            for d in targets:
                self._set_status(d, "connecting...")
                res = adb_connect(d, adb_path=adb)
                if res.ok:
                    self._set_status(d, "connected")
                    self._log(f"[OK] connect {d}: {res.stdout or 'ok'}")
                else:
                    self._set_status(d, "failed")
                    self._log(f"[FAIL] connect {d}: {res.stdout} {res.stderr}".strip())

        threading.Thread(target=work, daemon=True).start()

    def _send_key(self, keyname: str) -> None:
        adb = self.adb_path.get().strip() or DEFAULT_ADB
        targets = self._selected_devices()
        if not targets:
            self._log("[WARN] 디바이스가 없습니다.")
            return
        keycode = KEY[keyname]

        def work():
            self._log(f"[SEND] {keyname} ({keycode}) -> {', '.join(targets)}")
            results = adb_keyevent_many(targets, keycode, adb_path=adb)
            for dev, res in results:
                if res.ok:
                    self._set_status(dev, "connected")
                else:
                    self._set_status(dev, "failed")
                    self._log(f"[FAIL] {dev}: {res.stderr or res.stdout}")

        threading.Thread(target=work, daemon=True).start()

    def _send_channel(self) -> None:
        adb = self.adb_path.get().strip() or DEFAULT_ADB
        targets = self._selected_devices()
        channel = self.channel_entry.get().strip()
        if not targets:
            self._log("[WARN] 디바이스가 없습니다.")
            return
        if not channel:
            self._log("[WARN] 채널 번호를 입력하세요.")
            return

        def work():
            self._log(f"[SEND] CHANNEL {channel} -> {', '.join(targets)}")
            results = send_channel_number(targets, channel, adb_path=adb)
            for dev, res in results:
                if not res.ok:
                    self._set_status(dev, "failed")
                    self._log(f"[FAIL] {dev}: {res.stderr or res.stdout}")

        threading.Thread(target=work, daemon=True).start()

    def _send_custom_keycode(self) -> None:
        value = self.custom_keycode.get().strip()
        if not value:
            self._log("[WARN] keycode를 입력하세요 (예: 3=HOME, 4=BACK, 23=OK).")
            return
        try:
            keycode = int(value)
        except ValueError:
            self._log("[WARN] 숫자 keycode만 가능합니다.")
            return
        adb = self.adb_path.get().strip() or DEFAULT_ADB
        targets = self._selected_devices()
        if not targets:
            self._log("[WARN] 디바이스가 없습니다.")
            return

        def work():
            self._log(f"[SEND] KEYCODE {keycode} -> {', '.join(targets)}")
            results = adb_keyevent_many(targets, keycode, adb_path=adb)
            for dev, res in results:
                if not res.ok:
                    self._set_status(dev, "failed")
                    self._log(f"[FAIL] {dev}: {res.stderr or res.stdout}")

        threading.Thread(target=work, daemon=True).start()


if __name__ == "__main__":
    app = RemoteControlApp()
    app.mainloop()

