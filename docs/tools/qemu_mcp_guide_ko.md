# QEMU MCP 에이전트 디버깅 가이드 (qemu-mcp)

> 기준일: 2026-08-28
>
> 문서 상태: 운영 가이드 (외부 호스트 도구의 P0 편의 도입 문서)
>
> 도입 상태: `CURRENT` (호스트 편의 도구로서; 검증 증거 경로가 아님)
>
> 검증 증거 경로: 여전히 `tools/testkit/aios-testkit.py` lane만 유효
>
> Codex 등록 상태: 미등록·미노출 확인(2026-08-28 현재 호스트/세션), 절차만 문서화

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
- **baseline과 정규 lane artifact를 바꾸지 않는다.** 외부 서버의 VM/로그는
  임시 디렉터리에 생성된다. §7의 testkit 보조 명령은 그 진단 복사본만 새
  `kernel/build/qemu-mcp-diagnostic/<run-id>/`에 저장하며 기존 `serial_output.log`,
  `shell-smoke`, inventory, baseline을 덮어쓰지 않는다.
- **외부 서버 코드를 저장소에 들여오지 않는다.** 외부 설치 도구는 repo 밖에 둔다
  (Linux substrate `code_import=0`와 같은 공급망 정신). 버전은 고정해 쓰고
  갱신 시 별도 확인 후 이 문서의 검증 기록을 갱신한다.
- **변조성 동작은 신중하게.** 스냅샷 저장/복원, raw QMP, 강제 종료는 관찰·복구
  목적으로만 사용한다. K5 authorize 없는 apply가 아니며, 게스트 상태를 바꾼
  결과를 지원 증거로 표현하지 않는다.

## 2. 설치

요구사항: Python 3.10+, PATH 또는 표준 위치의 QEMU.

```powershell
# Windows / 공통
py -3 -m pip install --user "git+https://github.com/0xmortuex/qemu-mcp@037e7c8b7a6008db0874987edfdd41cc58896b78"
# 설치 위치(Windows): %APPDATA%\Python\Python311\Scripts\qemu-mcp.exe (PATH 외부일 수 있음)
```

Windows에서 QEMU는 `C:\Program Files\qemu`를 자동 탐색한다. 다른 위치면
환경변수 `QEMU_DIR`로 지정한다.

## 3. 클라이언트 등록 (stdio transport)

클라이언트마다 설정 저장소와 형식이 다르다. 아래 형식을 서로 바꿔 쓰지 않는다.
6절의 `CURRENT` 검증 기록은 qemu-mcp 서버를 직접 stdio JSON-RPC로 실행한 결과이며,
모든 클라이언트에 등록됐다는 증거가 아니다.

### 3.1 Claude Code

```powershell
claude mcp add qemu -- <qemu-mcp.exe 전체 경로>
```

이 명령은 Claude Code의 CLI 등록 경로다. Claude Code 설정 형식을 Codex 설정으로
재사용하지 않는다.

### 3.2 Codex

Codex는 generic `mcpServers` JSON이 아니라
[공식 Codex MCP 설정](https://developers.openai.com/codex/mcp)을 따른다. 사용자 범위
`~/.codex/config.toml` 또는 신뢰된 프로젝트의 `.codex/config.toml`에 stdio 서버를
둘 수 있고, CLI 등록 형식은 다음과 같다.

```powershell
codex mcp add qemu -- "C:\Users\<you>\AppData\Roaming\Python\Python311\Scripts\qemu-mcp.exe"
```

동등한 TOML 표면은 다음과 같다. Windows 경로의 backslash를 그대로 유지하려고
TOML literal string을 사용한다.

```toml
[mcp_servers.qemu]
command = 'C:\Users\<you>\AppData\Roaming\Python\Python311\Scripts\qemu-mcp.exe'
args = []
```

이 외부 호스트 편의 도구에는 사용자 범위 설정을 우선한다. 이 가이드를 갱신하는
작업은 저장소 `.codex/config.toml`이나 사용자 config를 자동 생성·변경하지 않는다.
2026-08-28 현재 이 호스트의 사용자 config에는 `[mcp_servers.qemu]`가 없고, 저장소의
trusted-project `.codex/config.toml`도 없으며, 현재 Codex 세션에도 `qemu_*` 도구가
노출되지 않았다. 따라서 Codex 등록은 현재 미완료다. 향후 등록 뒤에는 클라이언트를
재시작하고 tool namespace와 실제 stdio 호출을 다시 확인해야 한다. 그 확인을 마쳐도
qemu-mcp 출력은 계속 진단 전용이다.

### 3.3 generic JSON 설정 클라이언트

아래는 `mcpServers`를 실제 설정 계약으로 쓰는 클라이언트에만 적용한다. 클라이언트별
공식 문서에서 키와 설정 파일 위치를 먼저 확인한다.

```json
{
  "mcpServers": {
    "qemu": { "command": "C:\\Users\\<you>\\AppData\\Roaming\\Python\\Python311\\Scripts\\qemu-mcp.exe" }
  }
}
```

## 4. 도구 요약과 AIOS 사용 노트

| 도구 | 용도 | AIOS 노트 |
|---|---|---|
| `qemu_boot` | ISO/커널 헤드리스 부팅 | 고유 `name`이 필수다. `iso=kernel/build/aios-kernel.iso`, `arch="x86_64"`. 응답에 serial log 파일 경로가 나온다 |
| `qemu_wait_serial` | 문자열이 나올 때까지 대기 | `"=== AIOS Kernel Ready ==="`는 부팅 ready checkpoint다. 명령 전송 전에는 `"aios# "` 프롬프트까지 기다린다. 전체 과거 로그도 검색하므로 새 명령 응답의 신선함은 따로 확인한다 |
| `qemu_wait_screen` | framebuffer가 안정될 때까지 대기 | 연속된 동일 프레임을 관측할 뿐이다. AIOS에는 serial이 있으므로 `qemu_wait_serial`을 우선하고, `SETTLED`를 부팅 PASS로 해석하지 않는다 |
| `qemu_serial` / `qemu_serial_send` | COM1 읽기/쓰기 | 셸 Enter는 `\n`과 `\r` 모두 허용(`shell.c`). `[STATE] topic key=value` 단일 행 응답 |
| `qemu_screenshot` | VGA 프레임버퍼 PNG | VM이 살아 있는 동안의 화면 확인용이다. 이미 종료한 VM에는 사용할 수 없다 |
| `qemu_type` / `qemu_key` / `qemu_mouse` | 키보드/포인터 주입 | PS/2 경로 디버깅용 |
| `qemu_snapshot_save` / `qemu_snapshot_load` | savevm/loadvm | qcow2 디스크 필요. 실패를 정직 보고함 |
| `qemu_qmp` | raw QMP | **command는 문자열**(예: `"query-status"`). JSON 객체를 넣으면 실패한다 |
| `qemu_list`, `qemu_stop` | 세션 목록/종료 | `list`는 종료 VM의 로그 디렉터리를 회수하고, `stop`은 해당 디렉터리를 삭제한다. 먼저 로그를 복사한다 |

설치 commit `037e7c8b7a6008db0874987edfdd41cc58896b78`의 주의점:

- `qemu_wait_serial`의 정상 응답 첫 행은 `FOUND`, `TIMEOUT`, `VM EXITED` 중 하나다.
  문자열 검색이 생존 확인보다 먼저이며 검색 범위는 전체 serial 이력이다. 이미 죽은
  VM으로 호출을 시작하면 `VM EXITED` 문자열 대신 MCP tool error가 올 수 있다.
- `qemu_serial`은 기본 최근 50행이며, 아직 회수되지 않은 종료 VM도 읽는다.
  전체 부팅 로그나 새 명령 응답을 보증하는 API는 아니다.
- `qemu_stop(force=true)`는 ACPI 대기를 생략하고 QMP `quit`을 먼저 보낸 뒤 필요하면
  kill한다. 이 서버의 최종 kill 분기에는 별도 wait가 없어 stop 응답만으로 OS process
  종료를 확정하지 않는다. `qemu_list`의 빈 registry도 process 종료 증거가 아니다.
- MCP 서버의 stdin EOF/프로세스 종료에는 실행 중 VM을 자동 정리하는 보장이 없다.
  수동 사용자는 자신이 만든 정확한 VM을 명시적으로 정리하고, 자동 helper는 §7처럼
  시작 전부터 소유 프로세스 경계를 준비해야 한다.

위험도 분류(프로젝트 NodeBit 정신의 툴레이어 적용): `boot/wait/read/screenshot`
= 관찰(allow), `serial_send/type/key` = 셸 계약 내 조작(observe),
`snapshot/qmp/stop(force)` = 변조 가능(risky, 사용 후 재현 가능한 정규 lane으로
재확인).

## 5. 권장 워크플로 (대화형 bring-up 디버깅)

```text
1. make iso (또는 build-windows.ps1 -Target iso) 로 ISO 생성
2. name="aios-diagnostic-<run-id>"로 이번 실행의 고유 소유 이름 결정
3. qemu_boot(name=name, iso=..., arch="x86_64")
4. qemu_wait_serial(name=name, text="aios# ", timeout_s=90)
5. qemu_serial_send(name=name, text="state resource\n")  # 또는 ping/pressure/binding...
6. qemu_serial(name=name)로 [STATE] 응답 확인 (또는 serial log 파일 전체 read)
7. 원인 분석 필요 시 qemu_screenshot(name=name),
   qemu_qmp(name=name, command="query-status")
8. 필요한 serial.log 전체, qemu.log, screenshot을 진단 위치에 복사
9. qemu_stop(name=name, force=true) 후 소유 QEMU process 종료 확인
10. 마지막으로 qemu_list()의 registry 정리 확인
```

초기 부팅 마커 전체(`[BOOT] ... [SHELL] Interactive shell started`)가 필요하면
`qemu_serial`의 최근분 창 대신 부팅 응답이 알려준 **serial log 파일 전체**를 읽는다.
이미 종료한 VM에서도 로그 복사를 `qemu_list`보다 먼저 한다. 목록 조회가 종료 VM을
회수하면서 원본 로그를 지울 수 있기 때문이다. 종료 VM의 screenshot은 기대하지 않는다.

## 6. 검증 기록

- 2026-08-23, Windows 11 + Python 3.11.9 + QEMU (C:\Program Files\qemu),
  qemu-mcp 0.1.0 (resolved commit
  `037e7c8b7a6008db0874987edfdd41cc58896b78`), 대상 ISO:
  `kernel/build/aios-kernel.iso` (v0.2.0-beta.6 시절 빌드).
- stdio JSON-RPC 클라이언트로 initialize → tools/list(14개) → boot →
  wait_serial(READY 발견) → serial log 파일에서 5개 부트 마커 전부 확인 →
  `ping\n` 송신 후 `pong` 수신(셸 왕복) →
  `qemu_qmp(name=<verified-name>, command="query-status")` = running →
  screenshot OK → stop OK.
- 미검증(이 환경): qcow2 디스크를 사용하지 않아 snapshot save/load 미검증,
  mouse/type, 비-x86 arch.
- 2026-08-28 현재 설치 상태 재확인: `qemu-mcp 0.1.0` 실행 파일은
  `%APPDATA%\Python\Python311\Scripts\qemu-mcp.exe`에 있고 Claude Code의 `qemu`
  서버 health는 `Connected`다. Codex 쪽은 위 §3.2와 같이 아직 미등록·미노출이다.

## 7. testkit 진단 보조 명령 — `PARTIAL`

2026-09-02 현재 CLI와 전용 stdio helper를 구현했고, Windows에서 실제 VM의
prompt → 새 ping 응답 → QMP running → artifact 보존 → 소유 프로세스 정리를
확인했다. Linux E2E와 전체 실패 조합의 실제 VM 검증은 남아 있어 `PARTIAL`이다.
§6의 과거 수동 진단 기록과 이 새 helper의 E2E 기록은 구분한다. 이 명령은 qemu-mcp를
정규 kernel verdict로 편입하지 않는다.

```powershell
py -3 tools/testkit/aios-testkit.py qemu-mcp-diagnostic `
  --mcp-server "C:\path\to\qemu-mcp.exe" `
  --skip-build `
  --timeout 90
```

`--mcp-server`에는 shell 인자 없는 단일 실행 파일의 absolute path를 준다. 이 명령은
Claude/Codex 설정을 읽거나 등록·수정하지 않고, 실행마다 새 전용 qemu-mcp 프로세스의
UTF-8 newline-delimited stdio JSON-RPC에 직접 연결한다. `minimal/default` 한
프로파일만 사용한다. 기본 동작은 ISO를 빌드하며, `--skip-build`는 기존 ISO를 재사용하되
그 빌드 신선함을 `unknown`으로 기록한다. `--timeout`은 1–600 정수 초이며 기본 60초다.

preflight는 MCP protocol `2025-11-25` initialize 응답과 initialized notification,
빈 전용 registry, 다음 최소 도구 집합의 입력 schema를 확인한다.
`qemu_boot`, `qemu_wait_serial`, `qemu_serial`, `qemu_serial_send`, `qemu_qmp`,
`qemu_screenshot`, `qemu_stop`, `qemu_list`. 전체 도구 개수나 빈 server-version
문자열은 호환성 판정으로 사용하지 않는다. request ID와 response ID를
정확히 결속하고 stdout의 JSON-RPC 행과 stderr 진단을 분리한다.

boot 요청은 다음처럼 고정한다. `iso`는 resolve된 absolute path여야 하고
사용자가 임의 `extra_args`를 주입하는 옵션은 열지 않는다.

```json
{
  "name": "aios-diagnostic-<run-id>",
  "iso": "<absolute ISO path>",
  "arch": "x86_64",
  "memory_mb": 256,
  "extra_args": "-nic none -no-reboot",
  "qmp_connect_timeout_s": 20,
  "qmp_read_timeout_s": 15
}
```

MCP 호출은 JSON-RPC 최상위 `error`와 `result.isError=true`를 모두 실패로 처리한다.
text 도구는 단일 text content를 읽고, `structuredContent.result`도 있으면 두 값이
같은지 확인한다. screenshot은 단일 `image/png` content의 bounded base64를 검증한다.
`qemu_wait_serial`은 응답 첫 행의 case-sensitive `FOUND`, `TIMEOUT`, `VM EXITED`를
판정하며 tail에 과거 marker가 포함돼도 첫 행이 `TIMEOUT`이면 성공으로 바꾸지 않는다.
`ping\n` 전송 직전 serial byte offset을 기록하고, 그 뒤의 정확한 `[STATE] pong ticks=`
단일 행을 별도로 검사하므로 wait의 과거 이력 검색만으로 왕복 성공을 주장하지 않는다.
행 끝은 LF/CRLF와 현재 producer의 CRCRLF를 허용하지만, 잘린 행이나 앞쪽 공백,
중복 pong, uint64 범위 밖 ticks는 허용하지 않는다.
`query-status` 결과는 JSON 문자열을 다시 parse해 `status=="running"`을 관찰한다.

각 RPC에는 서버 내부 timeout과 별개인 host deadline을 둔다. `--timeout`은 전체 명령의
wall-clock 상한이 아니다. 빌드, 각 관찰 단계, 정리의 상한이 별도로 적용된다.

| 단계 | 상한 |
|---|---|
| initialize / tools-list 각 요청 / registry list | 각각 10초 |
| boot | host 65초; 내부 QMP connect 20초, read 15초 |
| shell prompt wait | 서버 `--timeout`초, host는 그 값 + 5초 |
| ping send / pong wait | send host 20초; pong 서버 15초, host 20초 |
| serial tail fallback / QMP / screenshot / stop | 각각 host 25초 |
| 소유 QEMU 종료 / MCP 서버 EOF 종료 | 각각 5초 |
| 소유 프로세스 경계의 drain / 강제 정리 | 각각 최대 10초 |

```text
exact owned name와 임시 루트 생성 -> 실행 전 소유 프로세스 경계 구성
  -> initialize / tools-list / 빈 registry preflight
  -> boot(name) -> wait_serial(name, "aios# ")
  -> serial byte offset -> serial_send(name, "ping\n")
  -> wait_serial(name, "[STATE] pong ticks=") -> 새 행 확인
  -> query-status(name)
  -> pre-stop serial/qemu.log와 선택적 실패 screenshot 복사
  -> qemu_stop(name, force=true) -> 해당 registry entry 제거 확인
  -> 소유 QEMU process 종료 확인 -> MCP 서버 종료 / 소유 경계 drain 확인
```

설치된 qemu-mcp는 server EOF/종료 때 실행 중 VM을 자동 정리하는 보장이 없고,
`qemu_stop`은 임시 workdir을 즉시 삭제한다. `qemu_list`도 종료 VM의 workdir을
회수하므로, boot 이후 artifact 복사는 stop/list보다 반드시 먼저 온다. 모든 실패
경로는 자신이 만든 정확한 VM/session만 `finally`에서 정리한다. Windows에서는
launcher를 suspended 상태로 만들고 kill-on-close Job Object에 할당한 뒤 primary
thread를 재개한다. 따라서 Python/QEMU 자식이 생기기 전에 경계가 성립한다. POSIX는
전용 process group을 사용한다. 서버 EOF 후에도 자식 종료를 bounded wait로 확인한다.
Job의 자식 프로세스 포함과 kill-on-close 의미는
[Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)를
따른다. 이 프로세스 수명 경계는 파일/네트워크 보안 sandbox를 뜻하지 않는다.
Windows no-VM 실측에서 숨겨진 `conhost.exe`는 서버 exit 직후 약 0.05초 늦게 종료했으므로
즉시 한 번의 process count만으로 cleanup 실패를 판정하지 않는다.
membership와 종료는 구분한다. Windows에서는 소유권을 확인한 process handle을
보존하고 signaled 상태로 종료를 확인하며, 전체 Job의 active count 0도 별도로 요구한다.
정상 예외/키보드 인터럽트는 정리하지만 호스트 자체가 suspended 생성과 Job 할당
사이에서 강제 종료되면 suspended launcher가 남을 수 있다. 그 구간에는 아직
Python/QEMU가 실행되지 않았으므로 VM 누수와는 구분한다.

`qemu_list`에서 이름이 사라진 사실은 서버 내부 registry 정리만 증명하므로 OS process
종료는 별도로 확인한다. 응답 전에 boot가 실패해 QEMU PID를 모르는 경우에도 소유
Job/process group으로 정리한다. 임의의 `qemu` 또는 Python 프로세스를 광범위하게
종료하지 않는다. 강제 경계 정리로 자식을 회수했어도 원래 stop/정리 실패를 성공으로
승격하지 않는다.

전체 serial은 기본 50행 tail인 `qemu_serial`만으로 만들지 않는다. boot 응답의
text result에서 `Serial log: ...` 경로를 얻고, 그 경로가 이번 실행의 시스템 temp
루트 `aios-qemu-mcp-<run-id>-*` 아래 `qemu-mcp-<owned-name>-*/serial.log`인지,
link/reparse 우회가 없는지 검증한 뒤 stop 전에 전체 파일을 복사한다. 그 순간 파일이
안정됐으면 `serial_snapshot_complete=true`, 범위는
`serial_capture_scope="full-source-through-pre-stop-snapshot"`이다. 이는 **stop 전
snapshot까지**의 완전성일 뿐이며 `serial_complete_through_termination=false`는
그대로 유지한다. 종료 과정의 마지막 로그까지 보존했다는 주장이 아니다.

원본 경로를 얻지 못했을 때 `qemu_serial` tail은 비상 진단으로만 저장하며 snapshot
완전성을 주장하지 않는다. `qemu.log`도 가능하면 함께 복사한다. 실패 screenshot은
boot 응답을 받았고 MCP 연결을 사용할 수 있을 때만 선택적으로 보존한다.
`VM_EXITED`에는 screenshot을 시도하지 않으며, screenshot 실패는 원래 진단 결과와
별도 reason으로 남긴다. PNG가 없다고 원래 timeout을 성공으로 바꾸지 않는다.

산출물은 `kernel/build/qemu-mcp-diagnostic/<run-id>/` 아래의 `summary.json`,
`serial.log`, `qemu.log`, `mcp-transcript.jsonl`, `stderr.log`, 선택적 `failure.png`다.
`summary.json`의 `artifacts`는 각 로그와 선택적 PNG의 byte 수와 SHA-256을 기록한다.
상위 결과는
`PASS`가 아니라 `OBSERVED`, `TIMEOUT`, `VM_EXITED`, `INFRA_ERROR`,
`CLEANUP_ERROR`, `ABORTED`만 사용하고 항상 `diagnostic_only=true`,
`authoritative=false`를 기록한다. 이 명령은 `all`, CI, inventory, baseline에 연결하지
않으며, 실행 뒤 정규 판정이 필요하면 별도로 strict kernel/shell lane을 실행한다.

`summary.json`의 최소 상위 필드는 `schema_version=1`,
`kind="aios.qemu_mcp_diagnostic"`, `diagnostic_only=true`, `authoritative=false`,
`outcome`, `reasons[]`, `request`, `provenance`, `mcp`, `vm`, `observations`,
`termination`, `artifacts`로 고정한다. 외부 프로세스를 시작하기 전에 새 run 디렉터리에
초기 실패 상태 summary와 빈 serial을 만들어 과거 `OBSERVED` artifact가 launch
failure 뒤에 남지 않게 한다. cleanup timeout 또는 소유 process 종료 미확인은 이전
`OBSERVED` 후보보다 우선해 `CLEANUP_ERROR`로 남긴다.

provenance에는 실행 파일/ISO의 경로·byte 수·SHA-256, git HEAD/dirty 상태, Python,
helper/CLI source hash, 협상 protocol과 최소 도구 schema fingerprint를 기록한다.
`host_packages`는 **testkit을
실행한 Python**에서 관찰한 설치 metadata이며 외부 `--mcp-server`의 실제 interpreter에
결속되지 않는다. `qemu_host_candidate`도 host 탐색 후보일 뿐이고
`qemu_host_candidate_is_vm_identity=false`다. 외부 서버는 `QEMU_DIR` 등 다른 탐색
경로를 사용할 수 있어 이 후보가 실제 VM executable과 같다고 주장하지 않는다.
파일 hash는 관찰한 파일의 식별 정보이지 transitive package나 runtime 전체의 공급망
고정 증거가 아니다.

CLI 정상 반환은 `OBSERVED`의 exit 0이며, 이것도 커널 PASS가 아니다. `TIMEOUT`과
`VM_EXITED`는 exit 1, `INFRA_ERROR`, `CLEANUP_ERROR`, 일반 `ABORTED`는 exit 2다.
새 pong 행 계약 불일치나 QMP non-running 관찰은 `ABORTED`로 중단한다. 사용자
interrupt는 가능한 summary/정리를 수행한 뒤 `KeyboardInterrupt`를 다시 전달하므로
shell/OS의 interrupt exit 규칙을 따른다(`ABORTED`, 또는 정리 실패 시
`CLEANUP_ERROR`). CLI 인자 parse 실패나 helper 진입 전 BuildLock 오류는 이 진단
summary 계약 바깥이다.

이 subcommand는 `--skip-build`에서도 기존 testkit `BuildLock`에 참여해 다른 lane의 ISO
재생성과 겹치지 않게 한다. 자체 run 디렉터리만 사용하며 기존 `serial_output.log`와
`shell-smoke` artifact를 덮어쓰지 않는다.

구현 표면:

- `tools/testkit/lib/qemu_mcp_diagnostic.py`
- `tools/testkit/tests/test_qemu_mcp_diagnostic.py`
- `tools/testkit/aios-testkit.py`의 `qemu-mcp-diagnostic` subcommand

실제 helper 확인 기록(2026-09-02, Windows + Python 3.11.9):

- no-VM initialize/tools-list/list preflight: protocol `2025-11-25`, 빈 registry,
  14개 노출 도구와 최소 집합 schema fingerprint
  `4311350eae98d9766e06cb0ac645fe84986b7d50c819e6a3935823303ca910ae` 확인.
- run `20260902t032416z-3904-67d76ba3`: `OBSERVED`, prompt와 fresh pong 확인,
  QMP `running`, pre-stop serial snapshot 완전, `CLEAN`, server exit 0,
  registry/소유 QEMU/Job/readers/temp 정리 확인. 강제 containment 회복은 사용하지 않았다.
  4개 로그 artifact의 hash를 재확인했다. 이는 helper의 진단 E2E이며 kernel PASS가 아니다.
- 위 실행은 `--skip-build` ISO 931,840 bytes,
  SHA-256 `657ca73ef52713f899783098ce7edd5a653b659901260d67e71502fcb45fab87`을
  재사용했다. `freshness=unknown`, `built_from_current_head=false`이며 현재 HEAD를 새로
  빌드해 통과했다는 증거가 아니다.
- 후속 run `20260902t032805z-23836-cbfe372c`: `--skip-build` 없이 ISO 빌드부터
  같은 관찰/정리 순서를 실행해 `OBSERVED`/`CLEAN`을 확인했다. 4개 로그 artifact의
  byte 수/hash가 일치하고 소유 QEMU PID와 원본 임시 디렉터리가 사라졌음을 재확인했다.
  ISO는 931,840 bytes, SHA-256
  `3419f10345c978bc781479e0f1f8695ac4dbafc95c0b80e897c22fd72d215c62`,
  `freshness=built-this-run`이다. 작업 트리가 dirty여서 `built_from_current_head=null`이며
특정 clean commit에 대한 빌드 증명은 아니다. 이것 역시 정규 strict lane verdict를
  대체하지 않는다.
- 최종 helper source-hash 보강 뒤 run `20260902t033015z-13524-9601270e`에서도
  같은 ISO를 재사용해 `OBSERVED`/`CLEAN`을 확인했다. host 단위 테스트는 기존 92개와
  새 진단 65개, 총 157개가 통과했다. 이 테스트들은 기존 CI의 unittest discovery에
  포함되지만 실제 `qemu-mcp-diagnostic` 명령/VM은 CI에 연결하지 않았다.

## 8. 관련 정본

- 검증 판정 정본: [verification_tooling_evolution_design_ko.md](verification_tooling_evolution_design_ko.md)
- testkit 운영: [testkit_guide_ko.md](testkit_guide_ko.md)
- 드라이브 bring-up 런북: `.agents/skills/aios-driver-bringup-qemu/SKILL.md`
- W축 콘솔(세션 중계 UI) 장기 방향:
  [browser_console_and_runtime_engine_roadmap_ko.md](../os/browser_console_and_runtime_engine_roadmap_ko.md)
