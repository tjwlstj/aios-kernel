# QEMU MCP 에이전트 디버깅 가이드 (qemu-mcp)

> 기준일: 2026-08-23
>
> 문서 상태: 운영 가이드 (외부 호스트 도구의 P0 편의 도입 문서)
>
> 도입 상태: `CURRENT` (호스트 편의 도구로서; 검증 증거 경로가 아님)
>
> 검증 증거 경로: 여전히 `tools/testkit/aios-testkit.py` lane만 유효

[qemu-mcp](https://github.com/0xmortuex/qemu-mcp)는 AI 에이전트(Claude Code,
opencode, Codex 등 MCP 클라이언트)에게 헤드리스 QEMU VM을 직접 구동·관찰·조작할
수 있는 [Model Context Protocol](https://modelcontextprotocol.io) 서버다.
게스트 협조(SSH, guest agent)가 필요 없으므로 베어메탈 커널인 AIOS에 적합하다.

## 1. 경계 원칙 (반드시 먼저 읽기)

- **진단 전용이다.** 이 경로의 부팅 성공, 마커 관찰, 스크린샷은 개발 중
  대화형 디버깅 입력일 뿐이다. PASS/FAIL 판정, marker 계약 확인, baseline
  비교는 반드시 정규 lane(`aios-testkit.py kernel|shell|boot-matrix --strict`)으로만
  수행하고 그 결과만 보고한다. `$aios-verification-tooling-guardian`의
  evidence/verdict 분리 규칙이 그대로 적용된다.
- **baseline과 artifact를 만지지 않는다.** 이 도구의 VM/로그는
  `%TEMP%\qemu-mcp-*` 아래 생성되며 `kernel/build/`의 testkit artifact와 무관하다.
- **저장소에 코드를 들여오지 않는다.** 외부 설치 도구는 repo 밖에 둔다
  (Linux substrate `code_import=0`와 같은 공급망 정신). 버전은 고정해 쓰고
  갱신 시 별도 확인 후 이 문서의 검증 기록을 갱신한다.
- **변조성 동작은 신중하게.** 스냅샷 저장/복원, raw QMP, 강제 종료는 관찰·복구
  목적으로만 사용한다. K5 authorize 없는 apply가 아니며, 게스트 상태를 바꾼
  결과를 지원 증거로 표현하지 않는다.

## 2. 설치

요구사항: Python 3.10+, PATH 또는 표준 위치의 QEMU.

```powershell
# Windows / 공통
py -3 -m pip install --user git+https://github.com/0xmortuex/qemu-mcp
# 설치 위치(Windows): %APPDATA%\Python\Python311\Scripts\qemu-mcp.exe (PATH 외부일 수 있음)
```

Windows에서 QEMU는 `C:\Program Files\qemu`를 자동 탐색한다. 다른 위치면
환경변수 `QEMU_DIR`로 지정한다.

## 3. 클라이언트 등록 (stdio transport)

```text
# Claude Code
claude mcp add qemu -- <qemu-mcp.exe 전체 경로>

# JSON 설정을 받는 클라이언트(opencode, Codex, Cursor 등)
{
  "mcpServers": {
    "qemu": { "command": "C:\\Users\\<you>\\AppData\\Roaming\\Python\\Python311\\Scripts\\qemu-mcp.exe" }
  }
}
```

## 4. 도구 요약과 AIOS 사용 노트

| 도구 | 용도 | AIOS 노트 |
|---|---|---|
| `qemu_boot` | ISO/커널 헤드리스 부팅 | `iso=kernel/build/aios-kernel.iso`, `arch="x86_64"`. 응답에 serial log 파일 경로가 나온다 |
| `qemu_wait_serial` | 문자열이 나올 때까지 대기 | AIOS 종료 마커는 `"=== AIOS Kernel Ready ==="` |
| `qemu_serial` / `qemu_serial_send` | COM1 읽기/쓰기 | 셸 Enter는 `\n`과 `\r` 모두 허용(`shell.c`). `[STATE] topic key=value` 단일 행 응답 |
| `qemu_screenshot` | VGA 프레임버퍼 PNG | 시리얼 사멸 직후 화면 확인용 |
| `qemu_type` / `qemu_key` / `qemu_mouse` | 키보드/포인터 주입 | PS/2 경로 디버깅용 |
| `qemu_snapshot_save/load` | savevm/loadvm | qcow2 디스크 필요. 실패를 정직 보고함 |
| `qemu_qmp` | raw QMP | **command는 문자열**(예: `"query-status"`). JSON 객체를 넣으면 실패한다 |
| `qemu_list`, `qemu_stop` | 세션 목록/종료 | hobby kernel은 ACPI 종료를 무시하므로 `force=true`가 실질적 |

위험도 분류(프로젝트 NodeBit 정신의 툴레이어 적용): `boot/wait/read/screenshot`
= 관찰(allow), `serial_send/type/key` = 셸 계약 내 조작(observe),
`snapshot/qmp/stop(force)` = 변조 가능(risky, 사용 후 재현 가능한 정규 lane으로
재확인).

## 5. 권장 워크플로 (대화형 bring-up 디버깅)

```text
1. make iso (또는 build-windows.ps1 -Target iso) 로 ISO 생성
2. qemu_boot(iso=..., arch="x86_64")
3. qemu_wait_serial(text="=== AIOS Kernel Ready ===", timeout_s=90)
4. qemu_serial_send(text="state resource\n")  # 또는 ping/pressure/binding...
5. qemu_serial 로 [STATE] 응답 확인 (또는 serial log 파일 전체 read)
6. 원인 분석 필요 시 qemu_screenshot, qemu_qmp("query-status")
7. qemu_stop(force=true)
```

초기 부팅 마커 전체(`[BOOT] ... [SHELL] Interactive shell started`)가 필요하면
`qemu_serial`의 최근분 창 대신 부팅 응답이 알려준 **serial log 파일 전체**를 읽는다.

## 6. 검증 기록

- 2026-08-23, Windows 11 + Python 3.11.9 + QEMU (C:\Program Files\qemu),
  qemu-mcp 0.1.0 (`git+...@main` 설치), 대상 ISO: `kernel/build/aios-kernel.iso`
  (v0.2.0-beta.6 시절 빌드).
- stdio JSON-RPC 클라이언트로 initialize → tools/list(14개) → boot →
  wait_serial(READY 발견) → serial log 파일에서 5개 부트 마커 전부 확인 →
  `ping\n` 송신 후 `pong` 수신(셸 왕복) → `qemu_qmp("query-status")` = running →
  screenshot OK → stop OK.
- 미검증(이 환경): snapshot save/load(qcow2 불필요), mouse/type, 비-x86 arch.

## 7. 관련 정본

- 검증 판정 정본: [verification_tooling_evolution_design_ko.md](verification_tooling_evolution_design_ko.md)
- testkit 운영: [testkit_guide_ko.md](testkit_guide_ko.md)
- 드라이브 bring-up 런북: `.agents/skills/aios-driver-bringup-qemu/SKILL.md`
- W축 콘솔(세션 중계 UI) 장기 방향:
  [browser_console_and_runtime_engine_roadmap_ko.md](../os/browser_console_and_runtime_engine_roadmap_ko.md)
