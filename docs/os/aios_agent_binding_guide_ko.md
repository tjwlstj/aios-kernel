# AIOS MAIN AI 서비스와 hosted 관리 결속 가이드

이 문서의 실행 ID별 `build/` 자료는 로컬 비추적 증거이며 Git checkout에 포함되지 않는다.
과거 결과의 재검증 명령은 해당 원본을 보유한 환경용이며 새 실행의 결과 경로와 구분한다.

> 작성: 2026-09-04 / 갱신: 2026-09-07
>
> 역할: 실제 모델 요청을 처리하는 MAIN AI_SERVICE와 별도 hosted 관리 권위의
> 결속·관측·재결속 계약 및 실행 증거 가이드
>
> 방향: `DIRECT` — producer identity를 실제 Cell/Node/NodeBit 관계로 연결
>
> 상태: `DIRECT/PARTIAL`. 실제 MAIN 요청·hosted 결속·재결속의 로컬 증거는 §8에서 구분한다.

정체성과 순서는 [Kernel Room 관리 모델](../kernel-room/kernel_room_management_model_ko.md),
substrate와 code import 경계는 [Linux-hosted 정책](linux_hosted_substrate_and_resource_policy_ko.md)을
따른다. 기존 [CONSOLE_RUNTIME](aios_service_lifecycle_guide_ko.md)은 하드웨어 관측과
콘솔 수명을 돕는 `SUPPORTING/PARTIAL` 서비스다. 이 서비스를 이름만 바꿔 MAIN으로
결속하지 않는다. 새 producer는 실제 모델을 준비하고 사용자 입력에 모델 응답을 돌려준다.

## 1. 범위와 사용자 가치

한 사용자가 Linux에서 장기 MAIN 서비스를 시작하고, AIOS CLI에서 상태를 조회하고
질문하며, CLI를 닫았다가 다시 열어도 같은 producer에 접근하는 경로를 만든다.
관리 계층은 이 source를 발견한 뒤 명시적으로 결속한다. 서비스가 재시작되면 기존
결속이 오래된 상태임을 표시하고 새 source를 확인한 뒤 재결속한다.

모델 응답은 사용자에게 표시할 텍스트다. shell 실행, 파일 변경, resource action,
quota·scheduler·capability 변경으로 해석하지 않는다. 사용자 요청 추론은 실제 compute와
통신을 수행하므로 전체 세션을 `observation_only`로 표현하지 않는다. snapshot 조회와
source 관측은 read-only이고 Cell 관리 전이는 별도의 명시적 명령이다. 커널 자원 action
capability는 계속 `UNSUPPORTED`다.

Linux는 이 경로에서 process·network·driver 실행 기반을 제공한다. AIOS의 native K1
1024B 및 K2-a 256B ABI는 별도로 유지한다. hosted 결과로 native 커널의 Linux module
호환, 실기기 지원, process isolation 또는 자원 소유권이 증명되는 것은 아니다.

## 2. 세 권위와 identity

| 소유자 | 소유하는 값 | 다른 것으로 대체할 수 없는 값 |
|---|---|---|
| MAIN producer | stable service UUID, 실행 instance UUID, source generation, 모델 준비 상태 | canonical Node/Cell ID와 binding generation |
| hosted management authority | authority UUID, typed Cell/Node/NodeBit, parent 관계, canonical·binding generation | PID, Linux boot ID, 모델 이름 |
| Linux / model backend | process lifetime, boot ID, backend version, 실제 inference 응답 | AIOS principal, Cell owner, canonical validity |

hosted authority namespace는 `aios-hosted-management`다. 초기화 때 만든
`authority_instance` UUID는 그 관리 저장소의 수명을 나타낸다. Cell namespace `cell`,
ID `1`, generation `1`과 Node namespace `node`, ID `101`, kind `ai-service`,
generation `1`을 관리 권위가 직접 소유한다. Node는 active Cell 1을 정확히 하나의
부모로 참조한다. 같은 authority 아래의 typed NodeBit도 유효한 부모 Node를 참조한다.

완전한 관리 identity는 authority namespace·instance와 typed ID·generation의 결합이다.
여기서 Node 101은 native semantic oracle와 같은 역할을 재현하는 hosted record다.
native K1의 boot-local Node 101과 동일 인스턴스라고 주장하지 않는다. 새 관리 저장소를
만들면 authority instance가 바뀌므로 과거 결속은 새 저장소에 그대로 적용할 수 없다.

producer가 source record에 canonical ID를 적어 스스로 결속을 발급할 수 없다.
adapter는 관리 권위가 검사하고 반환한 binding만 사용한다. Linux `process_id`와
`host_boot_id`는 source 수명 확인에만 사용하며 canonical namespace에 넣지 않는다.

## 3. MAIN source record와 세대

별도 versioned copied record는 다음 의미를 고정한다. 아래 이름은 새 hosted 계약이며
H1 v1의 native 숫자·문자열 mapping에 추가하거나 그 계약을 느슨하게 만들지 않는다.

| 필드 | 의미 |
|---|---|
| `schema_version` | hosted source schema 1 |
| `source_namespace` | `linux-userspace-service` |
| `source_id` | producer가 유지하는 stable service UUID |
| `source_instance` | 실행마다 새로 만든 UUID; retired instance 재사용 금지 |
| `source_generation` | 해당 instance의 semantic 상태 세대; 양의 정수, 새 instance는 1부터 |
| `service_start_generation` | 저장소 안의 시작 횟수 세대; source 수명 보조 증거 |
| `source_kind`, `source_role` | `ai-service`, `main` |
| `lifecycle_state` | `active` 또는 `exited`; 응답 실패와 process 종료를 구별 |
| `producer_owned`, `copied_read` | 둘 다 true; producer가 소유하는 복사본 읽기 |
| `model_ready`, `model_sha256` | 실제 모델 준비 여부와 준비한 bytes의 SHA-256 |
| `warmup_request_sha256`, `warmup_response_sha256` | 해당 실행의 실제 준비 요청·응답 증거 참조 |
| `completed_requests` | 완료 요청 수; source 또는 binding generation으로 사용하지 않음 |
| `host_boot_id`, `process_id`, `source_only` | Linux 수명 보조 정보와 source-only 경계 |

시간·heartbeat·요청 count의 변화만으로 source generation을 만들지 않는다. readiness,
모델 identity 또는 lifecycle처럼 결속 유효성에 영향을 주는 상태 변경은 producer가
source generation으로 나타내며, 같은 instance의 세대는 감소하거나 0이 될 수 없다.
서비스 시작 세대와 source generation은 다른 계약이다. CONSOLE_RUNTIME의 재시작
세대를 H1의 새 instance마다 1에서 시작하는 source generation으로 cast하지 않는다.

binding generation은 관리 권위가 유효한 결속 전이를 수락할 때 소유하는 세대다.
canonical generation은 canonical target lifecycle의 세대이므로 source가 재시작했다는
이유만으로 증가시키지 않는다. 관측 순서와 snapshot 순서는 이 둘의 대체물이 아니다.

## 4. 모델 준비와 요청 처리

모델 준비는 파일 존재, manifest 이름, 서버 port 열림 또는 health 응답만으로 완료되지
않는다. 아래를 같은 producer instance의 증거로 연결해야 한다.

1. 공식 release·immutable revision·다운로드 URL·크기·SHA-256을 고정한 backend와
   모델 bytes를 확인한다. 실행 파일 version 출력과 실제 launch 인자를 보존한다.
2. 준비한 모델을 지정해 backend를 시작하고, 지정 endpoint의 준비 상태를 확인한다.
3. 제한된 실제 warmup 요청을 전송한다. 성공 HTTP 응답을 strict JSON으로 읽고
   비어 있지 않은 생성 텍스트와 양의 생성 token 증거를 확인한다.
4. request/response 원문 bytes와 hash, 요청 ID, model identity, elapsed time,
   outcome 및 종료 이유를 보존한 뒤에만 `model_ready=true`를 발행한다.
5. 실패·시간 초과·잘못된 응답은 정해진 error로 남긴다. fixture 응답, 상수 문자열,
   빈 응답 또는 이전 실행의 warmup을 live readiness로 받아들이지 않는다.

사용자 요청도 입력 크기·출력 크기·생성 token·시간을 제한한다. 출력은 terminal control
문자와 prompt 위장을 제거해 표시한다. 요청 원문 hash와 응답 hash, source tuple,
요청 당시 binding generation을 같은 request record로 연결한다. 이미 잘린 preview의
hash를 전체 응답 hash라고 표현하지 않는다. 모델 응답은 정답이나 tool 실행 권한을
증명하지 않으며 사용자는 처리 결과와 오류를 CLI에서 구분할 수 있어야 한다.

backend/model은 외부 실행 dependency다. 저장소 안에 upstream 코드를 복사하거나
link하지 않고 별도 다운로드 cache와 provenance를 사용한다. license·NOTICE는
artifact와 함께 보존하되 이것을 code import 승인이나 모든 배포 형태의 허가로 표현하지
않는다. 임의 시스템 서비스를 설치하거나 기존 backend를 종료하지 않는다.

## 5. 발견·결속·재결속

관리 순서는 `initialize → discover → bind → observe`다. source가 없거나 정확히
하나의 active MAIN을 선택할 수 없으면 bound count는 증가하지 않는다. 발견은 결속이
아니며, 결속은 현재 source instance·generation과 kind·role·readiness를 확인해야 한다.

동일 source의 관측이 성공하면 현재 binding 아래의 상태를 읽는다. source semantic
generation 또는 instance가 바뀌면 기존 binding은 current가 아니다. 새 source를
발견하고 명시적 `reconcile`이 성공해야 증가한 binding generation을 발급한다.
observed source와 retained bound source를 따로 보존해야 이 전이를 재생할 수 있다.

정상 stop은 source `exited` 증거와 실제 process 종료를 연결한다. abrupt death는
종료 기록이 없다는 사실을 남기고 live 조회 실패를 stale로 처리한다. 이전의 READY
파일을 읽는 것만으로 process 생존 또는 현재 결속을 복구하지 않는다. collector
재시작·host reboot·PID 재사용은 서비스의 명시적 재시작과 구별되는 후속 H3 사례다.

최소 live 시나리오는 첫 질문 성공, CLI 종료·재접속, 같은 producer 질문 성공,
서비스 재시작, 과거 binding의 stale 거부, 새 source 발견·명시적 재결속, 다시 질문
성공, 서비스 정상 중지다. 재접속은 새 CLI process로 확인하고 마지막 backend까지
실제로 종료됐는지 별도로 판정한다.

## 6. 독립 verifier 수용 조건

producer는 증거를 만들고 verifier가 판정한다. runtime의 `PASS`, `model_ready` 또는
`binding_current` 주장 자체를 정답으로 사용하지 않는다.

| 검사 | 통과에 필요한 증거 |
|---|---|
| transport | exact field set·type·enum, duplicate key·non-finite·잘림·한도·순서 거부 |
| authority | namespace·instance 일치, Cell/Node/NodeBit 부모 유효성, exact-one 관계 |
| source | namespace·kind·role·producer-owned·copied-read, 유효 instance와 세대 |
| 모델 | 실제 bytes/provenance, same-instance warmup 요청·응답, 비어 있지 않은 생성 |
| 요청 | raw bytes/hash·source tuple·binding generation·실제 결과의 교차 일치 |
| 전이 | first bind, source 변경의 stale, explicit reconcile, retired instance 거부 |
| 실행 | CLI와 service/backend의 실제 process 결과, timeout·강제 종료 분리 |
| 재현성 | 새 artifact directory, 당시 runtime source와 입력 hash, raw/normalized/verdict 분리 |

필수 반례는 missing source, duplicate MAIN, orphan parent, unknown namespace,
kind·role mismatch, zero/rollback/stale generation, 다른 authority, retired instance
재사용이다. 모델 hash mismatch, backend 준비 실패, 빈 completion, timeout, 다른 실행의
receipt, hash를 다시 계산한 변조도 거부한다. fixture는 semantic 검증에 사용하지만
live runtime acceptance에 사용하지 않는다. 실패 뒤 정상 종료했다고 실패를 지우지 않는다.

H1의 `binding-trace-v1.contract.json`과 기존 native projection은 그대로 재검증한다.
새 source를 `native-slm-agent-tree`로 바꾸어 H1 PASS를 얻지 않는다. 공통 field/reject
의미의 대응과 live hosted 계약 검증을 분리하며, 양쪽 raw 입력의 차이를 숨기지 않는다.

## 7. 실행 표면과 문서 연결

다음 MAIN 명령은 CLI v0.3에 도입됐으며 현재 v0.7에서도 제공한다. 당시 실제 Linux
통합 실행 결과는 §8에서 구분하고, 후속 Cell 관리 검증은 아래 Cell 수명 가이드를 따른다.
기존 `service` 명령은 CONSOLE_RUNTIME용이다.

```text
agent start                 # 실제 모델 준비를 포함한 MAIN 시작
agent status                # producer와 모델 상태
room discover               # current MAIN 발견
room bind                   # 최초 명시적 결속
room status                 # hosted 관리 계층과 현재 결속
ask AIOS의 역할을 설명해줘   # 실제 모델 요청
agent restart               # 새 실행 instance
room discover
room reconcile              # 새 source와 명시적 재결속
agent stop
```

CLI는 `--agent-dir`, `--agent-config`로 서비스 저장소와 실행 설정을 받는다.
별도 `aios-agent.py`는 `--state-dir`, `--config`와 lifecycle action을 받으며 관리 action은
`room-status`, `room-discover`, `room-bind`, `room-reconcile` 및
`cell-status`, `cell-activate`, `cell-deactivate`다. `resources-link`, `resources-status`, `resources-sample`은
별도 자원 관측 명령이다. Windows 도구의 VM 종료와 Linux에서 CLI만 닫는 동작은 구분한다.

CLI의 `cell status/activate/deactivate`는 기존 Cell 1의 관리 상태와 세대를 다룬다.
비활성화해도 MAIN/backend 프로세스는 살아 있으며 기존 결속의 신뢰는 무효화된다.
재활성화 뒤 `room discover`, `room reconcile`, `resources link`가 필요하다.
이 bounded 관리 전이는 `PARTIAL`이며 `cell-01`은 보존된 CLI v0.5 소스로 로컬 Linux·실제 모델 검증을 통과했다.
[Cell 수명 가이드](aios_cell_lifecycle_guide_ko.md)가 정확한 전이·세대·실행 증거를 소유한다.

현재 `backend status/start/stop/restart`는 MAIN과 별도인 모델 프로세스 수명을 관리한다.
MAIN `start/restart`는 CLI의 `--backend-dir`에서 실행 대상을 확인하고 고정한다.
backend 교체·종료를 발견하면 MAIN이 살아 있어도 readiness와 결속 신뢰를 무효화한다.
복구는 `backend restart → agent restart → room discover → room reconcile → resources link`로 수행한다.
[backend 수명 가이드](aios_backend_lifecycle_guide_ko.md)의 구현은 `PARTIAL`이며
`backend-02`에서 로컬 Linux·실제 모델·교체 후 요청 거부·명시적 복구·자원 관측·
인터넷·정상 종료와 당시 v0.6 보존 소스의 독립 재검증을 통과했다.
현재 v0.7의 `backend recover`는 같은 CLI가 미리 확보한 child pidfd를 사용한 supervisor
소실 뒤 명시적 정리다(`PARTIAL`, 실제 Linux·모델 기록의 별도 독립 재검증 PASS; 원본 FAIL 보존). 별도 `RECOVERED` 증거와
그 뒤의 MAIN 재시작·발견·재결속은 위 backend 수명 가이드를 따른다.

기존 [부팅·하드웨어 가이드](aios_userspace_boot_hardware_guide_ko.md)와
[CLI·인터넷 가이드](aios_cli_internet_guide_ko.md)의 verified evidence는 보존한다.
새 session schema가 필요한 경우 과거 artifact는 당시 보존한 source로 재검증한다.

### 7.1 구현된 실행 제한

현재 CLI v0.7은 명시적 backend 복구를 포함한 session schema 7과 runtime source 31개의 hash를 남긴다.
과거 schema 1/source 8개, schema 2/source 12개, v0.3/schema 3/source 20개,
v0.4/schema 4 및 v0.5/schema 5/source 25개, v0.6/schema 6/source 31개의 계약도 당시 보존 소스로 재생한다. 자원 관측의 계약·검증
범위는 [자원 관측 가이드](aios_resource_observation_guide_ko.md)를 따른다. MAIN protocol과 개별 실행은
schema 4/source 24개(이전 run schema 1은 13개, schema 2/3은 18개)와 설정 hash, warmup, 요청 원문, source 전후 상태, 관리 snapshot,
event journal, terminal result를 남긴다. console 기록만으로 서비스 종료를 판정하지 않는다.
receipt schema 2는 `backend_execution`을 추가한다. live 성공은 초기 고정 descriptor와
일치하는 before/send/after 실행 증거가 필요하며, 실제 연결의 양끝 socket과 프로세스
연속성을 확인한다. fixture의 null 증거는 실제 요청 대상 검증으로 승격하지 않는다.
과거 receipt schema 1은 해당 실행의 보존 소스로 계속 검증한다.

기본 모델은 immutable revision의 Qwen3-0.6B Q8_0 GGUF이며 CPU 전용 llamafile
0.10.5 thin backend를 별도 프로세스로 실행한다. 639,446,688 bytes의 모델과 backend는
호스트와 Linux guest에서 SHA-256을 확인한다. 모델 disk는 읽기 전용이고 서비스는
일반 사용자로 실행한다. guest 내부 loopback endpoint만 기본 설정으로 사용한다.

CLI 전체 명령은 2,048자로 제한하며 모델 입력은 추가로 UTF-8 4,096 bytes를 검사한다.
응답 원문은 16 KiB, warmup은 최대 8 tokens, 사용자 응답은
최대 64 tokens로 제한한다. 모델 요청의 전체 시간 한도는 420초다. QEMU TCG의 CPU
추론은 느리므로 이 개발 경로를 실시간 대화 성능 증거로 사용하지 않는다. 각 서비스
실행의 journal은 64개 event, 요청은 64개, 저장소의 시작 세대는 64회로 제한한다.
한도에 도달하면 요청을 거절하며 stop/status를 위한 여유를 유지한다.

private state directory와 Unix socket의 owner·권한·peer PID를 확인하고, stop은 실제
process 종료를 기다린다. 저장된 READY만으로 생존을 복구하지 않는다. fixture backend는
기록마다 명시적으로 표시되며 live verifier를 통과할 수 없다.

### 7.2 Windows 개발 VM 실행

저장소 루트에서 다음 실행 도구를 사용한다. QEMU와 Python 3.11 이상이 필요하다.
`-Agent`는 모델이 준비된 대화형 콘솔이고, `-AgentSmoke`는 아래 수용 시나리오를
자동 실행한다. 최초 모델·backend 준비는 약 682 MB를 내려받아 사용자 cache에 보관한다.

```powershell
.\tools\hosted\Start-AiosConsole.ps1 -Agent
.\tools\hosted\Start-AiosConsole.ps1 -AgentSmoke -GuestTests
.\tools\hosted\Start-AiosConsole.ps1 -CellSmoke -GuestTests
.\tools\hosted\Start-AiosConsole.ps1 -BackendSmoke -GuestTests
```

콘솔 안에서는 `agent start → room discover → room bind → ask ...` 순서로 시작한다.
`agent restart` 뒤에는 `room discover → room reconcile`이 필요하다. `exit` 자체는
MAIN을 중지하지 않지만 이 Windows 개발 VM 도구는 마지막 콘솔 종료 뒤 MAIN과
별도로 시작한 model backend를 종료하고 VM을 끈다. VM 내부 저장소는 임시이며 다음
VM 부팅에 유지되지 않는다. Linux에서 CLI만 다시 여는 것과 VM 재부팅을 구별한다.

`-AgentSmoke` 결과 디렉터리에는 두 console session, 당시 runtime source, MAIN의 두
run, 요청·응답 원문, 관리 상태, backend command/output/exit, 모델 bytes 검사 결과와
공식 upstream receipt를 함께 보존한다. 독립 재생은 다음과 같다.

```powershell
py -3 tools/hosted/verify_agent.py <새-실행-결과-디렉터리> --workflow
```

`-CellSmoke`는 Cell 1 비활성화·재활성화와 명시적 재결속을 검증하는 별도 시나리오다.
결과는 `py -3 tools/hosted/verify_agent.py <Cell-실행-결과-디렉터리> --cells`로 재검증한다.
실제 모델·Linux 실행 결과는 Cell 수명 가이드에서 fixture 검사와 구분한다.
`-BackendSmoke`는 backend 교체 후 MAIN의 요청 거부와 명시적 재시작·재결속을 검사하는
별도 시나리오이며 `backend-02`에서 실제 Linux·모델·인터넷·정상 종료 검증을 완료했다.
당시 소스 대조와 독립 재검증을 포함한 결과는 backend 수명 가이드에 기록한다.
`backend-02`는 보존된 소스의 증거이며 이후 변경된 현재 소스의 검증을 대신하지 않는다.

대화형 `-Agent` 결과는 같은 도구의 `--interactive`로 재검증한다. MAIN을 시작하지 않은
경우에는 `ABSENT`, 빈 MAIN 저장소, 실제 console·backend·VM 종료를 확인하고 정상 종료를
허용한다. 이 결과는 warmup·질문·결속 또는 MAIN process 종료의 증거로 사용하지 않는다.

입력 prompt와 응답 원문이 해당 로컬 증거에 저장된다. 서비스의 사용자 홈 기본 경로와
owner/0700 검사는 구현돼 있다. 기록 삭제·영속 보관 정책 및 AIOS 자체 다중 사용자
principal/ownership 관리는 후속이다.

## 8. 실행 증거와 남은 범위

아래는 v0.3의 `agent-02`와 `interactive-03`에 보존한 실행 이력이다. 후속 v0.4 자원
관측과 v0.5 Cell 관리 전이의 검증 결과는 각각의 가이드에서 확인한다.

| 항목 | 당시 실행·재검증 결과 |
|---|---|
| MAIN 실제 warmup·사용자 inference | `PASS`: agent-02의 실제 warmup 2회와 사용자 요청 3회 |
| hosted authority·source·binding 독립 verifier | `PASS`: 원본 agent-02를 수정된 독립 verifier로 재생한 agent-replay-verdict.json |
| CLI 재접속·restart·stale·reconcile | `PASS`: 같은 producer 재접속, 새 instance 이후 stale 거부, binding generation 1→2 |
| malformed·kind·generation·readiness 반례 | `PASS`: Linux 일반 사용자 hosted 테스트 226개 / 147.464초, skip 없음; 이 안의 MAIN runtime 16개 모두 통과 |
| source hash·backend/model provenance | `PASS`: 당시 v0.3 runtime source 20개와 보존 snapshot 일치; host/guest model 전체 읽기 hash·backend pin·upstream receipt 11개 검증. 이후 v0.4/v0.5 소스 변경과 구별 |
| primary exact Linux baseline / remote exact-SHA acceptance | 미검증 |

당시 Windows hosted 전체 테스트는 356개 / 66.834초로 통과했다(Linux 전용·symlink 28개 skip).
그 뒤 보완한 최종 MAIN runtime은 별도 Windows 16개 중 4개 통과·Linux 전용 12개 skip,
위 실제 Linux에서는 16개 모두 통과했다. 최종 독립 MAIN verifier 30개도 Windows에서
통과했다. H1 host-only 검증과 Linux resource guard의 13개 source row·`code_import=0`을
유지하며 native kernel/public header는 변경하지 않았다.

agent-02의 실행 환경은 QEMU 10.2.0 / q35 TCG / CPU max / vCPU 2 / RAM 3 GiB,
Alpine virt 3.24.1 / Linux 6.18.35-0-virt / Python 3.14.7 / 일반 사용자 UID 1000이다.
두 MAIN의 stable source ID는 `4b75e0a1-cd77-4423-8e50-cbfe1f289326`, instance는
`ba2a9523-4154-41ac-9d0e-55dcbe027a87` → `f2f2ee79-f388-46bc-9589-712845cdec5a`다.
hosted authority `73a2e13a-3231-49e2-9585-a299472ea86a`는 유지하고 명시적 binding 세대만
1→2로 진행했다. 첫/두 번째 CLI session은 `86cd46c7-3f0a-4946-9ba2-0c005ed4f887`와
`6059f544-1d4a-44d6-a893-9c83420031ea`로 서로 다르다.

| 실제 사용자 요청 | 생성 결과 | 생성 token | 요청 시간 |
|---|---|---|---|
| France의 수도 | `The capital of France is Paris.` | 8 | 177.821초 |
| CLI 재접속 후 인사 | `Hello!` | 3 | 127.134초 |
| restart·재결속 후 Moon 설명 | `The Moon is a natural satellite of Earth, orbiting it in the solar system.` | 18 | 227.478초 |

각 실행의 warmup은 각각 167.088초, 167.758초이며 서로 다른 request ID를 가진다.
실행 증거는 모델의 정답률·한국어 품질·실시간 성능 보증이 아니다. backend load는
29.260초, 최종 backend exit는 0, VM exit도 0이며 `host_killed=false`다.

`build/hosted-agent/agent-01`은 Linux 일반 사용자 테스트 224개 중 native semantic
counterpart 검사 1개가 읽기 전용 share의 C header 누락으로 실패했다(124.692초).
MAIN runtime 14개 검사는 통과했지만 전체 run은 FAIL이며 실제 모델 시나리오에
진입하지 않았다. runner가 `kernel_room_management.h` 비교 원본을 share에 포함하도록
수정했다. 이 파일은 테스트 입력이며 Linux 런타임에서 native ABI를 import하지 않는다.

agent-02의 최초 최종 판정도 보존했다. 실제 모든 MAIN 시나리오와 정상 종료를 마친 뒤,
마지막 `localhost:~# [ 1178.297624] reboot: Power down` 줄의 셸 프롬프트 접두사를 종료
parser가 인식하지 못해 `vm_shutdown_record`로 FAIL을 기록했다. 정확한 이 접두사만
허용하고 인용 문자열·임의 접두사·중복 종료·종료 뒤 출력은 계속 거부하도록 수정했다.
runner는 이후 poweroff 전에 줄바꿈을 출력한다. 원본 `vm-verdict.json`과
`agent-verdict.json`을 덮어쓰지 않고 별도 독립 판정을 `agent-replay-verdict.json`에
저장했다. 이 재생 기록에는 원본 판정 hash와 적용한 verifier 5개의 hash가 포함된다.

이후 `build/hosted-agent/interactive-03`에서 실제 Windows `-Agent` 실행 도구에
`help → resolve example.com → fetch https://example.com/ → exit`를 입력했다.
MAIN을 시작하지 않은 대화형 종료는 `ABSENT`, user/warmup request 0으로 PASS였고,
backend와 VM은 exit 0, `host_killed=false`, 정상 powerdown으로 끝났다. session
`90798ba8-8247-41b1-8b44-a53a4b84bbc4`의 실제 DNS는 주소 4개를 조회했고 HTTPS는
인증서 검증 성공·200·559 bytes였다. 당시 보존 소스와 독립 verifier로 `--interactive` 및 별도 console
`--execution --require-live --require-internet` 재검증을 모두 통과했다. 이 실행에서
poweroff 전 줄바꿈 수정도 확인했다.

후속 MAIN/backend 각각의 CPU/RSS·system PSI 관측은 자원 관측 가이드의 resource-02에,
현재 Cell 1 관리 전이의 검증 상태는 Cell 수명 가이드에 분리한다. 구현을 추가했다는
이유로 전체 H2/H3를 CURRENT로 올리지 않는다. 현재 backend 자체 교체·종료의 관리·실행
결속은 별도 가이드의 `backend-02`에서 로컬 실제 실행과 당시 소스 재검증을 통과했다.
이 기록은 보존된 소스의 증거다.
성숙도는 `PARTIAL`이다. 현재 CLI 31개 소스의 supervisor 복구·실제 모델 기록은 별도
독립 재검증을 통과했다. 현재 source 35개의 모델 운영 이미지와 동일 worker의 recover·즉시 exit는
[운영 이미지 가이드](aios_operating_image_guide_ko.md)의 별도 실제 acceptance를 통과했다.
CLI 소실·재부팅 이후 복구, network/storage 등 전체 workload·per-Cell pressure/ownership,
collector restart·host reboot 등 전체 H3, 범용 설치·영속 AI 상태·업데이트,
native/hosted conformance는 후속이다.
K5 principal·ownership·authorize와 rollback 이전의 resource apply는 계속 PLANNED다.

모델 없는 기본 CLI 운영 이미지는 [운영 이미지 가이드](aios_operating_image_guide_ko.md)의
`image-07`에서 반복 부팅·설정/history 보존을 검증했다(`SUPPORTING/PARTIAL`).
이 기록은 MAIN을 시작하거나 부팅 간 readiness·binding을 복원한 증거가 아니다.

## 9. 공식 자료 검토

- [llamafile 0.10.5 release](https://github.com/mozilla-ai/llamafile/releases/tag/0.10.5)와
  [고정 commit의 quickstart](https://github.com/mozilla-ai/llamafile/blob/486e6c5f9356eae50b851b07517bfae1f2420193/docs/quickstart.md):
  외부 GGUF weights와 CPU 전용 thin 실행 범위를 확인했다. 특정 Linux guest에서의 성공은
  공식 설명과 별도로 실제 실행으로 검증한다.
- [Qwen3-0.6B-GGUF 고정 revision](https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/blob/23749fefcc72300e3a2ad315e1317431b06b590a/README.md):
  모델 제공 형식과 prompt template을 검토했다. 모델 revision과 GGUF SHA-256을 고정하며
  이를 최신 모델·최고 성능 선정으로 표현하지 않는다.
- [Python subprocess](https://docs.python.org/3/library/subprocess.html): process 시작·종료와
  timeout의 실제 결과를 별도로 보존하는 경계에 참고했다.
- [Python socket](https://docs.python.org/3/library/socket.html): bounded local control과
  socket timeout을 검토했다. API 존재는 AIOS canonical 결속 증거가 아니다.

초기 자료는 2026-09-04에 검토했고 고정 backend/model 문서는 2026-09-07에 다시 확인했다.
움직이는 웹 문서의 설명과 실제 실행에 사용한
backend release·model revision·파일 hash는 구분해 artifact에 남긴다.
