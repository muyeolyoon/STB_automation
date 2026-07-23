import tkinter as tk
from tkinter import filedialog, messagebox
import subprocess
import os
import threading

class PythonScriptRunnerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AnypointMedia RPA")
        self.root.geometry("900x700")
        self.root.configure(bg="#181818")

        try:
            self.root.iconbitmap("anypoint-media.ico")
        except:
            pass

        self.scripts = []
        self.dragging_index = None

        self.title_label = tk.Label(
            root, text="AnypointMedia RPA", font=("Segoe UI", 24, "bold"),
            bg="#181818", fg="#00ffc8"
        )
        self.title_label.pack(pady=(20, 10))

        self.desc_label = tk.Label(
            root, text="자동화 파일을 선택하고 실행하세요.",
            font=("Segoe UI", 13), bg="#181818", fg="#cccccc"
        )
        self.desc_label.pack(pady=(0, 20))

        self.list_frame = tk.Frame(root, bg="#181818")
        self.list_frame.pack(pady=10)

        self.script_listbox = tk.Listbox(
            self.list_frame, width=78, height=18, selectmode=tk.BROWSE,
            font=("Consolas", 13), bg="#1e1e1e", fg="#ffffff",
            selectbackground="#00aaff", bd=0, highlightthickness=1, 
            highlightcolor="#00ffc8", relief="flat"
        )
        self.script_listbox.pack(side=tk.LEFT, fill=tk.BOTH)

        self.script_listbox.bind("<Button-1>", self.on_click)
        self.script_listbox.bind("<B1-Motion>", self.on_drag)
        self.script_listbox.bind("<Double-Button-1>", self.remove_on_double_click)

        self.toolbar = tk.Frame(root, bg="#181818")
        self.toolbar.pack(pady=(0, 10))
        self.toolbar.pack(padx=(10, 10))

        self.create_button("파일 추가", self.add_script, "#00c896")
        self.create_button("목록 비우기", self.clear_listbox, "#fa141f")
        self.create_button("실행 (순차)", self.start_execution_thread, "#0085ff")

        self.log_label = tk.Label(
            root, text="실행 로그", font=("Segoe UI", 12, "bold"),
            bg="#181818", fg="#ffffff"
        )
        self.log_label.pack(pady=(10, 5))

        self.log_text = tk.Text(
            root, height=12, width=100, font=("Consolas", 9),
            bg="#1e1e1e", fg="#00ff88", insertbackground="#ffffff",
            wrap=tk.WORD, bd=0, highlightthickness=1, highlightcolor="#00ffc8"
        )
        self.log_text.pack(pady=(0, 20))

    def create_button(self, text, command, color):
        button = tk.Button(
            self.toolbar, text=text, command=command,
            width=18, height=2, bg=color, fg="white",
            font=("Segoe UI", 10, "bold"), bd=0,
            activebackground="white", activeforeground=color,
            cursor="hand2"
        )
        button.pack(side=tk.LEFT, padx=8)

    def add_script(self):
        filepaths = filedialog.askopenfilenames(filetypes=[("Python Files", "*.py")])
        for path in filepaths:
            if path not in self.scripts:
                self.scripts.append(path)
        self.update_listbox()

    def remove_on_double_click(self, event):
        index = self.script_listbox.nearest(event.y)
        if 0 <= index < len(self.scripts):
            del self.scripts[index]
            self.update_listbox()

    def clear_listbox(self):
        self.scripts.clear()
        self.script_listbox.delete(0, tk.END)
        self.log_text.delete(1.0, tk.END)

    def update_listbox(self):
        self.script_listbox.delete(0, tk.END)
        for idx, script in enumerate(self.scripts):
            filename = os.path.basename(script)
            self.script_listbox.insert(tk.END, f"{idx+1}. {filename}")

    def on_click(self, event):
        widget = event.widget
        self.dragging_index = widget.nearest(event.y)

    def on_drag(self, event):
        widget = event.widget
        idx = widget.nearest(event.y)
        if idx != self.dragging_index and 0 <= idx < len(self.scripts):
            self.scripts[self.dragging_index], self.scripts[idx] = self.scripts[idx], self.scripts[self.dragging_index]
            self.update_listbox()
            self.script_listbox.select_set(idx)
            self.dragging_index = idx

    def start_execution_thread(self):
        thread = threading.Thread(target=self.run_all_scripts)
        thread.start()

    def run_all_scripts(self):
        if not self.scripts:
            messagebox.showwarning("경고", "실행할 스크립트를 추가하세요.")
            return

        for i, script_path in enumerate(self.scripts):
            self.update_listbox()
            self.script_listbox.select_clear(0, tk.END)
            self.script_listbox.select_set(i)
            self.log(f"\n▶ 실행 중: {os.path.basename(script_path)}...\n")

            if os.path.isfile(script_path):
                try:
                    result = subprocess.run(["python", script_path], capture_output=True, text=True)
                    self.log(result.stdout)
                    if result.stderr:
                        self.log("[에러]\n" + result.stderr)
                except Exception as e:
                    self.log(f"오류 발생: {e}\n")
                    continue
            else:
                self.log(f"파일이 존재하지 않습니다: {script_path}\n")
                continue

        self.log("\n✅ 자동화 실행 완료!\n")
        messagebox.showinfo("완료", "모든 자동화 파일을 순차 실행했습니다.")

    def log(self, message):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)

if __name__ == "__main__":
    root = tk.Tk()
    app = PythonScriptRunnerApp(root)
    root.mainloop()
