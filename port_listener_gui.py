#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
테스트용 포트 리스너 (GUI)
- 지정한 포트를 열어서 TCP 연결을 대기(LISTEN)
- 접속이 들어오면 누가(IP:Port) 언제 접속했는지 로그로 표시
- 접속한 상대에게 짧은 응답 메시지를 보내고 연결을 닫음 (텔넷으로 확인해도 응답이 보임)
- 여러 포트를 동시에 열어둘 수 있음
- 다른 PC에서 포트 연결 확인 프로그램(connection_checker_gui.py)으로 테스트할 상대로 사용

주의: 이 프로그램은 테스트/점검 목적입니다. 사내망 등 본인이 접근 권한이 있는
환경에서만 사용하세요. 실행 중에는 지정한 포트가 외부 접속에 열려있게 됩니다.

실행 방법:
  python3 port_listener_gui.py

필요 사항: Python 3.8+  (tkinter 표준 라이브러리 포함, 별도 설치 불필요)
"""

import socket
import threading
import queue
import time
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        ip = socket.gethostbyname(socket.gethostname())
    finally:
        s.close()
    return ip


class PortListener:
    """단일 포트에 대한 리스닝 스레드 관리"""

    def __init__(self, port: int, log_callback, count_callback):
        self.port = port
        self.log_callback = log_callback
        self.count_callback = count_callback
        self.server_sock = None
        self.thread = None
        self.running = False
        self.accepted_count = 0

    def start(self):
        try:
            self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_sock.bind(("0.0.0.0", self.port))
            self.server_sock.listen(5)
            self.server_sock.settimeout(1.0)  # accept 루프에서 주기적으로 종료 신호 확인
        except OSError as e:
            self.log_callback(self.port, f"[오류] 포트 {self.port} 열기 실패: {e}", "fail")
            return False

        self.running = True
        self.thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.thread.start()
        self.log_callback(self.port, f"포트 {self.port} 리스닝 시작 (대기 중)", "info")
        return True

    def _accept_loop(self):
        hostname = socket.gethostname()
        while self.running:
            try:
                conn, addr = self.server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            self.accepted_count += 1
            self.count_callback(self.port, self.accepted_count)
            client_ip, client_port = addr
            ts = datetime.now().strftime("%H:%M:%S")
            self.log_callback(
                self.port,
                f"[{ts}] 접속 감지: {client_ip}:{client_port}  (포트 {self.port})",
                "ok",
            )
            try:
                conn.settimeout(2.0)
                msg = f"OK from {hostname} - port {self.port} is open\n"
                conn.sendall(msg.encode("utf-8"))
            except OSError:
                pass
            finally:
                conn.close()

        try:
            self.server_sock.close()
        except OSError:
            pass

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        self.log_callback(self.port, f"포트 {self.port} 리스닝 중지", "info")


class ListenerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("테스트용 포트 리스너")
        self.root.geometry("780x560")

        self.listeners = {}  # port -> PortListener
        self.log_queue = queue.Queue()

        self._build_ui()
        self._poll_queue()
        self._refresh_ip()

    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="이 PC의 IP:").pack(side="left")
        self.ip_var = tk.StringVar(value="확인 중...")
        ttk.Label(top, textvariable=self.ip_var, font=("", 10, "bold")).pack(side="left", padx=(4, 12))
        ttk.Button(top, text="새로고침", command=self._refresh_ip).pack(side="left")
        ttk.Label(
            top, text="※ 다른 PC에서 위 IP + 아래 포트로 접속 테스트하세요", foreground="#666"
        ).pack(side="left", padx=12)

        input_frame = ttk.LabelFrame(self.root, text="열어둘 포트")
        input_frame.pack(fill="x", **pad)

        ttk.Label(input_frame, text="포트 번호:").grid(row=0, column=0, sticky="w", **pad)
        self.port_var = tk.StringVar(value="9000")
        ttk.Entry(input_frame, textvariable=self.port_var, width=12).grid(row=0, column=1, **pad)
        ttk.Button(input_frame, text="+ 포트 열기(리스닝 시작)", command=self._start_listener).grid(
            row=0, column=2, **pad
        )
        self.root.bind("<Return>", lambda e: self._start_listener())

        ttk.Label(
            input_frame,
            text="(1024 이하 포트는 관리자 권한이 필요할 수 있습니다. 9000~9999 같은 포트 권장)",
            foreground="#888",
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=6)

        # 활성 리스너 목록
        list_frame = ttk.LabelFrame(self.root, text="열려있는 포트 목록")
        list_frame.pack(fill="x", **pad)

        self.tree = ttk.Treeview(
            list_frame, columns=("port", "status", "count"), show="headings", height=5
        )
        self.tree.heading("port", text="포트")
        self.tree.heading("status", text="상태")
        self.tree.heading("count", text="접속 수")
        self.tree.column("port", width=100, anchor="center")
        self.tree.column("status", width=150, anchor="center")
        self.tree.column("count", width=100, anchor="center")
        self.tree.pack(side="left", fill="x", expand=True)

        btn_col = ttk.Frame(list_frame)
        btn_col.pack(side="left", padx=8)
        ttk.Button(btn_col, text="선택 포트 닫기", command=self._stop_selected).pack(fill="x", pady=2)
        ttk.Button(btn_col, text="전체 포트 닫기", command=self._stop_all).pack(fill="x", pady=2)

        # 로그
        log_frame = ttk.LabelFrame(self.root, text="접속 로그")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, wrap="word", font=("Consolas", 10))
        self.log_text.pack(fill="both", expand=True)
        self.log_text.tag_config("ok", foreground="#0a7d2c")
        self.log_text.tag_config("fail", foreground="#c0392b")
        self.log_text.tag_config("info", foreground="#1a5fb4")

        self.status_var = tk.StringVar(value="대기 중 - 포트를 추가해서 리스닝을 시작하세요")
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w").pack(
            fill="x", side="bottom"
        )

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _refresh_ip(self):
        try:
            self.ip_var.set(get_local_ip())
        except Exception as e:
            self.ip_var.set(f"확인 실패: {e}")

    def _log(self, port, text, tag=None):
        self.log_queue.put((text, tag))

    def _count_update(self, port, count):
        self.log_queue.put((("__COUNT__", port, count), None))

    def _poll_queue(self):
        try:
            while True:
                text, tag = self.log_queue.get_nowait()
                if isinstance(text, tuple) and text[0] == "__COUNT__":
                    _, port, count = text
                    item_id = f"port_{port}"
                    if self.tree.exists(item_id):
                        vals = list(self.tree.item(item_id, "values"))
                        vals[2] = count
                        self.tree.item(item_id, values=vals)
                    continue
                ts = datetime.now().strftime("%H:%M:%S")
                self.log_text.insert("end", f"{text}\n", tag)
                self.log_text.see("end")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)

    def _start_listener(self):
        port_str = self.port_var.get().strip()
        try:
            port = int(port_str)
            if not (0 < port <= 65535):
                raise ValueError
        except ValueError:
            messagebox.showwarning("입력 오류", "포트는 1~65535 사이의 숫자여야 합니다.")
            return

        if port in self.listeners:
            messagebox.showinfo("이미 실행 중", f"포트 {port}는 이미 리스닝 중입니다.")
            return

        listener = PortListener(port, self._log, self._count_update)
        ok = listener.start()
        if ok:
            self.listeners[port] = listener
            item_id = f"port_{port}"
            self.tree.insert("", "end", iid=item_id, values=(port, "리스닝 중", 0))
            self.status_var.set(f"포트 {port} 리스닝 시작됨. 총 {len(self.listeners)}개 포트 열림.")
        else:
            messagebox.showerror(
                "포트 열기 실패",
                f"포트 {port}를 열 수 없습니다.\n"
                f"- 이미 다른 프로그램이 사용 중이거나\n"
                f"- 1024 이하 포트라 관리자 권한이 필요하거나\n"
                f"- 방화벽에서 차단했을 수 있습니다.",
            )

    def _stop_selected(self):
        for item_id in self.tree.selection():
            port = int(self.tree.item(item_id, "values")[0])
            self._stop_port(port)

    def _stop_all(self):
        for port in list(self.listeners.keys()):
            self._stop_port(port)

    def _stop_port(self, port):
        listener = self.listeners.pop(port, None)
        if listener:
            listener.stop()
        item_id = f"port_{port}"
        if self.tree.exists(item_id):
            self.tree.delete(item_id)
        self.status_var.set(f"포트 {port} 닫힘. 남은 {len(self.listeners)}개 포트 열림.")

    def _on_close(self):
        self._stop_all()
        time.sleep(0.2)
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ListenerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
