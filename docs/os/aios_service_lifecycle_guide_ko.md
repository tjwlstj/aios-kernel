# AIOS 장기 서비스 수명주기 운영 가이드

이 문서의 실행 ID별 `build/` 자료는 로컬 비추적 증거이며 Git checkout에 포함되지 않는다.
과거 결과의 재검증 명령은 해당 원본을 보유한 환경용이며 새 실행의 결과 경로와 구분한다.

> 문서 역할: Linux-hosted CLI 다음 단계의 작업·운영 가이드
>
> 상위 정본: [Linux substrate 정책](linux_hosted_substrate_and_resource_policy_ko.md),
> [Kernel Room 관리 모델](../kernel-room/kernel_room_management_model_ko.md)
>
> 이전 단계: [CLI·인터넷 가이드](aios_cli_internet_guide_ko.md)
>
> 성숙도: CLI와 분리된 `CONSOLE_RUNTIME` 실행 기반은 `SUPPORTING/PARTIAL`.
> 별도 MAIN `AI_SERVICE` 결속과 반복 부팅 운영 이미지는 각 가이드의 `PARTIAL`이며,
> 이 unbound 서비스 자체의 성숙도를 승격하지 않는다. 전체 H2/H3는 `PLANNED`.
>
> 문서 수명주기: 활성 / 공식 문서 검토일: 2026-09-04

## 1. 이번 단계의 의미

이번 조각은 **CLI를 닫아도 살아 있는 AIOS 서비스를 시작하고, 다시 접속해 상태를
확인하고, 명시적으로 중지·재시작할 수 있는 실행 기반**이다. Linux는 드라이버와
프로세스 실행을 맡고 AIOS는 서비스 명령, 식별자, 세대와 실행 기록의 의미를 소유한다.
`kernel/`의 독자적인 native 커널과 ABI는 계속 별도 reference/proof 경로로 유지한다.

서비스 종류는 `CONSOLE_RUNTIME`이다. 관측 daemon에 `AI_SERVICE`라는 이름을 붙여
K1의 Node 101에 결속하지 않는다. `binding_status=UNBOUND`이며 Linux PID,
boot ID, Unix socket 경로는 `source_only` 진단 정보다. 서비스 UUID와 source
generation도 아직 canonical Cell/Node ID 또는 binding generation이 아니다.

관리 모델에 대한 분류는 `SUPPORTING`이다. 이 실행 기반 이후 실제 `AI_SERVICE` 의미를
가진 producer와 명시적인 Cell/Node binding을 별도 `DIRECT/PARTIAL`로 구현·검증했다.
해당 namespace·kind·role·instance·generation과 실행 증거는 [MAIN 가이드](aios_agent_binding_guide_ko.md)를 따른다.
서비스가 오래 살아 있거나 heartbeat가 증가하는 것은 이 결속의 완료 증거가 아니다.

## 2. 시작하고 다시 접속하기

Windows에서는 [기존 실행 도구](../../tools/hosted/Start-AiosConsole.ps1)를 사용한다.

```powershell
powershell.exe -NoProfile -File tools/hosted/Start-AiosConsole.ps1
```

AIOS 콘솔에 들어온 뒤 서비스 명령을 사용한다. 다음은 입력 예다.

```text
aios> service status
aios> service start
aios> service status
aios> service restart
aios> service stop
aios> exit
```

| 명령 | 의미 |
|---|---|
| `service status` | 현재 서비스와 관측 상태 조회. 아직 시작하지 않은 `ABSENT`는 정상 상태 |
| `service start` | 새 서비스 인스턴스를 명시적으로 시작. 실행 중이면 `ALREADY_RUNNING` 거부 |
| `service stop` | 확인된 현재 인스턴스에 정상 종료 요청 |
| `service restart` | 이전 인스턴스를 중지한 뒤 새 인스턴스와 증가한 세대로 시작 |
| `exit` | CLI 세션 종료. Linux 직접 실행에서는 서비스가 계속 유지됨 |

콘솔을 열거나 닫는 동작은 서비스를 자동으로 시작·중지하지 않는다. Windows VM
실행 도구는 사용자가 콘솔을 끝내면 guest 서비스를 별도로 중지한 뒤 VM을 poweroff한다.
아직 서비스를 시작하지 않았다면 `ABSENT`를 확인한 뒤 종료한다. 검증용
`-ServiceSmoke`만 첫 CLI 전에 서비스를 명시적으로 시작한다. 이 개발 VM의 종료와
Linux에서 CLI만 닫는 동작을 구분한다.

Linux에서는 Python 3.11 이상, `pidfd_open`을 사용할 수 있는 Linux 커널과 로컬
파일시스템을 사용한다. pidfd를 확보할 수 없으면 `stop`은 종료 성공을 반환하지 않는다.
기본 상태 경로는
`~/.local/state/aios/console-runtime`이며 `--service-dir`로 명시할 수 있다. 같은
서비스에 접속할 때는 같은 사용자와 상태 경로를 사용하고, 각 CLI 결과 경로는 새로 만든다.

```bash
python3 hosted/linux/aios-console.py --artifact-dir build/session-a --service-dir "$HOME/.local/state/aios/console-runtime"
# 위 콘솔에서 service start, service status, exit 입력
python3 hosted/linux/aios-console.py --artifact-dir build/session-b --service-dir "$HOME/.local/state/aios/console-runtime"
# 위 콘솔에서 service status, service restart, service stop, exit 입력
```

두 번째 CLI는 이미 실행 중인 서비스의 identity·generation을 확인한다. 재시작 뒤에는
새 instance와 증가한 generation을 보여야 한다. 서비스를 직접 조회할 때는
`python3 hosted/linux/aios-service.py status --state-dir <same-private-path>`를 사용한다.
이 진입점도 `start`, `stop`, `restart`를 제공한다. 살아 있는 서비스의 상태 디렉터리를
정리하지 않는다. 저장소 삭제는 같은 서비스의 수명 이력을 이어가는 작업이 아니다.

## 3. 식별자와 상태의 계약

구체 필드·파일·상태 전이는 [서비스 구현](../../hosted/linux/aios_service/lifecycle.py)을
정본으로 삼는다. CLI의 정확한 명령·화면 계약은
[콘솔 구현](../../hosted/linux/aios_console/shell.py)과 실행 중 `help`를 따른다.

| 구분 | 소유자와 수명 |
|---|---|
| `service_id` | AIOS producer가 생성하고 동일 상태 저장소에서 재시작 사이에 유지 |
| `instance_id` | 새 시작 시도마다 생성하는 별도 UUID |
| `generation` | 같은 서비스의 새 시작 시도에서 증가. PID나 시각으로 대체하지 않음 |
| `observation_sequence` | 같은 인스턴스의 관측 진행 증거. 서비스 세대와 구분 |
| Linux PID·boot ID | 현재 Linux 실행체를 설명하는 source-only 정보 |
| canonical binding | `UNBOUND`; 관리 대상의 count·resource apply 권한을 만들지 않음 |

잠금을 얻은 새 시작 시도에 세대를 할당한다. 부팅이나 관측이 실패해도 이미 소비한
generation을 되돌리지 않는다. 재시작은 이전 세대를 성공으로 바꾸는 기능이 아니다.

저장된 마지막 관측은 증거이며 현재 실행 여부의 단독 원본이 아니다. 소켓 파일이나
잠금 파일의 존재만으로 `RUNNING`을 반환하지 않는다. 연결 응답의 identity와 세대를
대조한다. RPC가 실패하고 잠금도 풀린 이전 `RUNNING` 기록은 `STALE`과
`PROCESS_NOT_RUNNING`으로 표시한다. 저장된 `STOPPED`도 종료 의사를 기록한 증거이며,
`stop`의 성공 판정은 확인한 실행체의 pidfd가 실제 종료를 알렸는지 별도로 검사한다.
중지가 불확실하면 이를 정상 종료로 추정해 다음 generation을 시작하지 않는다.

프로토콜 요청은 대상 instance와 generation을 지정한다. 이전 세대의 요청,
다른 instance, malformed record와 제한을 넘은 메시지는 현재 서비스의 변경 명령으로
수용하지 않는다. 재시작은 임의의 Linux PID를 찾아 종료하는 기능이 아니다.

## 4. Linux 경계와 공식 근거

서비스는 일반 사용자의 private 상태 디렉터리와 pathname Unix socket을 사용한다.
디렉터리 `0700`, socket `0600`, 연결 peer UID 확인을 함께 사용한다. Linux의
`SO_PEERCRED`는 연결 상대의 자격 정보를 반환하지만 AIOS principal/Cell ownership을
자동으로 제공하지 않는다. 같은 UID의 다른 프로그램에 대한 sandbox 또는 root 차단으로
표현하지 않는다. [Linux unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html)

단일 실행체의 배타성은 열린 파일의 `flock`을 서비스 수명 동안 유지하는 방식이다.
잠금 파일명 자체는 소유권 증거가 아니며, `flock`은 advisory lock이다. NFS/SMB는
잠금 의미가 달라질 수 있어 이번 상태 저장소 지원 범위에서 제외한다.
[Linux flock(2)](https://man7.org/linux/man-pages/man2/flock.2.html)

CLI와 서비스의 세션·표준 입출력을 분리하고 셸 해석 없이 프로세스를 시작한다.
Python의 `start_new_session`은 POSIX에서 새 세션을 만들며, 별도 callback으로
`setsid`를 호출하는 방식보다 명시적인 경계를 제공한다.
[Python subprocess](https://docs.python.org/3/library/subprocess.html)

종료 신호는 main thread에서 처리하고 정상 종료 루프에 의사를 전달한다. Python
signal handler가 임의 지점에서 예외를 발생시키면 정리 과정이 중단될 수 있으므로,
종료 요청과 최종 저장·소켓 정리·실제 종료를 별도 단계로 본다.
[Python signal](https://docs.python.org/3/library/signal.html)

IPC에는 메시지 크기와 대기 상한을 적용하고 malformed 응답을 성공으로 해석하지 않는다.
wire 메시지 상한은 4 KiB다. 연결·전송에는 2초 socket timeout을 적용하고, 한 프레임의
수신은 여러 조각으로 나뉘어도 전체 2초 안에 끝내야 한다. 이는 서비스 명령 전체가
2초 안에 끝난다는 뜻이 아니다. 시작 준비 대기는 10초, 중지 응답 뒤 pidfd 종료 대기는
8초를 기준으로 하며, 각 IPC·파일 접근·정리 단계는 별도로 진행한다.
[Python socket](https://docs.python.org/3/library/socket.html)

Linux `pidfd`는 특정 프로세스의 수명을 가리킨다. 제어 클라이언트는 연결 peer와 응답의
PID·boot ID·instance·generation을 확인하고 pidfd로 그 실행체의 종료를 확인한다.
기록된 PID 숫자만을 대상으로 신호를 보내거나 pidfd를 canonical ID로 사용하지 않는다.
[Linux pidfd_open(2)](https://man7.org/linux/man-pages/man2/pidfd_open.2.html)

이번 서비스 관리 명령은 AIOS 실행체를 시작·중지하는 사용자 요청이다. 전체 CLI를
관측 전용으로 표시하지 않는다. 주기 관측 자료는 evidence-only이며 scheduler,
quota, cgroup, device 설정 등 resource action은 계속 `UNSUPPORTED`다. 호스트의
init/systemd 등록이나 상시 자동 재시작 정책은 이번 구현 범위가 아니다.

## 5. 완료 판정과 실행 증거

서비스 수명주기 검증은 다음을 모두 확인한다.

1. 일반 Linux 사용자로 시작하고 private 상태·소켓·단일 owner를 확인한다.
2. 첫 CLI 종료 뒤 서비스가 살아 있고 두 번째 CLI가 같은 identity·generation에 접속한다.
3. 명시적인 restart와 stop→start에서 새 instance와 증가한 generation을 확인한다.
4. 중복 시작, 오래된 instance/generation, 잘못된 peer·record·경로, 종료 시간 초과를 거부한다.
5. 정상 중지와 비정상 종료 후 stale 흔적을 구분하고 실제 상태와 로그를 대조한다.
6. 같은 실행의 CLI 출력·이벤트·서비스 관측·프로세스 결과·source hash를 독립 검증한다.
7. 최종 서비스를 중지하고 VM poweroff 및 QEMU exit 0을 확인한다. 강제 종료는 PASS가 아니다.
8. 기존 CLI schema 1 증거를 보존된 당시 소스로 재검증하고 이 단계의 schema 2와 구분한다.

`registry.json`은 service/instance/generation을, `latest.json`은 최근 상태를 저장한다.
`runs/<instance_id>/`에 시작 기록, 최대 4개 lifecycle 이벤트, 마지막 CPU·memory 관측과
종료 결과를 보존한다. 약 1초 주기 관측은 마지막 값을 교체하며 무제한 로그를 쌓지 않는다.
각 인스턴스의 `boot/`는 전체 하드웨어를 한 번 관측한 별도 증거다. 주기 관측은 실시간
보장을 제공하지 않으며 갱신 시각과 순서를 확인해야 한다. 재시작마다 별도 실행 이력과
시작 시도 기록을 보존하므로 세대가 늘면 전체 디스크 사용량도 늘어난다. 자동 보관 기한과
이력 정리는 아직 `PLANNED`다.

이 단계의 CLI v0.2.0은 session schema 2와 runtime source 12개의 hash를 남긴다. 서비스
실행 자체의 시작 기록은 boot·service source 8개의 hash를 남긴다. 이전 CLI v0.1.0의
schema 1·source 8개 계약은 당시 보존된 소스와 함께 별도로 재검증한다. 소스 파일 수의
차이를 현재 서비스 지원 또는 과거 증거의 실패로 자동 해석하지 않는다.

Windows의 실제 Linux 서비스 검증은 다음 진입점을 사용한다.

```powershell
powershell.exe -NoProfile -File tools/hosted/Start-AiosConsole.ps1 -ServiceSmoke -GuestTests
py -3 -m unittest discover -s tools/hosted/tests -p "test_*.py" -q
py -3 tools/hosted/verify_service.py build/hosted-console/service-04 --workflow
```

| 확인 대상 | service-04 당시 실행 결과 |
|---|---|
| Windows hosted 전체 테스트 | `PASS`: 최종 279 tests / 46.124s, Linux 전용 15개·symlink 2개 skip, 나머지 통과 |
| 실제 Linux guest 서비스·CLI 검증 | `PASS`: service-04, 일반 사용자 aios로 147 tests / 48.236s, skip 없음 |
| 두 CLI 사이 서비스 유지·재시작 | `PASS`: service-04, 같은 generation 1에서 관측 5→13, restart 및 stop/start에서 1→2→3 |
| 변조·stale·종료 실패 반례 | `PASS`: 실제 Linux lifecycle 반례와 독립 verifier 26개 fixture 반례; fixture는 live로 거부 |
| 이전 CLI schema 1 재검증 | `PASS`: console-02/03 당시 source 8개로 execution/live/Internet 재검증 |
| VM 정상 종료 | `PASS`: service-04 poweroff marker, QEMU exit 0, host_killed=false |
| 원격 exact-SHA acceptance | 미실행 |
| Linux primary exact qualification | 미검증 |

2026-09-04 로컬 작업은 `beta` / HEAD `2b4649b7db1a061d63dd1e9d9e778ebc7939c7a3`의
커밋 전 변경이다. 개발 환경은 QEMU 10.2.0 / q35 / TCG / 2 CPUs / RAM 768 MiB,
Alpine virt 3.24.1 / Linux 6.18.35-0-virt / Python 3.14.7이다. 최종 `service-04`의
service ID는 `5ff6eae0-8bdb-4ede-abbf-f477f787c575`이며 두 CLI의 실제 프로세스,
source hash, stdout/stderr와 서비스 세대 3개의 결과를 독립 재검증했다. 같은 실행에서
example.com DNS 4개 주소와 HTTPS 200 / 559 bytes / 인증서 검증을 확인했다.

`service-01`은 Python 3.14 JSON decoder가 깊이 2,000 입력을 자동 거부하지 않아
반례 테스트가 실패했다. runtime이 깊이 16·노드 4,096 상한을 직접 검사하도록 수정했다.
`service-02`는 테스트 143개 통과 후 일반 사용자 홈 아래의 새 상태 디렉터리를 거부했다.
이미지의 `/home/aios`가 setgid `2755`이므로 새 하위 디렉터리가 `2700`을 상속한 것이
원인이다. 직접 만든 디렉터리만 소유자·inode 확인 후 정확히 `0700`으로 설정하도록
수정했고, 기존 경로의 권한을 고치거나 검사를 완화하지 않았다. 실패 run은 보존한다.
Windows VM 실행 도구는 별도의 사용자 소유 `/tmp/aios-runtime`을 준비한다.
`service-03`은 이 경로로 첫 생존·세대·통신 smoke를 통과했고, 최종 `service-04`는
setgid 수정과 verifier 반례를 포함한 일반 사용자 Linux 검증 147개 및 같은 smoke를
모두 통과했다. 당시 runtime source 12개는 service-04에 보존했다. v0.3/schema 3에서 도입한
별도 MAIN과 hosted 결속은 [MAIN 가이드](aios_agent_binding_guide_ko.md)를 따르며, 이 과거
schema 2 실행은 당시 보존 소스로 재생한다.

`runtime-source/`는 당시 실행한 소스를 보존하며 이후 코드가 바뀌어도 별도로 재생할 수
있다. `verify_service.py --workflow`는 저장된 제어 프로세스의 출력·실제 종료 코드와
두 CLI 실행, 서비스 기록·현재 registry를 검사한다. QEMU 종료는 실행 도구의 별도
`vm-verdict.json` 판정이다. source hash나 저장된 STOPPED 기록 하나만으로 현재 프로세스
생존을 주장하지 않는다. 실행 이력 검증기는 최대 64세대, QEMU export는 256파일로
제한하며 이번 고정 smoke는 새 상태 저장소의 세대 3개를 검사한다.

로컬 개발 VM의 실행 결과는 canonical Linux primary reference의 지원 검증을 대신하지
않는다. upstream resource manifest의 exact pin과 `code_import=false`는 유지한다.

## 6. 장기 작업의 다음 단계

native 모델 로더 도메인의 scaffold·예제 manifest와 별도 hosted 모델 실행을 구분한다.
현재 hosted MAIN은 실제 모델 bytes·warmup·요청·응답 검증 뒤에만 readiness를 발행한다.
CONSOLE_RUNTIME의 UUID instance와 재시작 generation을 H1 native u64 instance 및
새 instance마다 1에서 시작하는 source generation으로 임의 변환하지 않는다.

후속 [MAIN 가이드](aios_agent_binding_guide_ko.md)는 실제 producer의 typed namespace·semantic
kind·role과 copied read record, 별도 관리 authority의 명시적 결속·재결속을
`DIRECT/PARTIAL`로 구현·검증한 증거를 소유한다. source와 canonical generation을 분리하며
CONSOLE_RUNTIME을 MAIN으로 재명명하지 않는다. [운영 이미지 가이드](aios_operating_image_guide_ko.md)는
basic 및 모델 이미지의 반복 부팅·설정/history 보존을 별도 `SUPPORTING/PARTIAL`로 검증했다.

전체 H3의 exit·재생성·collector restart·host reboot 구분과 native/hosted parity는 후속이다.
MAIN/backend의 개별 CPU/RSS·system PSI 관측을 전체 workload의 자원 소유권으로 확대하지 않는다.
범용 설치·업데이트·영속 AI 상태·primary 지원 기준선은 별도 증거가 필요하다.
K5 principal/ownership·별도 권한 경계·rollback 검증 전에는 resource apply를 열지 않는다.
