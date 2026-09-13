# AIOS hosted 모델 backend 수명과 MAIN 실행 결속

> 개발 경계 — 2026-09-13: 환경 문맥·오류 안내에 이어 v0.10은 UUID Task의
> 접수·조회·결과·동일 CLI 소유 backend의 명시적 취소를 연결한다(`PARTIAL`).
> 버전·schema·source 수와 보존된 v0.8 실제 소비·v0.10 fixture의 범위는
> [환경 문맥 가이드](aios_space_context_guide_ko.md)가 소유한다. 실제 모델 Task의 한정 흐름은 PASS이며
> source 39개 운영 이미지 acceptance는 미완료다. 본문의 과거 계약·실행 기록·source
> 일치 주장은 각 보존 소스의 당시 범위이며 새 개발 소스의 검증으로 승계하지 않는다.
> 이번 문서 검토는 외부 자료 재조사나 runtime 재실행 판정이 아니다.

이 문서의 실행 ID별 `build/` 자료는 로컬 비추적 증거이며 Git checkout에 포함되지 않는다.
과거 결과의 재검증 명령은 해당 원본을 보유한 환경용이며 새 실행의 결과 경로와 구분한다.

> 방향: `DIRECT` — AIOS CLI가 모델 backend 수명을 관리하고 MAIN의 실제 요청 대상을 검증한다.
> 성숙도: `PARTIAL` — backend-02에서 로컬 실제 Linux·모델·교체/복구·인터넷·정상 종료를 검증했다.
> 증거 경계: `backend-02`는 당시 보존 소스의 기록이다. 이후 기록 경로 수정은 model-image-04의 별도 실제 부팅·모델 증거로 검증했다.
> 후속 recover: `PARTIAL` — CLI v0.7/session 7의 동일 CLI가 보유 pidfd로 supervisor 소실 뒤 자식을 정리한다. `recovery-model-02`의 실제 Linux·모델 기록은 독립 재검증 PASS이며 원본 FAIL을 보존한다. 별도 운영 장애 복사본 02도 같은 worker의 recover·즉시 exit·정상 종료를 실제 검증했고 01의 원본 FAIL은 보존한다.
> 작성: 2026-09-07 / 갱신: 2026-09-08

## 정체성과 책임

AIOS의 Cell·Node·binding은 [Kernel Room 관리 모델](../kernel-room/kernel_room_management_model_ko.md)의
독립 관리 정체성을 따른다. Linux는 프로세스, 드라이버, TCP 연결을 제공한다.
Linux PID·boot ID·시작 tick·socket inode는 실행 출처의 관측 증거이며 canonical ID가 아니다.
기존 [MAIN 결속](aios_agent_binding_guide_ko.md), [자원 관측](aios_resource_observation_guide_ko.md),
[Cell 수명](aios_cell_lifecycle_guide_ko.md) 계약을 유지한다.

모델 backend를 시작하는 제품 코드는 `hosted/linux/aios_backend/`에 둔다.
`tools/hosted/`는 VM 구성, 외부 모델 준비, 증거 추출과 독립 판정을 담당한다.
llamafile과 Qwen은 고정된 외부 실행 의존성이다. Linux source policy 13개와 schema 1,
`code_import=false`, 네이티브 K1·K2-a ABI, H1 host-only 범위는 바꾸지 않는다.

## 명령과 수명

CLI v0.6에서 도입한 기본 명령은 다음과 같다. backend 서비스와 MAIN 서비스는 각각 독립된 수명을 가진다.
v0.7의 별도 `backend recover` 계약과 실제 기록의 독립 재검증은 아래 후속 절에서 구분한다.

| 명령 | 동작 |
|---|---|
| `backend status` | 인증된 현재 supervisor와 실제 자식 생존 상태 확인 |
| `backend start` | 고정 profile의 backend 자식 하나를 소유하는 supervisor 시작 |
| `backend stop` | 소유한 자식을 종료하고 실제 supervisor 종료까지 확인 |
| `backend restart` | 기존 자식·supervisor 종료 확인 후 새 instance 시작 |

한 supervisor instance는 하나의 자식만 시작한다. `service_id`는 유지하고 시작마다
`start_generation`을 1씩 올리며 `instance_id`를 새로 만든다. 최대 64회다.
자식의 Linux 실행 정체성과 attester source instance는 별도 값으로 기록한다.
같은 endpoint를 다시 쓰더라도 이전 실행 대상과 동일하다고 간주하지 않는다.

private state directory와 lock, 같은 사용자 IPC, pidfd 및 열린 `/proc` 핸들을 사용한다.
descriptor와 attestation은 instance별 `runs/<instance>/`에 남기고 두 socket은 state root에 둔다.
backend 자식의 stdout·stderr는 각각 1 MiB로 제한하면서 계속 비운다.
준비 확인은 제한된 health 응답과 실제 소유 listener를 모두 요구한다.
예상하지 않은 자식 종료는 exit code 0도 실패이며 저장된 ready를 생존 증거로 재사용하지 않는다.

정상 중지는 TERM 후 최대 20초 안에 자식 종료를 확인한다. 강제 KILL이 필요하면 성공으로
승격하지 않는다. supervisor 강제 소실 뒤 남은 자식의 자동 인수·정리 기능은 제공하지 않는다.
확인할 수 없는 저장 상태에서는 stale로 거부하며 기록된 PID만으로 임의 프로세스를 종료하지 않는다.
시작 시도 기록도 최대 64개로 제한한다. 별도의 private nonblocking 예약 lock 안에서
한도 확인과 디렉터리 생성만 수행하고, 실제 프로세스 시작·모델 준비 대기 전에 lock을 놓는다.
상태 조회와 중지는 이 예약 lock을 사용하지 않는다.

## MAIN이 고정한 실행 대상

MAIN 시작 시 backend의 private attestation을 확인하여 descriptor를 고정한다.
warmup과 모든 실제 모델 요청은 이 descriptor를 사용한다. 요청 worker는 TCP를 먼저 연결한 뒤
클라이언트와 서버의 역방향 주소·포트 쌍, ESTABLISHED socket inode와 backend 소유 fd,
backend와 supervisor의 boot ID·PID·시작 tick을 확인하고 나서 prompt를 전송한다.
소유권 확인 이후 HTTP 자동 재연결을 허용하지 않으며 응답 뒤에도 같은 실행 대상과 listener를 확인한다.
이 단계가 실패하면 모델 요청 성공 증거를 만들지 않는다.

이 검사는 CPU·RSS·PSI 관측과 별개다. 선택적 자원 관측 실패만으로 정상 모델 응답을 버리지 않는
기존 계약은 유지하지만, 실제 요청 대상의 실행 정체성이 검증되지 않으면 성공할 수 없다.
MAIN은 attester에 매번 의존하지 않고 보유한 프로세스 핸들과 직접 읽은 실행 증거로 연속성을 확인한다.

MAIN status 또는 명령을 처리할 때 고정된 backend가 종료·교체된 것을 발견하면
`BACKEND_INVALIDATED`를 한 번 기록한다. MAIN 프로세스는 살아 있지만 `model_ready=false`가 되고
source generation을 올려 binding trust와 discovery를 폐기한다. 이후 `ask`는 실제 receipt 없이 거부한다.
관리 상태 조회에도 이 생존 상태 갱신이 먼저 수행될 수 있다.

복구는 명시적으로 수행한다.

```text
backend restart
agent status
agent restart
room discover
room reconcile
resources link
ask What is the capital of France? Answer in one short sentence.
```

새 MAIN은 새 backend로 warmup하고, 사용자가 canonical binding과 자원 관계를 다시 연결한다.
실행 중인 MAIN의 warmup 계약을 조용히 다른 backend로 치환하지 않는다.

## 증거와 호환성

기존 CLI/session schema 6은 제품 source 31개, MAIN protocol/run schema 4는 source 24개를 기록한다.
MAIN `backend-binding.json`은 초기 descriptor와 인증 proof를 고정한다.
inference receipt schema 2는 기존 요청·응답 증거에 `backend_execution`을 추가하며
성공한 live warmup과 user request는 before/send/after 원시 실행 증거를 모두 요구한다.
명시적 fixture는 실제 모델 증거로 승격하지 않는다. backend descriptor와 관리 저장 schema는 1을 유지한다.

backend run은 시작·자식 시작·준비·중지·종료의 순서, config/source hash, 실제 종료 상태,
bounded 로그와 파일 hash 목록을 보존한다. 독립 검증기는 같은 endpoint의 두 실행을 구별하고
MAIN 초기 proof·실제 요청 proof·resource proof를 해당 backend run과 대조한다.
이전 CLI 1–5, MAIN run 1–3, receipt 1은 각각 보존된 source로 다시 판독한다.
이전 verdict나 source snapshot은 덮어쓰지 않는다.

### CLI v0.7/session 7의 명시적 `backend recover` — `PARTIAL`, 실제 실행의 독립 재검증 PASS

2026-09-08 `recovery-model-02`에서 동일 CLI의 감독 프로세스 소실·잔존 모델 정리·새 세대
시작·MAIN 재결속·실제 응답·인터넷·정상 종료를 실행했다. 최초 검증기의 오류 기대값을
수정한 별도 독립 재검증은 PASS이며, 원본 실행의 FAIL 판정은 보존한다. 아래 실제 증거
절이 이 조각의 정본이다. 기존 v0.6 `backend-02`와는 별도 실행이며, 현재 CLI source
31개에 한정한다. 현재 source 35개의 운영 이미지에서 동일 worker가 recover 후 즉시
exit하는 별도 acceptance도 통과했으며, 아래 운영 이미지 증거 절에서 구분한다.

backend control schema 1은 `recover` action과 `RECOVERED` state를 추가한다. backend
source 15개·CLI source 31개·이미지 source 35개의 목록 수는 유지하지만 해당 파일 bytes는
이전 실행과 달라진다. CLI session schema 7의 새 기록과 별도 recovery receipt를 함께
판독하며 과거 schema 6 증거에 recover를 소급 적용하지 않는다.

복구 범위는 **backend를 직접 시작한 동일 CLI가 계속 살아 있고**, RUNNING supervisor와
그 자식을 인증한 시점부터 child pidfd를 계속 보유한 경우다. 이 메모리상의 lease는
CLI → supervisor → model child의 실제 부모 관계와 boot ID·시작 tick·descriptor를
함께 확인한다. 저장된 PID나 JSON receipt를 다시 읽어 새 CLI가 복구 권한을 얻지 않는다.
별도 CLI 재접속, CLI 자체 종료·crash, 재부팅 뒤 orphan 인수, RUNNING 인증 전 startup
실패와 일반적인 프로세스 탐색·정리는 이 조각의 범위가 아니다.

구현 책임은 [aios_backend/client.py](../../hosted/linux/aios_backend/client.py)의
`_acquire_lease`·`_recover`, [protocol.py](../../hosted/linux/aios_backend/protocol.py)의
명령·응답 계약, [aios_console/shell.py](../../hosted/linux/aios_console/shell.py)의
사용자 진입점으로 나눈다. 복구 시 아래 조건을 모두 확인한다.

| 경계 | 필요한 조건과 실패 동작 |
|---|---|
| 이전 인증 | 같은 CLI가 직접 시작한 supervisor의 인증된 RUNNING 응답과 descriptor를 확인한 뒤, 아직 살아 있는 child의 pidfd를 획득·유지 |
| supervisor 소실 | 보유 Popen과 supervisor pidfd로 실제 비정상 종료·회수를 확인; 여전히 실행 중이거나 정상 exit 0이면 이 복구를 허용하지 않음 |
| 기존 실행 일치 | 현재 registry·마지막 RUNNING 기록·descriptor·state directory가 lease와 일치해야 함; 저장된 PID에 새 핸들을 열어 대상을 채택하지 않음 |
| 자식 종료 요청 | 이미 종료한 child는 신호 없이 확인; 살아 있으면 보유 pidfd로 `SIGTERM`만 한 번 전송하고 최대 20초 동안 종료 관측 |
| 종료 증거 | child pidfd의 실제 종료를 요구; 원래 부모가 아닌 CLI가 알 수 없는 child exit code는 `null`로 남기며 exit 0으로 꾸미지 않음 |
| socket 정리 | 열린 state directory와 기존 두 socket의 device/inode·소유권·`0600`을 대조한 뒤 해당 `control.sock`·`backend.sock`만 제거; 교체된 경로·symlink는 거부 |
| 실패 보존 | lease 부재·잘못된 실행·지원하지 않는 pidfd 신호·시간 초과·socket 교체·기록 실패는 성공으로 승격하지 않음; `SIGKILL` 또는 PID 기반 신호로 fallback하지 않음 |

새 증거는 backend state 아래 `recoveries/<이전-backend-instance>.json`의 별도 recovery
receipt schema 1이다. 이전 `runs/<instance>/`의 RUNNING·부분 로그를 STOPPED로
덮어쓰거나 종료 `result.json`을 만들어 보충하지 않는다. receipt는 이전 run 파일 hash,
제품 source hash, lease 획득 시점의 실행 관측, supervisor 회수, 보유 pidfd의 TERM·종료
시각과 socket 제거 결과를 기록한다. `capture_kind=fixture`는 실제 모델 증거가 아니다.

신호 전 FAILED receipt를 먼저 기록하여 한 번만 쓸 수 있는 복구 시도를 소비한다.
중간 중단·최종 기록 실패·20초 시간 초과 뒤 같은 저장 파일을 재사용해 신호를 반복하거나
부분 정리를 성공으로 바꾸지 않는다. 모든 조건을 충족한 최종 receipt만 `RECOVERED`가
될 수 있다. 이는 기존 자식의 정리 완료이며 backend readiness·MAIN readiness를 의미하지
않는다. 저장 receipt는 이미 완료된 복구 판독에만 쓰며 신호 권한을 부여하지 않는다.

복구 이후의 사용 흐름은 명시적이다. supervisor 장애 주입은 제품 명령이 아니며 실제
검증에서는 외부 도구가 대상 실행을 확인한 뒤 별도 실패 유발 증거로 남긴다.

```text
backend recover
backend start
agent status
agent restart
room discover
room reconcile
ask What is the capital of France? Answer in one short sentence.
```

`backend start`는 성공한 recovery receipt를 확인한 뒤 같은 service ID의 다음
`start_generation`과 새 backend instance를 만든다. 유효한 복구 완료 뒤에는 같은 CLI나
새 CLI가 명시적으로 다음 세대를 시작할 수 있다. 이는 새 CLI가 이전 자식의 신호 권한을
인수한다는 뜻이 아니다. 복구 전의 stale 상태에서는 기존 start/restart도 계속 거부한다.
기존 MAIN의 고정 실행 대상이나
binding을 새 backend로 자동 치환하지 않는다. `agent restart`의 실제 warmup과 명시적
discover/reconcile을 거친 뒤 새 결속으로 질문한다. 자원 관측을 사용하는 경우에는
별도로 `resources link`도 다시 수행한다.

완료 판정에는 같은 CLI의 인증된 RUNNING·lease 획득, 실제 supervisor 소실과 잔존 child,
명시적 recover의 보유 pidfd TERM·종료 관측, 이전 run 보존과 별도 receipt, 다음 세대의
실제 backend·MAIN 시작·warmup·재결속·질문·정상 cleanup 및 VM 종료가 필요하다.
새 CLI·위조 receipt·잘못된 instance·PID 재사용·socket 교체·신호 실패/시간 초과·부분
receipt를 거부하는 독립 검증도 함께 요구한다. fixture 검사·실제 실행 증거·host verdict는
각각 기록한다. fixture 성공만으로 실제 실행을 주장하지 않으며 성숙도는 `PARTIAL`이다.

#### 2026-09-08 실제 실행과 수정된 검증기의 재판독

정본은 최종 증거 색인 (`build/hosted-recovery/final-summary.json`)과
별도 독립 재검증 보고서 (`build/hosted-recovery/recovery-model-02-replay/report.json`)다.
재검증 보고서 SHA-256은 `82d45e967411fd61e08343a0d8951c6b6303c74c78a4b5407f8ecb0f34101ae0`이다.
기존 `recovery-model-02/agent-verdict.json`과 `vm-verdict.json`의 FAIL을 덮어쓰지 않았다.

| 검증 항목 | 실제 결과 |
|---|---|
| Linux 검사 | backend 관련 57개 PASS, 151.738초, skip 0; 현재 recovery 검사 10개와 producer→독립 verifier 결합 포함 |
| Windows 검사 | CLI·출력 계약 105개 PASS, 장애 증거 결합 9개 PASS; 각각 skip 0 |
| 실행 계약 | CLI 0.7/session 7, UID 1000, 23개 명령; 재검증 당시 제품 source 31개와 실행 snapshot/hash 일치 |
| 감독 프로세스 소실 | 인증한 supervisor에 검증 도구가 SIGKILL; 실제 종료 코드 -9와 회수, 같은 모델 자식의 생존 관측 |
| 소유 조건 | 새 CLI의 recover는 `recovery-owner-required`로 거부; 원래 CLI만 보유 pidfd로 TERM 수행 |
| 복구 결과 | 1.166703395초; 이전 backend는 별도 RECOVERED, 자식 종료 관측, 알 수 없는 child exit code는 null, 원본 run 9개 파일 보존 |
| 새 실행 | 같은 service ID의 backend generation 2와 새 instance; MAIN 2개, warmup 2회, binding generation 2로 명시적 재결속 |
| 실제 질문 | `The capital of France is Paris.`; 8 tokens, 266.477879242초; warmup은 179.543514675초와 228.084497211초 |
| 인터넷·종료 | DNS 주소 4개, HTTPS 200/559 bytes/인증서 검증; 새 MAIN·backend 정상 종료, VM exit 0, host 강제 종료 없음 |
| 보존·판정 분리 | 원본 모든 파일 hash·크기·수정 시각 불변; 일반 STOPPED 전용 판독은 같은 복구 기록을 `recovery_not_allowed`로 거부 |

최초 검증기는 장애 직후 `resources sample`의 오류를 `resource-relation-stale`로 잘못
예상했다. 실행 당시와 현재가 같은 `ResourceManager.begin()`은 `_check()`보다 `_proof()`를
먼저 수행하므로, 죽은 supervisor의 attestation 연결은 정확히 `backend-unavailable`로
실패한다. 수정된 검증기는 이 오류를 고정하고 `relation_current=false`, 새 observation
없음, 원래 관계 보존까지 요구한다. 잘못된 이전 오류나 새 관측·재연결 주장은 추가한
변조 거부 검사에서 거부된다. 제품 소스를 바꾸거나 원본 기록을 고쳐 PASS를 얻지 않았다.

```powershell
py -3 tools/hosted/verify_agent.py build/hosted-recovery/recovery-model-02 --recovery-smoke --source-root build/hosted-recovery/recovery-model-02/runtime-source
```

이 명령은 당시 보존 소스를 사용한다. 베타 게시 전 형식 정리 뒤의 checkout과 byte 단위
동일 판정은 아니며, 변경 범위는 [운영 이미지 가이드 §11](aios_operating_image_guide_ko.md)을 따른다.

실제 실행 도구는 `qemu_console.py --recovery-smoke`이며 `--guest-tests`와
`--guest-test-pattern 'test_hosted_backend*.py'`를 사용했다. 고정 ISO·kernel·initramfs·모델과
QEMU 인자는 원본 `environment.json` 및 출처 기록에 보존한다. 앞선 `recovery-model-01`은
AIOS 시작 전 Linux IO-APIC timer panic으로 실패했고 host가 VM을 종료했다. 이 실패도
보존하며, 같은 설정의 두 번째 실행이 부팅에 성공했다는 사실과 구분한다.

현재 v0.7/source 35개의 정상 운영 이미지 `model-image-05`는 online·offline 두 부팅
45명령, 실제 질문·재결속·인터넷·정상 종료와 현재 소스 독립 재생을 통과했다.
전용 0.7 launcher의 사용자 사본 실행도 별도 PASS이며 [운영 이미지 가이드 §9.7](aios_operating_image_guide_ko.md)이
정확한 증거를 소유한다. 이 정상 실행에는 supervisor 장애를 주입하지 않았다.
별도 `model-image-05-recovery-02`는 root의 복구 receipt·worker 소유자 결합과 recover
직후 exit하는 실제 6명령을 검증했다. recovered backend 1개·MAIN 0개, root cleanup·
정상 VM exit 0·host kill 없음이며 첫 장애 복사본 01의 원본 FAIL은 보존한다.
별도 보존 검증기로 재생한 판정도 02 원본과 정확히 같은 PASS다. 정상·실패·성공 원본의
파일 보존과 일반 verifier의 시험 이미지 거부 증거는 운영 이미지 가이드 §9.7을 따른다.
후속 기본 이미지 선택·이전 선택으로 돌아가기는 같은 가이드 §9.8의 별도 host 계약이다.
Windows 로컬의 0.7·0.6 실제 기본 부팅과 최종 0.7 재선택을 검증한 `SUPPORTING/PARTIAL`이며
backend protocol이나 제품 source 35개를 바꾸지 않는다.

`model-image-04`의 모델 포함 디스크, 두 cold boot·실제 질문·재결속·사용자 사본 PASS는
CLI v0.6/session 6과 당시 source 35개를 보존한 역사적 증거다. CLI v0.7/session 7로
소스가 바뀐 이후 그 디스크의 PASS를 새 recover 구현이나 새 source의 검증으로 승계하지
않는다. 원본 artifacts와 당시 재검증 기록을 변경하지 않는다.

기존 v0.6 정상 교체 시나리오의 실제 검증 진입점은
`tools/hosted/Start-AiosConsole.ps1 -BackendSmoke -GuestTests`다.
두 backend 수명과 두 MAIN 수명, 교체 후 요청 거부, 명시적 복구 뒤 실제 모델 응답,
DNS·인증서 검증 HTTPS, 프로세스 종료 및 VM 정상 poweroff를 함께 확인한다.
이 명령의 기존 PASS는 위 supervisor 소실·`backend recover` 조각을 검증하지 않는다.
Windows 검사는 Linux 프로세스·IPC 증거를 대신하지 않는다.

### 검증 기록

`build/hosted-backend/backend-01`은 Linux 408개 검사 중 failures 2 / errors 3으로 실패했다
(336.877초). 모델 실사용 검증에 진입하지 않았고 VM 종료도 `host_killed=true`이므로
수락 증거가 아니다. 원본 verdict와 serial을 보존한다.
세 오류는 테스트의 private control 디렉터리 준비 누락, 한 실패는 변조를 올바르게 거부한
verifier 오류 이름과 테스트 기대값 불일치였다. 나머지는 자식 종료 중 listener 소실을 먼저
관측하는 순서 차이로, 실패·준비 해제·실제 종료 확인 계약은 유지한 채 테스트 기대를 맞췄다.
동시 시작 시도 예약의 한도 경쟁 수정까지 포함하여 `backend-02`에서 다시 검증했다.

`build/hosted-backend/backend-02`는 고정 21개 CLI 명령을 실제 모델과 실행했고
`agent-verdict.json`, `vm-verdict.json`이 모두 PASS다. 같은 endpoint
`http://127.0.0.1:18081`의 두 backend를 아래 실행 출처로 구별했다.

| 항목 | 첫 실행 | 명시적 교체/복구 후 |
|---|---|---|
| backend service ID | `45f4a96d-a3b2-411a-a06e-0ccbed423e12` | 동일 |
| backend 시작 세대 | 1 | 2 |
| backend instance | `1704c405-9cc7-4d43-b355-746baff2533f` | `bd2f5928-3b5b-4609-9274-b0497483aa57` |
| Linux supervisor / child PID | 2493 / 2494 | 2518 / 2519 |
| backend descriptor source instance | `21c2ab63-ce7e-4f4f-995f-0c2cc55308b9` | `19ab88e8-3933-44c6-9ff7-2a2af890c52e` |
| MAIN source instance | `a8efb4c0-cb74-414b-9abc-ab35fbecbb89` | `75b23d47-39f2-45d7-a74b-eb6d48deb806` |
| MAIN Linux PID | 2516 | 2533 |
| MAIN 시작 세대 | 1 | 2 |
| backend STARTING→RUNNING | 57.901초 | 60.430초 |
| MAIN warmup | 221.908초 | 227.899초 |
| 정상 backend child 종료 | exit 0 / forced=false | exit 0 / forced=false |

MAIN source ID `bd5ebbdf-443b-4512-b9d6-a94682d02351`과 hosted authority
`2e5a2b2b-7db2-44e2-9c82-67d583bdfa97`는 유지됐다. Linux boot ID는
`e0141468-028a-453c-9a0c-3b8ee78e789e`, CLI session은
`8166e57e-419d-429b-b19e-07ca64b7976f`다. 위 PID·boot·instance는 Linux 실행 출처 증거다.
canonical Cell 1 / MAIN Node 101과 각 generation 1은 이 교체로 바뀌지 않았다.

기존 MAIN의 source generation은 backend 소실 발견 시 1→2, 준비 여부는 false가 됐다.
`BACKEND_INVALIDATED`는 한 번이며 첫 MAIN의 user receipt는 0개다.
`ask Say hello.`는 `model-not-ready`, 이전 자원 관계는 `resource-relation-stale`로 거부됐다.
이후 새 MAIN을 명시적으로 discover/reconcile/link하여 binding generation과
resource relation generation을 각각 1→2로 올렸다. 그 뒤에만 실제 user receipt가 생성됐다.

| 검사 | 확정 결과 |
|---|---|
| Linux 전체 hosted 검사 | 409개 / 303.578초 / skip 0 / PASS |
| Windows 전체 hosted/H1 검사 | 541개 / 85.772초 / PASS 490, skip 51 |
| Windows skip 범위 | Linux 프로세스·IPC 49개, symlink 권한 2개 |
| 실제 모델 | 고정 Qwen3-0.6B Q8_0 / warmup 2회, user 1회 |
| user request ID | `192f066c-8d0b-47e9-b96f-6ef41798c87d` |
| 실제 응답 | `The capital of France is Paris.` / 8 tokens / 264.457초 |
| 실제 HTTP 수신 증거 | worker PID 2535 → backend PID 2519; client inode 32537 / server inode 23907 |
| resource relation ID | `e3c0033e-99a7-4f5a-abf9-ff94a06bebf7` |
| observation ID | `06f19d15-e67c-4bd3-b8dc-1f2a38fddc58` |
| MAIN CPU / 관측 구간 / RSS 추정 | 80,000,000 ns / 264,553,273,269 ns / 21,848,064 bytes |
| backend CPU / 관측 구간 / RSS 추정 | 366,390,000,000 ns / 264,554,077,797 ns / 828,170,240 bytes |
| system PSI | CPU/memory/io available; CPU some avg10 35.73%, system CPU full은 undefined |
| 실제 DNS / HTTPS | example.com 주소 4개 / HTTPS 200, 559 bytes, certificate verified |
| 종료 | MAIN 두 실행·backend 두 실행 STOPPED; VM exit 0 / host_killed=false |
| 최종 serial | `[ 1289.097271] reboot: Power down` |

이 CPU 소프트웨어 에뮬레이션 VM의 모델 지연은 제품 성능 보장이 아니다. RSS는 Linux의
순차 읽기 추정치이며 system PSI는 특정 Node에 귀속하지 않는다. 이번 모드의
`backend_workflow_verified=true`는 backend 조각에만 적용된다.
`resource_workflow_verified=false`, `cell_workflow_verified=false`이므로
별도 ResourceSmoke/CellSmoke 전체를 재실행한 것으로 표시하지 않는다.

`current-source-replay.json`은 재검증 당시 source 31개가 보존 snapshot과 같음을 확인하고
독립 checker 10개 hash, 전체 시나리오 재판독, 다음 3개 변조 거부를 기록한다.
backend-start listener descriptor를 바꾼 뒤 stdout hash를 재계산한 경우,
첫 MAIN의 실행 binding을 새 backend의 정상 binding으로 바꾸고 파일 hash를 재계산한 경우,
STOPPED를 유지한 채 실제 child 종료 확인을 제거한 경우 모두 거부됐다.
Windows 최종 로그 `build/hosted-backend/windows-02.log` SHA-256은
`387fedb2a0f0208a9c61272ff3e8fff4030d59c3f3f923517688312bfff369f6`이다.

원본 `agent-verdict.json` SHA-256은
`07c145fe322308c088d165f580cead9170d16d46ca560854c68c0be041c8e106`,
`vm-verdict.json`은 `4c33d84139fe7cf4d072d378b9ccc03a4b2ab6ffb58564c3833cb665170e1a4f`다.
재판독과 변조 반례는 원본 verdict·source snapshot을 바꾸지 않았다.

### 모델 운영 이미지에 연결하면서 바뀐 경로 검사

별도 [운영 이미지 가이드](aios_operating_image_guide_ko.md) §9는 설치된 모델 profile의
반복 부팅을 다룬다(`SUPPORTING/PARTIAL`). `model-image-02`는 설치·Linux 이미지 검사
46개를 통과했지만 첫 운영 부팅에서 `backend-listener`로 실패했다. `BackendAttester`가
별도 socket 디렉터리를 사용하면서도 긴 run 기록 디렉터리에 socket 길이 제한을 적용한
것이 원인이다. 기록 디렉터리 뒤의 가상 control socket은 115 bytes, 실제 backend socket은
73 bytes였으며 제한은 104 bytes 미만이다.

`hosted/linux/aios_resources/backend.py`는 output 경로가 socket 경로로도 사용되는 경우에만
그 output에 socket 길이 검사를 적용하도록 수정했다. 별도 socket 경로의 길이 제한과
기록 디렉터리의 private 소유권·symlink 거부 검사는 유지한다. 파일 개수와 CLI/MAIN schema는
같지만 runtime source bytes가 바뀌었으므로 `backend-02`를 현재 수정본의 실제 검증으로
일반화하지 않는다. 수정 후 `model-image-04`의 Linux 이미지 검사 51개와 같은 디스크의
online·offline 두 cold boot, 실제 warmup·질문 각 2회, 명시적 재결속·인터넷·정상 종료 및
당시 source 35개의 독립 재검증을 통과했다. 별도 `local-model` 사용자 사본의 실행·종료도
확인했다. 이 결과는 운영 이미지 가이드 §9.6의 `SUPPORTING/PARTIAL` 증거이며
`backend-02`의 backend 교체 시나리오 전체를 현재 소스로 재실행한 것은 아니다.
이 `model-image-04` 역시 CLI v0.6/session 6의 보존된 소스 증거다. 후속 v0.7 recover
계약·source 변경의 실제 acceptance와 분리한다.

## 공식 자료 검토와 한계

2026-09-08에는 [Python `os.pidfd_open` 공식 문서](https://docs.python.org/3/library/os.html#os.pidfd_open)와
[`signal.pidfd_send_signal` 공식 문서](https://docs.python.org/3/library/signal.html#signal.pidfd_send_signal)를
다시 확인했다. Linux 프로세스를 가리키는 열린 핸들과 그 핸들을 통한 신호 전달 계약을
사용한다. AIOS의 추가 제한인 동일 CLI의 사전 RUNNING 인증·소유 관계·단회 TERM·별도
복구 receipt는 이 프로젝트의 구현 및 검증 계약이다. 외부 구현 코드를 가져오지 않았다.

2026-09-07에 [Linux TCP proc 인터페이스](https://docs.kernel.org/networking/proc_net_tcp.html)와
[Python HTTPConnection 공식 문서](https://docs.python.org/3/library/http.client.html)를 검토했다.
Linux 문서는 `/proc/net/tcp`를 deprecated로 표시하고 `tcp_diag` 사용을 권장한다.
이번 조각은 이미 사용하는 읽기 전용 proc 증거를 제한적으로 확장한다. 장기 backend에는
진단 socket 기반 교체를 검토하되 wire evidence schema와 독립 verifier를 함께 바꿔야 한다.
HTTP 연결을 먼저 만들 수 있다는 API 계약을 사용하며 upstream 코드는 복사하지 않는다.

이 증거는 동일 사용자·Linux guest·고정 모델 profile의 로컬 실행 연속성에 한정된다.
프로세스 권한 격리, cgroup 소유권, quota/allocator/enforcement, CLI 소실·startup·재부팅 뒤 복구,
실기기 범용 호환성은 아직 완료하지 않았다. 기본·모델 포함 설치 이미지의 bounded 실제
acceptance는 운영 이미지 가이드의 별도 `SUPPORTING/PARTIAL` 증거다.
독자 네이티브 커널이 Linux driver ABI를 직접 실행한다는 증거로 해석하지 않는다.
