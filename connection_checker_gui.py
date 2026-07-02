#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
범용 포트 연결 확인 도구 (GUI)
- 확인하고 싶은 구간(이름/호스트/포트)을 목록에 등록
- 등록된 모든 구간을 한 번에(병렬) TCP 연결 테스트
- 결과를 표로 확인 (OK / CLOSED / TIMEOUT / ERROR, 응답시간)
- 목록을 CSV로 저장/불러오기 -> 반복 점검에 재사용 가능
- 결과도 CSV로 내보내기 가능

실행 방법:
  python3 connection_checker_gui.py

필요 사항: Python 3.8+  (tkinter 표준 라이브러리 포함, 별도 설치 불필요)
"""

import socket
import csv
import time
import re
import subprocess
import platform
import threading
import queue
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# ---------- 네트워크 유틸 ----------

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


def test_tcp_port(host: str, port: int, timeout: float = 2.0):
    """TCP 연결 시도. (상태, 응답시간ms, 에러메시지) 반환"""
    start = time.time()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        result = sock.connect_ex((host, port))
        elapsed = round((time.time() - start) * 1000, 1)
        if result == 0:
            return "OPEN", elapsed, None
        return "CLOSED", elapsed, None
    except socket.timeout:
        return "TIMEOUT", round((time.time() - start) * 1000, 1), None
    except socket.gaierror as e:
        return "ERROR", None, f"DNS 조회 실패: {e}"
    except OSError as e:
        return "ERROR", None, str(e)
    finally:
        sock.close()


def run_traceroute_raw(host: str, max_hops: int = 30, timeout_per_hop_sec: float = 1.0):
    """OS별 경로 추적 명령을 실행해서 원본 출력을 반환. (성공여부, 원본출력) 반환"""
    system = platform.system().lower()
    if system == "windows":
        cmd = ["tracert", "-h", str(max_hops), "-w", str(int(timeout_per_hop_sec * 1000)), host]
    else:
        cmd = ["traceroute", "-m", str(max_hops), "-w", str(int(timeout_per_hop_sec)), host]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=max_hops * (timeout_per_hop_sec * 3 + 1) + 10,
        )
        return True, (result.stdout + result.stderr)
    except subprocess.TimeoutExpired:
        return False, "경로 추적이 타임아웃되었습니다."
    except FileNotFoundError:
        return False, (
            "경로 추적 명령을 찾을 수 없습니다.\n"
            "Windows는 기본 포함(tracert), Linux는 설치 필요: sudo apt install traceroute"
        )


_IP_PATTERN = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")


def parse_traceroute(raw_output: str):
    """tracert/traceroute 원본 출력을 홉 단위 리스트로 파싱.
    반환: [{"hop": int, "ip": str, "status": "응답"/"무응답", "raw": 원본줄}, ...]
    """
    hops = []
    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d{1,2})\s", line)
        if not m:
            continue
        hop_num = int(m.group(1))
        ip_match = _IP_PATTERN.search(line)
        if ip_match:
            ip = ip_match.group(1)
            status = "응답"
        else:
            ip = "-"
            if "*" in line or "timed out" in line.lower() or "시간을 초과" in line:
                status = "무응답"
            else:
                status = "-"
        hops.append({"hop": hop_num, "ip": ip, "status": status, "raw": line})
    return hops


# ---------- GUI ----------

class ConnectionCheckerApp:
    COLUMNS = ("name", "host", "port", "status", "latency", "checked_at")

    def __init__(self, root):
        self.root = root
        self.root.title("범용 포트 연결 확인 도구")
        self.root.geometry("900x600")

        self.running = False
        self.result_queue = queue.Queue()

        self._build_ui()
        self._poll_queue()
        self._refresh_local_ip()

    # ---- UI ----
    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="내 PC IP:").pack(side="left")
        self.my_ip_var = tk.StringVar(value="확인 중...")
        ttk.Label(top, textvariable=self.my_ip_var, font=("", 10, "bold")).pack(side="left", padx=(4, 12))
        ttk.Button(top, text="새로고침", command=self._refresh_local_ip).pack(side="left")

        # 입력 영역
        input_frame = ttk.LabelFrame(self.root, text="확인할 구간 추가")
        input_frame.pack(fill="x", **pad)

        ttk.Label(input_frame, text="이름(설명):").grid(row=0, column=0, sticky="w", **pad)
        self.name_var = tk.StringVar()
        ttk.Entry(input_frame, textvariable=self.name_var, width=25).grid(row=0, column=1, **pad)

        ttk.Label(input_frame, text="상대 호스트(IP):").grid(row=0, column=2, sticky="w", **pad)
        self.host_var = tk.StringVar()
        ttk.Entry(input_frame, textvariable=self.host_var, width=20).grid(row=0, column=3, **pad)

        ttk.Label(input_frame, text="포트:").grid(row=0, column=4, sticky="w", **pad)
        self.port_var = tk.StringVar()
        ttk.Entry(input_frame, textvariable=self.port_var, width=10).grid(row=0, column=5, **pad)

        ttk.Button(input_frame, text="+ 목록에 추가", command=self._add_row).grid(row=0, column=6, **pad)
        self.root.bind("<Return>", lambda e: self._add_row())

        # 버튼 영역
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", **pad)
        ttk.Button(btn_frame, text="▶ 전체 테스트", command=self._on_test_all).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="선택 항목만 테스트", command=self._on_test_selected).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="선택 항목 삭제", command=self._remove_selected).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="경로 추적(선택 1개)", command=self._on_traceroute).pack(side="left", padx=4)
        ttk.Separator(btn_frame, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(btn_frame, text="목록 불러오기(CSV)", command=self._load_list).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="목록 저장(CSV)", command=self._save_list).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="결과 내보내기(CSV)", command=self._export_results).pack(side="left", padx=4)

        # 표
        table_frame = ttk.Frame(self.root)
        table_frame.pack(fill="both", expand=True, **pad)

        self.tree = ttk.Treeview(
            table_frame, columns=self.COLUMNS, show="headings", selectmode="extended"
        )
        headers = {
            "name": "이름(설명)", "host": "호스트", "port": "포트",
            "status": "상태", "latency": "응답시간(ms)", "checked_at": "확인시각",
        }
        widths = {"name": 200, "host": 150, "port": 70, "status": 90, "latency": 100, "checked_at": 130}
        for col in self.COLUMNS:
            self.tree.heading(col, text=headers[col])
            self.tree.column(col, width=widths[col], anchor="center" if col != "name" else "w")

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.tag_configure("OPEN", background="#e6f6e6", foreground="#0a7d2c")
        self.tree.tag_configure("CLOSED", background="#fdecec", foreground="#c0392b")
        self.tree.tag_configure("TIMEOUT", background="#fdecec", foreground="#c0392b")
        self.tree.tag_configure("ERROR", background="#fdecec", foreground="#c0392b")
        self.tree.tag_configure("PENDING", background="#ffffff", foreground="#888888")

        # 요약 + 상태바
        self.summary_var = tk.StringVar(value="등록된 구간이 없습니다. 위에서 추가해주세요.")
        ttk.Label(self.root, textvariable=self.summary_var, font=("", 10, "bold")).pack(anchor="w", **pad)

        self.status_var = tk.StringVar(value="대기 중")
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w").pack(fill="x", side="bottom")

    # ---- 내 IP ----
    def _refresh_local_ip(self):
        try:
            self.my_ip_var.set(get_local_ip())
        except Exception as e:
            self.my_ip_var.set(f"확인 실패: {e}")

    # ---- 목록 관리 ----
    def _add_row(self):
        name = self.name_var.get().strip() or "-"
        host = self.host_var.get().strip()
        port_str = self.port_var.get().strip()

        if not host or not port_str:
            messagebox.showwarning("입력 필요", "호스트와 포트를 입력해주세요.")
            return
        try:
            port = int(port_str)
            if not (0 < port <= 65535):
                raise ValueError
        except ValueError:
            messagebox.showwarning("입력 오류", "포트는 1~65535 사이의 숫자여야 합니다.")
            return

        self.tree.insert("", "end", values=(name, host, port, "미확인", "-", "-"), tags=("PENDING",))
        self.name_var.set("")
        self.host_var.set("")
        self.port_var.set("")
        self._update_summary()

    def _remove_selected(self):
        for item in self.tree.selection():
            self.tree.delete(item)
        self._update_summary()

    # ---- 경로 추적(traceroute) ----
    def _on_traceroute(self):
        items = self.tree.selection()
        if not items:
            messagebox.showinfo("선택 없음", "경로를 추적할 항목을 하나 선택해주세요.")
            return
        if len(items) > 1:
            messagebox.showinfo("여러 개 선택됨", "경로 추적은 한 번에 한 항목만 가능합니다. 첫 번째 선택 항목으로 진행합니다.")
        item = items[0]
        vals = self.tree.item(item, "values")
        name, host = vals[0], vals[1]

        win = self._open_traceroute_window(name, host)
        t = threading.Thread(target=self._traceroute_worker, args=(host, win), daemon=True)
        t.start()

    def _open_traceroute_window(self, name, host):
        win = tk.Toplevel(self.root)
        win.title(f"경로 추적 - {name} ({host})")
        win.geometry("560x480")

        info_var = tk.StringVar(value=f"{host} 로 가는 경로를 확인 중입니다... (최대 30초 정도 걸릴 수 있습니다)")
        ttk.Label(win, textvariable=info_var, font=("", 10, "bold")).pack(anchor="w", padx=8, pady=6)

        columns = ("hop", "ip", "status")
        tree = ttk.Treeview(win, columns=columns, show="headings")
        tree.heading("hop", text="홉(hop)")
        tree.heading("ip", text="IP 주소")
        tree.heading("status", text="상태")
        tree.column("hop", width=60, anchor="center")
        tree.column("ip", width=180, anchor="center")
        tree.column("status", width=100, anchor="center")
        tree.pack(fill="both", expand=True, padx=8, pady=4)
        tree.tag_configure("응답", background="#e6f6e6", foreground="#0a7d2c")
        tree.tag_configure("무응답", background="#fdecec", foreground="#c0392b")

        raw_box = tk.Text(win, height=8, font=("Consolas", 9))
        raw_box.pack(fill="both", expand=False, padx=8, pady=(0, 8))

        btn_row = ttk.Frame(win)
        btn_row.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(
            btn_row, text="목록에 각 홉 추가 (Ping 확인용, 포트 999)",
            command=lambda: self._add_hops_to_list(tree)
        ).pack(side="left")

        win._info_var = info_var
        win._tree = tree
        win._raw_box = raw_box
        return win

    def _traceroute_worker(self, host, win):
        ok, raw_output = run_traceroute_raw(host)
        hops = parse_traceroute(raw_output) if ok else []
        self.root.after(0, lambda: self._render_traceroute_result(win, host, ok, raw_output, hops))

    def _render_traceroute_result(self, win, host, ok, raw_output, hops):
        if not win.winfo_exists():
            return
        if not ok:
            win._info_var.set(f"경로 추적 실패: {raw_output}")
            return
        if not hops:
            win._info_var.set(f"{host} 경로 추적 결과를 해석하지 못했습니다. 아래 원본 출력을 확인해주세요.")
        else:
            last_ip = hops[-1]["ip"]
            reached = (last_ip == host) or any(h["ip"] == host for h in hops)
            reach_msg = "목표까지 도달함" if reached else "목표 도달 여부 불확실 (마지막 홉이 목표 IP와 다름)"
            win._info_var.set(f"{host} 경로 추적 완료 · 총 {len(hops)}개 홉 · {reach_msg}")

        for h in hops:
            win._tree.insert("", "end", values=(h["hop"], h["ip"], h["status"]), tags=(h["status"],))

        win._raw_box.insert("1.0", raw_output)
        win._raw_box.configure(state="disabled")

    def _add_hops_to_list(self, hop_tree):
        """경로 추적에서 발견된 각 홉 IP를 메인 목록에 추가 (참고용, 포트는 임의값이므로 직접 수정 필요)"""
        added = 0
        for item in hop_tree.get_children():
            hop, ip, status = hop_tree.item(item, "values")
            if ip == "-" or not ip:
                continue
            self.tree.insert(
                "", "end",
                values=(f"홉 {hop}", ip, 999, "미확인", "-", "-"),
                tags=("PENDING",),
            )
            added += 1
        self._update_summary()
        if added:
            messagebox.showinfo(
                "추가 완료",
                f"{added}개 홉 IP를 목록에 추가했습니다.\n"
                f"포트는 임시로 999를 넣었으니, 실제 확인하려는 포트로 수정 후 테스트해주세요."
            )

    # ---- 테스트 실행 ----
    def _on_test_all(self):
        items = self.tree.get_children()
        if not items:
            messagebox.showinfo("목록 없음", "먼저 확인할 구간을 추가해주세요.")
            return
        self._run_tests(items)

    def _on_test_selected(self):
        items = self.tree.selection()
        if not items:
            messagebox.showinfo("선택 없음", "테스트할 항목을 선택해주세요.")
            return
        self._run_tests(items)

    def _run_tests(self, items):
        if self.running:
            messagebox.showinfo("실행 중", "이미 테스트가 진행 중입니다.")
            return
        self.running = True
        self.status_var.set(f"{len(items)}개 구간 테스트 중...")
        for item in items:
            vals = list(self.tree.item(item, "values"))
            vals[3] = "확인중"
            self.tree.item(item, values=vals, tags=("PENDING",))

        t = threading.Thread(target=self._test_worker, args=(list(items),), daemon=True)
        t.start()

    def _test_worker(self, items):
        threads = []
        for item in items:
            th = threading.Thread(target=self._test_one, args=(item,), daemon=True)
            threads.append(th)
            th.start()
        for th in threads:
            th.join()
        self.result_queue.put(("__DONE__", None))

    def _test_one(self, item):
        vals = self.tree.item(item, "values")
        name, host, port = vals[0], vals[1], int(vals[2])
        status, latency, err = test_tcp_port(host, port)
        checked_at = datetime.now().strftime("%H:%M:%S")
        latency_display = f"{latency}" if latency is not None else "-"
        self.result_queue.put((item, (name, host, port, status, latency_display, checked_at)))

    def _poll_queue(self):
        try:
            while True:
                item, data = self.result_queue.get_nowait()
                if item == "__DONE__":
                    self.running = False
                    self.status_var.set("완료")
                    self._update_summary()
                    continue
                name, host, port, status, latency_display, checked_at = data
                self.tree.item(item, values=(name, host, port, status, latency_display, checked_at), tags=(status,))
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _update_summary(self):
        items = self.tree.get_children()
        total = len(items)
        if total == 0:
            self.summary_var.set("등록된 구간이 없습니다. 위에서 추가해주세요.")
            return
        open_count = sum(1 for i in items if self.tree.item(i, "values")[3] == "OPEN")
        fail_count = sum(
            1 for i in items if self.tree.item(i, "values")[3] in ("CLOSED", "TIMEOUT", "ERROR")
        )
        self.summary_var.set(f"총 {total}개 구간  |  연결됨(OPEN): {open_count}개  |  실패: {fail_count}개")

    # ---- CSV 저장/불러오기 ----
    def _save_list(self):
        items = self.tree.get_children()
        if not items:
            messagebox.showinfo("목록 없음", "저장할 항목이 없습니다.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV 파일", "*.csv")], title="목록 저장"
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["name", "host", "port"])
            for i in items:
                v = self.tree.item(i, "values")
                writer.writerow([v[0], v[1], v[2]])
        messagebox.showinfo("저장 완료", f"목록을 저장했습니다:\n{path}")

    def _load_list(self):
        path = filedialog.askopenfilename(filetypes=[("CSV 파일", "*.csv")], title="목록 불러오기")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                count = 0
                for row in reader:
                    name = row.get("name", "-")
                    host = row.get("host", "").strip()
                    port = row.get("port", "").strip()
                    if not host or not port:
                        continue
                    self.tree.insert("", "end", values=(name, host, port, "미확인", "-", "-"), tags=("PENDING",))
                    count += 1
            self._update_summary()
            messagebox.showinfo("불러오기 완료", f"{count}개 항목을 불러왔습니다.")
        except Exception as e:
            messagebox.showerror("오류", f"파일을 불러오는 중 오류가 발생했습니다:\n{e}")

    def _export_results(self):
        items = self.tree.get_children()
        if not items:
            messagebox.showinfo("목록 없음", "내보낼 결과가 없습니다.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV 파일", "*.csv")], title="결과 내보내기"
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["이름", "호스트", "포트", "상태", "응답시간(ms)", "확인시각"])
            for i in items:
                writer.writerow(self.tree.item(i, "values"))
        messagebox.showinfo("내보내기 완료", f"결과를 저장했습니다:\n{path}")


def main():
    root = tk.Tk()
    app = ConnectionCheckerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
