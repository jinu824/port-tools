# port-tools

간단한 TCP 포트/연결 점검용 GUI 도구 모음입니다. Python 표준 라이브러리(tkinter)만 사용하며 별도 패키지 설치 없이 실행됩니다.

## 구성

### 1. connection_checker_gui.py — 범용 포트 연결 확인 도구
- 확인하고 싶은 구간(이름/호스트/포트)을 목록에 등록
- 등록된 모든 구간을 한 번에(병렬) TCP 연결 테스트 → OPEN / CLOSED / TIMEOUT / ERROR
- 목록 CSV 저장/불러오기, 결과 CSV 내보내기
- 경로 추적(tracert/traceroute) 결과를 홉 단위 표로 확인, 홉 IP를 목록에 바로 추가 가능

### 2. port_listener_gui.py — 테스트용 포트 리스너
- 지정한 포트를 열어서(LISTEN) 접속을 대기
- 접속이 들어오면 상대 IP:Port와 시각을 로그로 표시하고 짧은 응답 메시지 전송
- 여러 포트 동시 개방 가능
- `connection_checker_gui.py`로 테스트할 상대(서버) 역할로 사용

## 실행 방법

```bash
python3 connection_checker_gui.py
python3 port_listener_gui.py
```

Python 3.8+ 필요, 추가 설치 불필요 (tkinter 표준 포함). Mac은 `brew install python-tk`, Linux는 `sudo apt install python3-tk` 필요할 수 있습니다.

## Windows exe로 빌드하기

```bash
py -3 -m pip install pyinstaller
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name PortConnectionChecker connection_checker_gui.py
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name PortListener port_listener_gui.py
```

## 주의사항

- 본인 소유이거나 접근 권한이 있는 네트워크/호스트에서만 사용하세요.
- `port_listener_gui.py` 실행 중에는 지정한 포트가 실제로 열려있는 상태가 됩니다. 테스트 후 반드시 종료하세요.
