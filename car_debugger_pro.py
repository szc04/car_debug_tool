import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import threading
import time
import os
import json
import serial
from adbutils import adb
import subprocess
import queue  # ← 新增：用于线程间通信


class CarDebuggerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("车机自动化调试工具 - Windows命令版 v1.0")
        self.root.geometry("1300x900")

        self.serial_conn = None
        self.serial_thread = None
        self.serial_running = False
        self.serial_log_path = "serial.log"
        self.adb_log_path = "adb.log"
        self.config_file = "debugger_config.json"

        # === 新增：线程安全队列 ===
        self.serial_queue = queue.Queue()  # 串口数据队列
        self.adb_queue = queue.Queue()     # ADB 日志队列（可选增强）

        # 加载配置
        self.config = self.load_config()

        self.create_ui()
        self.clear_logs()

        # 启动队列检查循环（主线程定期刷新UI）
        self.check_serial_queue()
        self.check_adb_queue()

    def load_config(self):
        """加载上次的配置"""
        default_config = {
            "serial_port": "COM3",
            "serial_baud": "115200",
            "file1_path": "",
            "file1_target": "/data/local/tmp/",
            "file2_path": "",
            "file2_target": "/data/local/tmp/",
            "log_dir": "./logs/",
            "step1_cmd": "getprop\nls /system\n",
            "step2_cmd": "getprop ro.build.fingerprint\ngetprop ro.product.model\n",
            "step3_cmd": "reboot\n",
            "step4_cmd": "cat /proc/version\ngetprop ro.build.fingerprint\n",
            "step5_cmd": "dmesg | tail -20\nlogread | tail -20\n",
            "step6_cmd": "adb pull /sdcard/test1114phone5.txt .\nadb logcat -d > logcat.txt\n"
        }
        
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                    default_config.update(saved)
                    return default_config
            except:
                pass
        return default_config

    def save_config(self):
        """保存当前配置"""
        config = {
            "serial_port": self.config["serial_port"],
            "serial_baud": self.config["serial_baud"],
            "file1_path": self.config["file1_path"],
            "file1_target": self.config["file1_target"],
            "file2_path": self.config["file2_path"],
            "file2_target": self.config["file2_target"],
            "log_dir": self.config["log_dir"],
            "step1_cmd": self.step1_cmd_text.get("1.0", tk.END),
            "step2_cmd": self.step2_cmd_text.get("1.0", tk.END),
            "step3_cmd": self.step3_cmd_text.get("1.0", tk.END),
            "step4_cmd": self.step4_cmd_text.get("1.0", tk.END),
            "step5_cmd": self.step5_cmd_text.get("1.0", tk.END),
            "step6_cmd": self.step6_cmd_text.get("1.0", tk.END)
        }
        
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except:
            pass

    def clear_logs(self):
        self.serial_lines = []
        self.adb_lines = []

    def create_ui(self):
        # === 顶部：双日志窗口（横向并列）===
        log_pane = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        log_pane.pack(fill="x", padx=10, pady=(10, 5))

        # 串口日志
        serial_frame = ttk.LabelFrame(log_pane, text="串口日志 (UART) - 实时滚动", padding=5)
        self.serial_log = scrolledtext.ScrolledText(serial_frame, height=10, state='disabled', wrap=tk.WORD)
        self.serial_log.pack(fill="both", expand=True)
        log_pane.add(serial_frame, weight=1)

        # ADB 日志
        adb_frame = ttk.LabelFrame(log_pane, text="ADB 日志 - 实时滚动", padding=5)
        self.adb_log = scrolledtext.ScrolledText(adb_frame, height=10, state='disabled', wrap=tk.WORD)
        self.adb_log.pack(fill="both", expand=True)
        log_pane.add(adb_frame, weight=1)

        # === 中间：双列步骤布局 ===
        main_pane = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_pane.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # 左列：步骤1-2-3
        left_frame = ttk.LabelFrame(main_pane, text="左侧步骤", padding=10)
        # 步骤1: 串口连接
        self.create_step_with_serial_params(left_frame, 1, "连接串口并执行初始化命令",
            self.config["step1_cmd"],
            self.run_step1)
        # 步骤2: ADB命令窗口（半高度，支持Windows命令）
        self.create_step_in_column(left_frame, 2, "执行 ADB 命令窗口（支持Windows命令）",
            [],
            self.config["step2_cmd"],
            self.run_step2,
            height=3)
        # 步骤3: 上传文件+重启
        self.create_step_in_column(left_frame, 3, "上传文件并重启车机",
            [("文件1 - 本地路径:", self.config["file1_path"]), ("目标路径1:", self.config["file1_target"]),
             ("文件2 - 本地路径:", self.config["file2_path"]), ("目标路径2:", self.config["file2_target"])],
            self.config["step3_cmd"],
            self.run_step3)
        main_pane.add(left_frame, weight=1)

        # 右列：步骤4-5-6
        right_frame = ttk.LabelFrame(main_pane, text="右侧步骤", padding=10)
        # 步骤4: 启动后串口命令（带中断按钮）
        self.create_step_in_column_with_interrupt(right_frame, 4, "车机启动后执行串口命令",
            [],
            self.config["step4_cmd"],
            self.run_step4)
        # 步骤5: 启动后串口命令（无中断按钮）
        self.create_step_in_column(right_frame, 5, "车机启动后执行串口命令",
            [],
            self.config["step5_cmd"],
            self.run_step5)
        # 步骤6: ADB命令窗口（支持Windows命令）
        self.create_step_in_column(right_frame, 6, "执行 ADB 命令窗口（支持Windows命令）",
            [],
            self.config["step6_cmd"],
            self.run_step6)
        main_pane.add(right_frame, weight=1)

        # === 启动时自动连接 ===
        self.root.after(100, self.auto_connect)

    def auto_connect(self):
        """启动时自动连接串口"""
        try:
            port = self.config["serial_port"]
            baud = int(self.config["serial_baud"])
            self.serial_conn = serial.Serial(port, baud, timeout=1)
            self.serial_queue.put(f"[✓] 自动连接串口 {port} @ {baud}")
            self.start_serial_monitor()
            self.serial_queue.put("[ℹ] 串口实时监控已启动")
        except Exception as e:
            self.serial_queue.put(f"[⚠] 自动连接串口失败: {e}")

    def create_step_with_serial_params(self, parent, step_num, title, placeholder, callback):
        frame = ttk.LabelFrame(parent, text=f"步骤 {step_num}: {title}", padding=10)
        frame.pack(fill="x", pady=5)

        params_frame = ttk.Frame(frame)
        params_frame.pack(fill="x", pady=2)
        
        ttk.Label(params_frame, text="串口设备:", width=10, anchor="w").pack(side="left")
        self.serial_port_entry = ttk.Entry(params_frame, width=15)
        self.serial_port_entry.insert(0, self.config["serial_port"])
        self.serial_port_entry.pack(side="left", padx=(5, 10))
        
        ttk.Label(params_frame, text="波特率:", width=8, anchor="w").pack(side="left")
        self.serial_baud_entry = ttk.Entry(params_frame, width=10)
        self.serial_baud_entry.insert(0, self.config["serial_baud"])
        self.serial_baud_entry.pack(side="left", padx=(5, 0))

        cmd_label = ttk.Label(frame, text="命令区域（每行一条命令）:")
        cmd_label.pack(anchor="w", pady=(10, 2))
        cmd_text = scrolledtext.ScrolledText(frame, height=5, wrap=tk.WORD)
        cmd_text.insert("1.0", placeholder.strip())
        cmd_text.pack(fill="x", pady=(0, 10))

        self.step1_cmd_text = cmd_text

        btn = ttk.Button(frame, text="执行", command=lambda: self.run_in_thread(lambda: callback([self.serial_port_entry, self.serial_baud_entry], cmd_text)))
        btn.pack(side="right")
        return frame

    def create_step_in_column_with_interrupt(self, parent, step_num, title, fields, placeholder, callback):
        frame = ttk.LabelFrame(parent, text=f"步骤 {step_num}: {title}", padding=10)
        frame.pack(fill="x", pady=5)

        cmd_label = ttk.Label(frame, text="命令区域（每行一条命令）:")
        cmd_label.pack(anchor="w", pady=(10, 2))
        cmd_text = scrolledtext.ScrolledText(frame, height=5, wrap=tk.WORD)
        cmd_text.insert("1.0", placeholder.strip())
        cmd_text.pack(fill="x", pady=(0, 10))

        self.step4_cmd_text = cmd_text

        button_frame = ttk.Frame(frame)
        button_frame.pack(side="right", pady=2)
        
        interrupt_btn = ttk.Button(button_frame, text="中断log", command=self.send_interrupt_to_serial)
        interrupt_btn.pack(side="right", padx=(5, 0))
        
        exec_btn = ttk.Button(button_frame, text="执行", command=lambda: self.run_in_thread(lambda: callback([], cmd_text)))
        exec_btn.pack(side="right")
        
        return frame

    def create_step_in_column(self, parent, step_num, title, fields, placeholder, callback, height=5):
        frame = ttk.LabelFrame(parent, text=f"步骤 {step_num}: {title}", padding=10)
        frame.pack(fill="x", pady=5)

        entries = []
        for label, default in fields:
            row = ttk.Frame(frame)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=label, width=20, anchor="w").pack(side="left")
            entry = ttk.Entry(row)
            entry.insert(0, default)
            entry.pack(side="left", fill="x", expand=True, padx=(5, 0))
            entries.append(entry)

            if "本地路径" in label:
                btn = ttk.Button(row, text="选择", width=6,
                                 command=lambda e=entry: self.select_file_to_entry(e))
                btn.pack(side="right", padx=(5, 0))

        cmd_text = None
        if placeholder:
            cmd_label = ttk.Label(frame, text="命令区域（每行一条命令）:")
            cmd_label.pack(anchor="w", pady=(10, 2))
            cmd_text = scrolledtext.ScrolledText(frame, height=height, wrap=tk.WORD)
            cmd_text.insert("1.0", placeholder.strip())
            cmd_text.pack(fill="x", pady=(0, 10))

        if step_num == 1: self.step1_cmd_text = cmd_text
        elif step_num == 2: self.step2_cmd_text = cmd_text
        elif step_num == 3: self.step3_cmd_text = cmd_text
        elif step_num == 4: self.step4_cmd_text = cmd_text
        elif step_num == 5: self.step5_cmd_text = cmd_text
        elif step_num == 6: self.step6_cmd_text = cmd_text

        btn = ttk.Button(frame, text="执行", command=lambda: self.run_in_thread(lambda: callback(entries, cmd_text)))
        btn.pack(side="right")
        return frame

    def select_file_to_entry(self, entry):
        path = filedialog.askopenfilename(
            title="选择文件",
            filetypes=[("All files", "*.*"), ("APK", "*.apk"), ("SO", "*.so"), ("Log", "*.log")]
        )
        if path:
            entry.delete(0, tk.END)
            entry.insert(0, path)
            if hasattr(self, 'entries_step3'):
                pass  # 可忽略，配置在 save_config 中处理

    # ================== 安全的日志更新方法 ==================
    def log_serial(self, msg):
        """仅由主线程调用！"""
        self.serial_lines.append(msg)
        self._update_log(self.serial_log, self.serial_lines, self.serial_log_path)

    def log_adb(self, msg):
        """仅由主线程调用！"""
        self.adb_lines.append(msg)
        self._update_log(self.adb_log, self.adb_lines, self.adb_log_path)

    def _update_log(self, widget, lines, filepath):
        widget.config(state='normal')
        widget.delete(1.0, tk.END)
        content = "\n".join(lines[-500:])
        widget.insert(tk.END, content)
        widget.see(tk.END)
        widget.config(state='disabled')
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
        except:
            pass

    # ================== 队列检查循环（主线程）==================
    def check_serial_queue(self):
        """主线程定期检查串口队列"""
        try:
            while True:
                msg = self.serial_queue.get_nowait()
                self.log_serial(msg)
        except queue.Empty:
            pass
        self.root.after(50, self.check_serial_queue)  # 每50ms检查一次

    def check_adb_queue(self):
        """主线程定期检查ADB队列"""
        try:
            while True:
                msg = self.adb_queue.get_nowait()
                self.log_adb(msg)
        except queue.Empty:
            pass
        self.root.after(50, self.check_adb_queue)

    def run_in_thread(self, func):
        t = threading.Thread(target=func, daemon=True)
        t.start()

    # ================== 实时串口日志监控（线程安全）==================
    def start_serial_monitor(self):
        if self.serial_conn and not self.serial_running:
            self.serial_running = True
            self.serial_thread = threading.Thread(target=self._monitor_serial, daemon=True)
            self.serial_thread.start()

    def stop_serial_monitor(self):
        self.serial_running = False

    def _monitor_serial(self):
        buffer = ""
        while self.serial_running and self.serial_conn and self.serial_conn.is_open:
            try:
                if self.serial_conn.in_waiting > 0:
                    data = self.serial_conn.read(self.serial_conn.in_waiting).decode('utf-8', errors='ignore')
                    buffer += data
                    lines = buffer.split('\n')
                    buffer = lines[-1]
                    for line in lines[:-1]:
                        self.serial_queue.put(line)  # ← 安全放入队列
                time.sleep(0.01)
            except:
                break
        self.serial_running = False

    def send_interrupt_to_serial(self):
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.write(b'\x03')
            self.serial_queue.put("[✓] 已发送中断信号 (Ctrl+C)")
        else:
            self.serial_queue.put("[✗] 串口未连接，无法发送中断")

    # ================== 步骤逻辑（ADB部分也使用队列）==================
    def run_step1(self, entries, cmd_text):
        port = entries[0].get().strip()
        baud = int(entries[1].get().strip())
        self.config["serial_port"] = port
        self.config["serial_baud"] = str(baud)
        self.save_config()

        try:
            if self.serial_conn:
                self.stop_serial_monitor()
                self.serial_conn.close()
            
            self.serial_conn = serial.Serial(port, baud, timeout=1)
            self.serial_queue.put(f"[✓] 连接串口 {port} @ {baud}")
            self.start_serial_monitor()
        except Exception as e:
            self.serial_queue.put(f"[✗] 串口连接失败: {e}")
            return

        cmds = cmd_text.get("1.0", tk.END).strip().splitlines()
        for cmd in cmds:
            cmd = cmd.strip()
            if not cmd or cmd.startswith("#"): continue
            try:
                self.serial_conn.write((cmd + "\n").encode())
                time.sleep(0.5)
                out = self.serial_conn.read_all().decode(errors='ignore').strip()
                self.serial_queue.put(f"$ {cmd}")
                if out:
                    self.serial_queue.put(out)
            except Exception as e:
                self.serial_queue.put(f"[✗] 串口命令 '{cmd}' 失败: {e}")

    def run_step2(self, entries, cmd_text):
        cmds = cmd_text.get("1.0", tk.END).strip().splitlines()
        try:
            d = adb.device()
            for cmd in cmds:
                cmd = cmd.strip()
                if not cmd or cmd.startswith("#"): continue
                
                if cmd.startswith("adb "):
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                    self.adb_queue.put(f"[ADB TOOL] {cmd}")
                    self.adb_queue.put(result.stdout + result.stderr)
                else:
                    out = d.shell(cmd).strip()
                    self.adb_queue.put(f"$ {cmd}")
                    self.adb_queue.put(out if out else "(无输出)")
        except Exception as e:
            self.adb_queue.put(f"[✗] 命令失败: {e}")

    def run_step3(self, entries, cmd_text):
        self.config["file1_path"] = entries[0].get().strip()
        self.config["file1_target"] = entries[1].get().strip()
        self.config["file2_path"] = entries[2].get().strip()
        self.config["file2_target"] = entries[3].get().strip()
        self.save_config()

        paths = [
            (entries[0].get().strip(), entries[1].get().strip()),
            (entries[2].get().strip(), entries[3].get().strip())
        ]
        try:
            d = adb.device()
            for local, remote in paths:
                if local and remote:
                    if not os.path.isfile(local):
                        self.adb_queue.put(f"[⚠] 文件不存在，跳过: {local}")
                        continue
                    d.push(local, remote)
                    self.adb_queue.put(f"[✓] 推送: {local} → {remote}")
        except Exception as e:
            self.adb_queue.put(f"[✗] ADB 上传失败: {e}")
            return

        reboot_cmds = cmd_text.get("1.0", tk.END).strip().splitlines()
        for cmd in reboot_cmds:
            cmd = cmd.strip()
            if not cmd or cmd.startswith("#"): continue
            try:
                d.shell(cmd)
                self.adb_queue.put(f"$ {cmd} → 已发送")
            except Exception as e:
                self.adb_queue.put(f"[✗] 重启命令 '{cmd}' 失败: {e}")

    def run_step4(self, entries, cmd_text):
        if not (self.serial_conn and self.serial_conn.is_open):
            self.serial_queue.put("[✗] 串口未连接")
            return

        cmds = cmd_text.get("1.0", tk.END).strip().splitlines()
        for cmd in cmds:
            cmd = cmd.strip()
            if not cmd or cmd.startswith("#"): continue
            try:
                self.serial_conn.write((cmd + "\n").encode())
                time.sleep(1)
                out = self.serial_conn.read_all().decode(errors='ignore').strip()
                self.serial_queue.put(f"$ {cmd}")
                if out:
                    self.serial_queue.put(out)
            except Exception as e:
                self.serial_queue.put(f"[✗] 串口命令失败: {e}")

    def run_step5(self, entries, cmd_text):
        if not (self.serial_conn and self.serial_conn.is_open):
            self.serial_queue.put("[✗] 串口未连接")
            return

        cmds = cmd_text.get("1.0", tk.END).strip().splitlines()
        for cmd in cmds:
            cmd = cmd.strip()
            if not cmd or cmd.startswith("#"): continue
            try:
                self.serial_conn.write((cmd + "\n").encode())
                time.sleep(1)
                out = self.serial_conn.read_all().decode(errors='ignore').strip()
                self.serial_queue.put(f"$ {cmd}")
                if out:
                    self.serial_queue.put(out)
            except Exception as e:
                self.serial_queue.put(f"[✗] 串口命令失败: {e}")

    def run_step6(self, entries, cmd_text):
        cmds = cmd_text.get("1.0", tk.END).strip().splitlines()
        try:
            d = adb.device()
            for cmd in cmds:
                cmd = cmd.strip()
                if not cmd or cmd.startswith("#"): continue
                
                if cmd.startswith("adb "):
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                    self.adb_queue.put(f"[ADB TOOL] {cmd}")
                    self.adb_queue.put(result.stdout + result.stderr)
                else:
                    out = d.shell(cmd).strip()
                    self.adb_queue.put(f"$ {cmd}")
                    self.adb_queue.put(out if out else "(无输出)")
        except Exception as e:
            self.adb_queue.put(f"[✗] 命令失败: {e}")

    def on_closing(self):
        self.stop_serial_monitor()
        if self.serial_conn:
            try:
                self.serial_conn.close()
            except:
                pass
        self.save_config()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = CarDebuggerApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()