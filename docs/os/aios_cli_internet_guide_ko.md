# AIOS CLI와 기본 인터넷 사용 가이드

> 개발 경계 — 2026-09-13: 환경 문맥·오류 안내에 이어 v0.10은 UUID Task의
> 접수·조회·결과·동일 CLI 소유 backend의 명시적 취소를 연결한다(`PARTIAL`).
> 버전·schema·source 수와 보존된 v0.8 실제 소비·v0.10 fixture의 범위는
> [환경 문맥 가이드](aios_space_context_guide_ko.md)가 소유한다. 실제 모델 Task의 한정 흐름은 PASS이며
> source 39개 운영 이미지 acceptance는 미완료다. 본문의 과거 계약·실행 기록·source
> 일치 주장은 각 보존 소스의 당시 범위이며 새 개발 소스의 검증으로 승계하지 않는다.
> 이번 문서 검토는 외부 자료 재조사나 runtime 재실행 판정이 아니다.

> 문서 역할: Linux-hosted AIOS 대화형 사용자 환경의 작업·운영 가이드
>
> 상위 정본: [Linux substrate 정책](linux_hosted_substrate_and_resource_policy_ko.md),
> [Kernel Room 관리 모델](../kernel-room/kernel_room_management_model_ko.md),
> [기존 부팅·하드웨어 가이드](aios_userspace_boot_hardware_guide_ko.md)
>
> 성숙도: H2-a 실행 환경은 `PARTIAL`; 실제 Linux 개발 VM에서 대화형 CLI와
> DNS·HTTP·인증서 검증 HTTPS를 로컬 검증했다(§6).
> 기본 및 현재 v0.7 모델 운영 이미지의 반복 부팅·실제 모델 질문은 `SUPPORTING/PARTIAL`로 별도 검증했다.
> 별도 장애 복사본의 recover·즉시 종료도 검증했으며 전체 bound H2/H3와 범용 설치·업데이트·복구는 후속이다.
>
> 문서 수명주기: 활성 / 공식 문서 검토일: 2026-09-03

## 1. 지금 유저스페이스로 이어가는 이유

이번 목표는 **AIOS 고유 화면과 프롬프트에서 상태를 보고, 이름을 해석하고,
HTTP·HTTPS 응답을 읽은 뒤 정상 종료할 수 있는 최소 사용자 환경**이다.
기존 가이드는 Linux-hosted userspace service를 기본 delivery 경로로 이미 정했다.
H1 contract/replay acceptance와 H2-a 실제 Linux 부팅·관측 증거가 있으므로,
네이티브 process/storage 전체 확장을 기다리지 않고 이 사용자 환경을 이어 만든다.

AIOS는 자체 관리 계약과 사용자 명령의 의미를 소유한다. `Room -> Cell -> Node ->
NodeBit`의 identity·generation·binding은 AIOS 정본에 남는다. Linux는 드라이버,
파일시스템, 네트워크 스택, 프로세스 실행을 제공하는 하드웨어 특권 커널이다.
이번 CLI는 그 위에서 실행되는 AIOS 프로그램이며, 독자적인 제품 의미를 배포판
설정이나 Linux PID/cgroup 이름으로 바꾸지 않는다.

`kernel/`의 별도 x86_64 AIOS 커널과 native ABI·검증은 계속 유지한다. 이번 작업은
Linux 배포판 fork, Linux kernel module, 네이티브 AIOS의 Linux `.ko` 직접 적재를
구현하지 않는다. 이 실행 경로에서 CPU 특권 모드의 커널이 Linux라는 사실은
`about`과 실행 증거로 확인할 수 있어야 한다.

분류는 H2 실행 기반을 보강하는 `SUPPORTING`이다. CLI가 존재하거나 인터넷 요청이
성공해도 canonical `AI_SERVICE`, Cell/Node binding, H3 reconciliation 또는 K5
authorize가 완성됐다고 표시하지 않는다. 시작·하드웨어 관측 source는 계속 `UNBOUND`다.
별도 MAIN source의 명시적 결속과 Cell 1 관리 전이는 아래 연결된 가이드의 `PARTIAL`
계약을 따르며, CLI 자체의 관측 source와 구분한다.

## 2. 실행하고 사용하기

모델 포함 운영 이미지가 준비된 Windows 호스트에서는 저장소 루트에서
[Start-AiosImage.cmd](../../tools/hosted/Start-AiosImage.cmd)를 다음과 같이 실행한다.

```powershell
.\tools\hosted\Start-AiosImage.cmd -Agent
```

`-Agent`는 저장한 모델 기본 선택을 사용하며 이 문서의 로컬 검증 호스트에서는 CLI v0.7의
`model-image-05-user`를 선택했다. `-Agent` 없이 실행하면 별도 basic 프로필을 사용하며 같은 호스트의
`local-basic`은 모델이 없는 CLI v0.6 이미지다. 두 경로 모두 검증 원본을 보존하고
영속 사용자 복사본으로 부팅하며, 운영 중 host 로그인·패키지 설치·소스 공유 없이
AIOS CLI로 들어간다. 각 이미지의 실제 부팅·설정/history 보존 증거와 기본 이미지
선택·되돌리기 방법은 [운영 이미지 가이드](aios_operating_image_guide_ko.md)에 기록했다.
이미지·모델·선택 상태는 Git checkout에 포함되지 않는다. 새 환경은 해당 가이드의
§7(basic) 또는 §9.2(모델)에서 Build·Smoke·Run으로 준비한다.

Linux 개발 VM을 준비하는 Windows 경로는 [Start-AiosConsole.cmd](../../tools/hosted/Start-AiosConsole.cmd)를
실행하거나 저장소 루트에서 다음 명령을 사용한다.

```powershell
powershell.exe -NoProfile -File tools/hosted/Start-AiosConsole.ps1
```

설치된 QEMU와 Python을 사용한다. 실행 도구는 공식 이미지의 고정 hash를 확인하고,
임시 Linux 개발 VM의 DHCP·Python·CA 인증서를 준비한 뒤 AIOS 콘솔로 들어간다.
준비 단계 뒤의 콘솔은 일반 사용자 `aios`로 실행한다. 개발 VM의 Linux 부팅 로그와
AIOS 자체 시작·명령 출력은 역할을 구분한다. 준비 과정의 오류는 숨기지 않고 남긴다.
이미지 자체는 캐시하지만 임시 guest의 Python·CA 준비에는 인터넷 연결이 필요하다.
현재 콘솔은 입력 2,048자·최대 254개 명령·출력 4 MiB의 한 세션으로 제한한다.
Windows 콘솔에서 EOF 또는 Ctrl+C로 입력을 끝내면 실행 도구는 `exit`를 전달해
증거를 저장하고 VM을 종료한다.

Linux에서는 Python 3.11 이상, 시스템 CA 인증서와 정상적인 네트워크 설정을 준비한
뒤 직접 실행할 수 있다. 매번 새 결과 디렉터리를 사용한다.

```bash
python3 hosted/linux/aios-console.py --artifact-dir build/hosted-console/<new-run>
```

콘솔에서 다음 순서로 사용할 수 있다. 아래는 입력 예이며 성공한 실행 로그가 아니다.

```text
aios> help
aios> about
aios> status
aios> hardware
aios> net status
aios> resolve example.com
aios> fetch https://example.com/
aios> exit
```

| 명령 | 사용자에게 제공하는 정보 |
|---|---|
| `help` | 현재 지원하는 명령과 입력 형식 |
| `about` | AIOS 정체성, 실행 버전과 Linux-hosted 경계 |
| `status` | 현재 AIOS 세션과 초기 관측 상태 |
| `hardware [all\|cpu\|memory\|pci\|usb\|block\|net]` | 시작 시점의 Linux-visible 하드웨어 snapshot |
| `net status` | 시작 시점의 네트워크 인터페이스 상태; 인터넷 성공 판정과 구분 |
| `resolve HOST` | 요청한 이름에 대한 DNS 해석 결과 또는 구분된 오류 |
| `fetch URL` | HTTP(S) GET 결과, 응답 상태와 제한된 본문 표시 |
| `service status\|start\|stop\|restart` | 별도 CONSOLE_RUNTIME의 수명·상태 |
| `backend status\|start\|stop\|restart\|recover` | MAIN과 별도인 모델 backend의 수명·준비 상태; `recover`는 v0.7의 동일 CLI 보유 lease 범위 |
| `agent status\|start\|stop\|restart` | 실제 모델을 사용하는 별도 MAIN 서비스의 수명·상태 |
| `room status\|discover\|bind\|reconcile` | MAIN source의 명시적 발견·결속·재결속 |
| `ask TEXT` | 현재 유효하게 결속된 MAIN에 실제 모델 요청 |
| `cell status\|activate\|deactivate` | 기존 Cell 1의 관리 활성 상태·세대·결속 상태; 비활성화는 프로세스 중지가 아님 |
| `resources link\|status\|sample` | 명시적 MAIN/backend 관계와 각각의 CPU/RSS·unattributed system PSI |
| `clear` | 대화형 터미널 화면 정리 |
| `exit` | AIOS 세션 종료·증거 저장; Windows VM 실행 도구는 이어 정상 poweroff |

명령의 정확한 구문과 출력 계약은 [콘솔 구현](../../hosted/linux/aios_console/shell.py)과
실행 중 `help`를 따른다. 입력은 지원 명령으로 해석하며 호스트 셸 명령을 실행하는
프롬프트가 아니다. 실패한 DNS·HTTP 요청 뒤에도 다음 명령을 받을 수 있어야 한다.

설치된 모델을 쓰려면 Windows에서 `Start-AiosImage.cmd -Agent`로 부팅한 뒤
`backend start`, `agent start`, `room discover`, `room bind`, `ask ...`를 사용한다.
별도 개발 VM을 준비할 때는 `Start-AiosConsole.ps1 -Agent`를 사용한다.
v0.10의 `ask`는 UUID 접수 뒤 프롬프트로 돌아온다. 같은 CLI에서
`task status <UUID>`, `task result <UUID>`, `task cancel <UUID>`로 진행·결과·명시적 취소를
연결한다. 취소는 같은 CLI가 시작해 소유한 backend에 한정하며 접수와 실제 종료를 구분한다.
기존 동기 `SpaceSmoke`와 별도 TaskSmoke의 한정 실제 모델 PASS·미완료 이미지 범위는
[환경 문맥 가이드](aios_space_context_guide_ko.md)를 따른다. 기존 운영 이미지의 버전은 별도다.
현재 CLI v0.7/session schema 7은 runtime source 31개, MAIN protocol/run schema 4는
source 24개를 기록한다. [MAIN 가이드](aios_agent_binding_guide_ko.md),
[자원 관측 가이드](aios_resource_observation_guide_ko.md),
[Cell 수명 가이드](aios_cell_lifecycle_guide_ko.md)가 각 명령의 계약과 실제 증거를 소유한다.
Cell 1 관리 전이는 `PARTIAL`이며 이전 v0.5 `cell-01`의 로컬 Linux·실제 모델 검증을 통과했다. 비활성화 뒤에도
MAIN/backend는 살아 있고, 재활성화 뒤 `room discover`, `room reconcile`, `resources link`가
필요하다. `-CellSmoke -GuestTests`로 이 별도 시나리오를 실행한다.
이 Cell 실행은 당시 v0.5 소스로 보존되며 현재 소스의 검증 완료를 뜻하지 않는다.
현재 backend 관리와 receipt schema 2의 MAIN 실제 요청 대상 결속은 `backend-02`에서
로컬 Linux·실제 모델·교체 후 요청 거부·명시적 복구·자원 관측·인터넷·정상 종료와
당시 소스 독립 재검증을 통과했다. `backend-02`는 보존된 소스의 증거이며 이후 변경된
현재 소스의 실행 검증을 대신하지 않는다. 성숙도는 `PARTIAL`을 유지한다.
[backend 수명 가이드](aios_backend_lifecycle_guide_ko.md)의 `-BackendSmoke -GuestTests`를 따른다.

## 3. 인터넷 통신의 범위

`hardware`와 `net status`는 source-only 상태 관측이다. `resolve`와 `fetch`는
사용자가 요청한 실제 네트워크 I/O다. 따라서 전체 콘솔을 `observation_only`라고
부르지 않는다. 이 통신 지원은 scheduler·quota·cgroup 변경 같은 resource apply
capability를 열지 않으며, 기존 관측 레코드의 경계를 바꾸지 않는다.

첫 통신 범위는 DNS 해석과 HTTP(S) GET이다. 기본 대기 상한은 DNS 5초, HTTP(S) 8초이며
별도 worker process로 제한한다. process 생성·종료 처리 시간은 추가될 수 있다.
기본 본문 수신은 16 KiB, 화면 미리보기는 최대 2 KiB이고 수신 상한 도달을 표시한다.
잘못된 입력·해석 실패·접속 거부·시간 초과·TLS 오류·HTTP 오류를 구분해 보여 준다.
redirect는 따라가지 않고 응답 상태로 보고한다. 정확한 제한과 의미는
[네트워크 구현](../../hosted/linux/aios_console/network.py)을 따른다.
TLS는 시스템 CA와 호스트 이름 검증을 사용하고, 인증서 오류를 성공으로 우회하지 않는다.
Python은 기본 client context에서 CA·hostname 검증을 제공하므로 이 경계를 유지한다.
[공식 근거: Python SSL client context](https://docs.python.org/3/library/ssl.html)

현재 개발 VM의 QEMU user networking은 DHCP·DNS와 외부 접속 경로를 제공한다.
기본 구성이 외부의 guest 진입을 막고 일반 ICMP에는 환경 제약이 있으므로,
`ping` 한 번을 인터넷 acceptance로 삼지 않는다. 실제 DNS와 HTTP(S) 응답을 검증한다.
[공식 근거: QEMU user mode networking](https://www.qemu.org/docs/master/system/devices/net.html)

host forwarding은 외부에서 guest 서비스에 들어오는 별도 설정이다. 이번 클라이언트
기능에 필요한 설정이 아니므로 listener·port forwarding을 추가하지 않는다.
[공식 근거: QEMU network invocation](https://www.qemu.org/docs/master/system/invocation.html)

공식 최신 문서의 설명을 검토하되, 실행에 사용한 QEMU·Linux·Python 버전은 각 run의
provenance로 확인한다. 최신 문서를 읽은 날짜가 기존 upstream exact pin을 갱신하거나
Linux primary reference 지원을 증명하지 않는다. upstream 구현 코드는 수입하지 않는다.

## 4. 실행 증거와 검증

runtime 코드는 `hosted/linux/`, 실행·외부 판정은 `tools/hosted/`가 소유한다.
runtime에서 검증 도구를 import하지 않는다. 명령 출력, 구조화 이벤트, 종료 결과와
외부 판정은 각각 보존하고 run identity·입출력 순서·종료 상태로 서로 대조한다.
정확한 파일·필드·판정 계약은 [외부 verifier](../../tools/hosted/verify_console.py)를 따른다.
기존 [boot verifier](../../tools/hosted/verify_boot.py)의 성공을 새 콘솔 검증으로 재사용하지 않는다.

Windows의 자동 인터넷 검증:

```powershell
powershell.exe -NoProfile -File tools/hosted/Start-AiosConsole.ps1 -Smoke -GuestTests
py -3 -m unittest discover -s tools/hosted/tests -p "test_*.py" -q
```

첫 명령은 새 결과 디렉터리를 만들며 실제 Linux 테스트·CLI·인터넷 명령과 정상 종료를
확인한다. `-ArtifactDirectory <new-directory>`로 결과 위치를 지정할 수 있다.
실행 도구가 출력한 경로를 아래 두 위치에 동일하게 넣어 보존된 증거를 재검증한다.

```powershell
py -3 tools/hosted/verify_console.py <outerdir> --execution --require-live --require-internet --source-root <outerdir>/runtime-source
```

`--require-internet`은 실제 통신 성공이 필요한 acceptance에 사용한다. 사용자가
`help`만 보고 `exit`한 정상 세션을 인터넷 검증 완료로 표시하지 않는다.
일반 사용자 세션의 정상 종료와 인터넷 acceptance는 서로 다른 판정이다.
이 재검증 명령은 콘솔 프로세스·stdout/stderr·명령 결과를 검사한다. VM의 poweroff와
QEMU 종료는 실행 도구가 별도로 판정해 `vm-verdict.json`에 보존하므로, 세션 재검증만으로
VM 종료까지 다시 검사했다고 설명하지 않는다.

정규 host 테스트는 외부 사이트 상태와 분리된 로컬 서버·고정 입력으로 실패 처리와
제한을 검증한다. 실제 인터넷 시도는 별도 run에서 목적지·실행 시각·응답·TLS 결과를
남긴다. 원격 사이트 장애가 테스트 전체를 불규칙하게 만드는 상시 CI gate는 피한다.
실제 외부 접속을 하지 않은 fixture 결과는 인터넷 지원 증거로 사용하지 않는다.

## 5. 최소 완료 조건

1. Linux 준비 뒤 AIOS 고유 banner와 `aios>`가 출력되고 여러 명령을 계속 받는다.
2. 상태·하드웨어·인터페이스 결과는 실제 source와 관측 한계를 보존한다.
3. 같은 실행에서 DNS와 HTTP(S) 요청 결과를 확인한다. HTTPS는 인증서를 검증한다.
4. 잘못된 명령·URL, DNS/접속/TLS/HTTP 오류, 시간·읽기 상한 뒤 프롬프트가 유지된다.
5. `exit` 뒤 runtime 종료와 외부 process exit를 대조한다. VM 검증은 poweroff와
   QEMU exit 0까지 확인하며 강제 종료·누락·다른 run·변조된 증거를 PASS로 보지 않는다.

## 6. 실행 결과 기록

2026-09-03 실제 실행 결과다. 기존 H2-a `qemu-07`과 별도로 CLI 경로를 검증했다.
기반 checkout은 `beta` / HEAD `2b4649b7db1a061d63dd1e9d9e778ebc7939c7a3`이며
이번 작업은 커밋 전 상태다. 각 `environment.json`에 실행 당시 HEAD·dirty 목록,
QEMU·ISO/kernel/initramfs hash와 실행 인자를 남겼고 `runtime-source/`에 실제
실행한 runtime 8개 소스를 보존했다.

| 확인 대상 | 결과 | 실제 증거·범위 |
|---|---|---|
| Windows hosted 전체 테스트 | `PASS` | Python 3.11.9, 219 tests / 32.180 s, symlink 권한 관련 1 skip |
| Linux guest 관련 테스트 | `PASS` | Linux 6.18.35-0-virt / Python 3.14.7, 87 tests / 12.956 s, skip 없음 |
| 자동 명령 실행 | `PASS` | `console-02`에서 about/status/hardware/net status/DNS/HTTPS/잘못된 명령/help/exit 9개 |
| 실제 대화형 입력 | `PASS` | `console-03`에서 Windows 터미널 입력, clear·DNS·HTTP·HTTPS·DNS 오류·status·exit 7개 |
| 실제 DNS | `PASS` | `resolve EXAMPLE.com` → 정규화된 `example.com`, IPv4 2개·IPv6 2개 |
| 실제 HTTP·HTTPS | `PASS` | `http://example.com/`, `https://example.com/` 모두 200·559 bytes; HTTPS `tls_verified=true` |
| 사용자 오류 뒤 계속 사용 | `PASS` | unknown command 뒤 help/exit, `no-such-aios-host.invalid` DNS 실패 뒤 status/exit |
| process 종료·외부 재검증 | `PASS` | 두 실행 모두 CLOSED/exit 0, stderr 0 bytes, 전체 stdout·명령 의미·boot/run·소스 hash 일치 |
| VM 정상 종료 | `PASS` | 두 실행 모두 poweroff marker·QEMU exit 0, `host_killed=false`, `shutdown_observed=true` |
| source policy·문서 | `PASS` | 13-row resource guard 유지, 문서 상대 링크 167개 정상, PowerShell 구문·diff whitespace 정상 |
| 원격 exact-SHA acceptance | 미실행 | 동일 SHA의 named job·terminal outcome·artifact |
| Linux primary exact qualification | 미검증 | canonical kernel/config/package/hash와 지원 gate |

공통 VM 조건은 QEMU 10.2.0 / q35 / TCG / 2 CPUs / RAM 768 MiB,
Alpine virt 3.24.1 x86_64다. 게스트의 usable RAM은 약 719.55 MiB다.
이 Alpine 이미지는 개발 호스트이며 AIOS 배포판 산출물로 게시하지 않는다.

| 로컬 실행 디렉터리 | session identity | 의미 |
|---|---|---|
| `build/hosted-console/console-02/` | `06e9ae47-528d-4dfd-80df-643800bbef90` | 자동 입력 smoke, 최종 외부 verifier로 재검증 |
| `build/hosted-console/console-03/` | `1900bf2c-50ce-43e6-ac18-fcb2d75f5cc0` | 실제 대화형 터미널·Linux tests, 최종 외부 재검증 |

`console-03`의 boot 완료 시각은 `2026-09-03T09:12:00.923487+00:00`이며,
다음 명령으로 해당 세션을 특정해 재검증할 수 있다.

```powershell
py -3 tools/hosted/verify_console.py build/hosted-console/console-03 --execution --require-live --require-internet --session-id 1900bf2c-50ce-43e6-ac18-fcb2d75f5cc0 --source-root build/hosted-console/console-03/runtime-source
```

검증은 로그 hash만 보지 않고 각 명령의 의미에 맞는 전체 화면 출력을 독립 재구성해
정확히 대조한다. 종료 뒤 추가 출력, 변조된 상태·하드웨어·TLS 결과, 다른 session,
bool/int 혼동, 비정상 process 종료·stderr를 거부한다. 원격 본문 속 `FAIL`·`aios>`는
단순 문자열로 표시하고 정상 프롬프트나 runtime 실패로 해석하지 않는다.

첫 개발 시도 `console-01`은 guest loopback 미설정 때문에 로컬 HTTP 반례 서버가
열리지 않아 `FAIL`로 보존했다. 실행 도구가 loopback을 준비하도록 수정한 뒤
실제 Linux 87개 검증을 통과했다. 실패한 실행을 PASS로 덮어쓰지 않았다.
로컬 비추적 artifact는 clone에 포함되지 않는다. 기존 native 커널·ABI와 upstream
source pin은 변경하지 않았으며 native QEMU/원격 CI는 이번 확장에서 실행하지 않았다.

## 7. 장기 작업에서 이어갈 순서

이 절의 초기 실행 증거는 개발 VM에서 직접 사용하는 최소 AIOS 사용자 환경이다.
후속 기본 운영 이미지는 같은 설치 디스크의 반복 부팅과 설정·history 보존을 검증했다.
그 결과는 [운영 이미지 가이드](aios_operating_image_guide_ko.md)가 소유하며, 이전 세션의
실행 상태·AI readiness 복원이나 범용 설치·복구를 제공한다는 뜻은 아니다.
터미널 HTTP 클라이언트의 완료를 웹 브라우저·네트워크 서버·범용 OS 지원으로 확장하지 않는다.

v0.2에서 [장기 서비스 운영 가이드](aios_service_lifecycle_guide_ko.md)에 따라
CONSOLE_RUNTIME의 시작·중지·재시작과 CLI 재접속을 도입했다(`SUPPORTING/PARTIAL`).
위 §6의 schema 1 실행은 당시 소스로 재생하며 schema 2와 혼합하지 않는다.
v0.3/schema 3은 source 20개와 별도 MAIN의 `agent`·`room`·`ask` 명령을 도입했다.
모델 준비·명시적 결속·재결속과 검증 범위는 [MAIN 가이드](aios_agent_binding_guide_ko.md)를 따른다.
v0.3의 실제 인터넷 재검증은 `build/hosted-agent/interactive-03`에 보존했다. 일반
대화형 입력 4개(help/DNS/HTTPS/exit), DNS 주소 4개, 인증서가 검증된 HTTPS 200/559 bytes,
정상 VM 종료와 당시 v0.3 source 20개 snapshot의 독립 재검증을 통과했다(2026-09-07).
v0.4에서 MAIN/backend 자원 관측을 도입했고, v0.5는 기존 Cell 1 관리 전이를 추가했다.
v0.6의 backend 수명과 MAIN 실행 결속은 `backend-02`에서 로컬 실제 검증과
당시 소스 재검증을 통과했으며 보존된 소스 증거로 `PARTIAL`을 유지한다. 과거 CLI schema 1~6은
각 버전의 보존 소스로 재생하며 후속 기능의 증거로 승격하지 않는다. 전체 H2/H3,
CLI 소실·재부팅 이후 복구와 필요한 worker·cgroup의
명시적 읽기 관계, native Cell lifecycle은 남아 있다.
모델 포함 이미지의 실제 검증은 아래 운영 이미지 증거로 구분한다. 영속 AI 상태·업데이트와
지원 기준선은 후속이며, resource apply는
K5 principal/ownership·권한 분리·rollback 증거 뒤의 단계로 유지한다.

현재 v0.7은 같은 CLI가 생존 중 확보한 child pidfd를 사용해 supervisor 소실 뒤
`backend recover`로 명시적 정리를 수행한다(`PARTIAL`, 실제 Linux·모델 기록의 별도 독립 재검증 PASS; 원본 FAIL 보존).
결과는 정상 `STOPPED`와 다른 `RECOVERED`이며 새 backend·MAIN 시작과 재결속은 명시적이다.
정확한 한도·실패 계약은 [backend 수명 가이드](aios_backend_lifecycle_guide_ko.md)를 따른다.

현재 CLI 0.7/session 7의 설치 모델 이미지는 `model-image-05`에서 source 35개를 고정하여
online·offline 두 부팅 45명령, 실제 질문·재결속·인터넷·정상 종료와 독립 재생을 통과했다.
전용 0.7 실행기 (`build/hosted-image-v07/Start-Aios-0.7.cmd`)는 별도 `model-image-05-user`를
사용하며 실제 버전·상태 조회·종료 세 명령도 PASS다. 기존 v0.6 `local-model`·기본 포인터는 보존한다.
정확한 결과는 [운영 이미지 가이드 §9.7](aios_operating_image_guide_ko.md)을 따른다.
별도 장애 복사본 02의 recover·즉시 exit·정상 종료 PASS와 01 원본 FAIL 보존도 같은 정본을 따른다.
정상 모델 이미지와 장애 시험의 증거를 구분하며 성숙도는 `SUPPORTING/PARTIAL`이다.
기본 사용자 사본의 `Selection`·`Select`·`Rollback`은 운영 가이드 §9.8의 별도 host 기능이다.
선택 기록을 우선하고 디스크·history를 보존한다. Windows 로컬의 실제 0.7·0.6 기본 부팅과
최종 0.7 재선택은 별도 PASS이며 `SUPPORTING/PARTIAL`이다. 두 부팅 모두 DNS·인증서 검증 HTTPS와
정상 종료를 확인했고 앞의 전용 실행 PASS와 구분한다.
