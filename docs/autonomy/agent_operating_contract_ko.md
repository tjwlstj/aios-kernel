# AI 에이전트 운용 계약

> 문서 역할: 에이전트가 소비하는 관측·행동·결과·상호작용 계약 가이드
> 문서 수명주기: 활성
> 내용 검토일: 2026-09-13 — v0.10 Task의 소유자·취소·결과 불명 경계 대조; 실제 모델 실행 판정 아님
> 상위 정본: [제품 목적](../meta/aios_product_direction_ko.md), [Kernel Room 관리 모델](../kernel-room/kernel_room_management_model_ko.md)
> 현재 구현 기준: 이 체크포인트의 개발 소스 v0.10과 각 운영 가이드의 source별 실행 증거. v0.8/v0.9의 보존 실행은 별도이며 문서 검토는 runtime 재실행 판정이 아니다.
> 후속 개발: 제한된 환경 문맥·v0.10 Task는 `PARTIAL`이며 보존된 실제 소비/fixture·한정 실제 모델 Task PASS는 [환경 문맥 가이드](../os/aios_space_context_guide_ko.md)에서 구분한다.

## 1. 목적

AIOS에서 에이전트는 자신의 실행 환경, 작업 대상, 가능한 행동, 진행 상태와 결과를
확인하고 사용자와 함께 활동할 수 있어야 한다. 로컬 자원 효율, 상태를 알아내는 부담,
사용자 개입과 복구 가능성을 함께 평가한다.

이 문서는 그 목적을 관측·행동·결과의 소비 규칙으로 연결한다. 전역 작업 큐는
[성숙도 작업흐름](../meta/minimal_io_and_maturity_workflow_ko.md), Linux 실행 책임은
[실행 기반 정책](../os/linux_hosted_substrate_and_resource_policy_ko.md), 판정 의미는
[검증 정본](../tools/verification_tooling_evolution_design_ko.md)이 소유한다.

## 2. 에이전트가 사용하는 환경과 상호작용

아래는 후속 구현의 요구사항이다. 특정 JSON 필드·syscall·완료된 runtime으로 해석하지 않는다.

| 능력 | 소비 계약 | 사용자에게 보이는 결과 |
|---|---|---|
| 환경 발견 | 연결된 관리/실행 대상, 도구와 지원 범위, 미관측 대상을 구분 | 무엇을 사용할 수 있고 무엇을 아직 확인하지 못했는지 설명 |
| 상태 이해 | 관측 출처·시점·세대·유효성을 보존 | 오래된 결과를 현재 상태로 표시하지 않음 |
| 작업 실행 | 확인된 대상과 지원 동작, 입력 조건, 요청 추적 관계를 사용 | 요청 접수·실행 중·성공·실패·결과 불명을 구분 |
| 작업 중 대화 | 질문·대상 수정·취소를 기존 작업에 명시적으로 연결 | 진행 상황을 확인하고 개입할 수 있음 |
| 결과 확인 | 모델 설명과 실제 도구/서비스 결과를 구분 | 결과물과 확인 가능한 근거를 함께 보여 줌 |
| 중단·복구 | 재시도 전 실제 실행 여부와 대상 유효성을 다시 확인 | 완료된 작업의 중복 실행을 피하고 불확실한 결과를 알림 |

사람용 CLI/화면과 에이전트용 구조화된 표면은 같은 상태·결과를 소비한다. 사용자와
작업의 연결은 session/history만으로 증명되지 않으며, 관리 계층과 작업 기록도 별도다.
이미 허용된 동작은 그 범위에서 진행하고 추가 판단이 필요한 경우 구체적인 상태와
선택의 효과를 제시한다. 편의를 위해 오래된 binding을 자동 신뢰하거나 resource action을
지원됨으로 바꾸지 않는다.

모델 실행기를 사용할 수 없거나 결속 검사가 요청을 거부하면 운용 계층이 구조화된
실패 증거로 상태와 복구 방법을 설명한다. 이 설명은 모델 생성 응답과 구분하며, 오류를
설명하기 위해 무효한 대상에 추가 모델 요청을 보내지 않는다.

## 3. 현재 구현 상태

### 3.1 native proof와 host-only 계약

| 단계 | 상태 | 현재 표면 |
|---|---|---|
| 발견 | `CURRENT` | `state list` |
| 커널 관측 | `CURRENT` | `state health/room/binding/mem/sched/nodes/pipeline/resource/pressure/slm/autonomy/user/sec/time/version` |
| Kernel Room K1 계층 | `CURRENT` | schema 1/1024B management-only snapshot + exact boot/summary + `state room` |
| Kernel Room native K2-a 결속 | `CURRENT` | schema 1/256B management-only snapshot + producer-owned SLM source + exact boot/summary + `state binding` |
| H1 OS-neutral binding replay | `CURRENT` | H1-a/b/c contract·12 fixtures·artifact/parity가 동일 run·exact SHA의 원격 Linux/Windows/parity 및 세 artifact 검증을 통과; live producer는 없음 |
| 커널 내부 리소스 ledger | `CURRENT` | schema 1 aggregate snapshot + exact boot summary + `SYS_INFO_RESOURCE=0x706` + `state resource` |
| 자율 제어 관측 | `CURRENT` | `state autonomy` schema 1 |
| 제한된 행동 제안 | `PARTIAL` | `SYS_AUTONOMY_ACTION_PROPOSE`; scheduler만 apply 지원, delta ±32 |
| commit/rollback | `PARTIAL` | `SYS_AUTONOMY_ACTION_COMMIT`, `SYS_AUTONOMY_ROLLBACK`; native 상주 userspace agent는 아직 없음 |
| principal authorize | `PLANNED` | K1~K4 관리 identity/binding/attribution 증거 뒤 K5 |
| 재부팅 후 연속성 | `PLANNED` | C1 정책·관리 저널, C2 AI Flow |

native proof 경로의 반복 가능한 에이전트 인터페이스는 QEMU COM1에 연결된 host-driven shell이다.
ring3는 두 정적 bootstrap process를 PID 1→PID 2 순서로 각각 동기 실행하는 단계이며,
상주 AI runtime이나 일반 프로세스 모델로 과장하지 않는다.

### 3.2 Linux-hosted 제품 경로

| 현재 표면 | 상태·한계 | 계약과 증거 |
|---|---|---|
| `help`, `hardware`, 네트워크 상태와 DNS·HTTP(S) | `PARTIAL`; source 관측과 사용자 요청 I/O | [CLI 가이드](../os/aios_cli_internet_guide_ko.md) |
| `agent`, `room`, `ask` | `PARTIAL`; 실제 모델과 bounded MAIN 결속, 전체 agent 도구 실행 루프는 아님 | [MAIN 가이드](../os/aios_agent_binding_guide_ko.md) |
| `cell`, `resources` | `PARTIAL`; Cell 1 관리 전이, 개별 CPU/RSS 관측. ownership/apply 미완료 | [Cell](../os/aios_cell_lifecycle_guide_ko.md), [자원](../os/aios_resource_observation_guide_ko.md) |
| `backend` 수명·실행 대상 검증·recover | `PARTIAL`; 동일 CLI가 보유한 핸들 범위, CLI 소실·재부팅 복구 제외 | [backend](../os/aios_backend_lifecycle_guide_ko.md) |
| 설정·CLI history 보존 | `SUPPORTING/PARTIAL`; 작업 연속성과 별개 | [운영 이미지](../os/aios_operating_image_guide_ko.md) |
| MAIN 환경 조회와 실제 에이전트의 문맥 소비 | `PARTIAL`; v0.8 실제 소비의 수정 검증기 재생 PASS, 원본 FAIL 보존·새 버전 실행 별도 | [환경 문맥](../os/aios_space_context_guide_ko.md) |
| UUID 질문 접수·조회·결과·취소 | v0.10 개발 `PARTIAL`; 같은 CLI 소유 backend 정지와 독립 종료 관측, 한정 실제 모델 TaskSmoke PASS; 범용 수정/연속 대화는 미완료 | [다음 조각](../meta/minimal_io_and_maturity_workflow_ko.md#agent-consumer-next) |

v0.7은 고정 system 문장과 이번 prompt만 전송했다. v0.8 개발 경로는 제한된 MAIN 환경을
질문과 함께 전달하며 `space`의 관측 갱신과 `ask`의 요청을 구분한다. 현재·오래된·미관측
사실을 모델 입력과 사용자 출력에서 일치시키고, backend가 입력을 잘랐는지도 검사한다.
실제 v0.8 소비의 수정 검증기 재생과 v0.10 Task fixture를 구분한다. 이전 대화·선택 workspace·
범용 작업 수정의 자동 연결은 후속이다. 실제 모델 Task는 같은 CLI의 정상 답변·별도 진행 중 Task 취소·독립 종료 범위에서 PASS다. 원본 FAIL·재생 범위와 정확한 버전·입력·응답 상한은 환경 문맥 가이드가 소유한다.

hosted의 실제 MAIN producer가 존재해도 H1 fixture가 live capture로 바뀌지는 않는다.
반대로 native 상주 runtime의 부재가 hosted 모델 실행의 부재를 뜻하지 않는다.

## 4. `state autonomy` schema 1

응답은 정확히 한 줄이며 값 안에 공백을 넣지 않는다.

```text
[STATE] autonomy schema=1 observation_only=1 safe_mode=0 support_mem=observe-only support_sched=apply support_accel=observe-only support_infer=observe-only ...
```

필드 그룹:

- `schema`: 이 shell record의 버전. 현재 `1`.
- `observation_only`, `safe_mode`: 현재 자율 제어 모드.
- `support_mem/sched/accel/infer`: target별 선언된 지원도
  (`none`, `observe-only`, `apply`).
- `telemetry`, `proposed`, `approved`, `committed`, `rejected`, `rollbacks`:
  누적 통계.
- `queue_depth`, `event_depth`: 현재 action queue와 event log 깊이.
- `last_valid`, `last_action`, `last_target`, `last_state`, `last_reason`:
  마지막 결정 또는 거절. 이벤트가 없으면 `last_valid=0`, 나머지 이름 필드는 `none`이다.

`support_sched=apply`는 현재 모드와 health를 무시하고 즉시 적용 가능하다는 뜻이 아니다.
지원도, `observation_only/safe_mode`, `state health`를 함께 읽어야 한다. 기본값
`observation_only=1`에서는 apply-capable target도 제안 단계에서 차단된다.

## 5. 안전 경계

- `state autonomy` 계약 자체는 read-only 관측면이며 action, syscall 번호, enum 값,
  구조체 ABI를 늘리지 않는다. 별도 AI Resource Ledger는 커널 내부 versioned snapshot과
  read-only `SYS_INFO_RESOURCE=0x706` userspace ABI를 제공한다. owner attribution과
  reserve/quota/apply ABI는 아직 없다.
- memory/accel/infer target은 계속 `observe-only`다.
- scheduler action만 구현돼 있으며 delta는 `-32..32`로 제한된다.
- raw pointer, register, MMIO 주소를 모델 출력으로 받지 않는다.
- 현재 `SYS_AUTONOMY_MODE_SET`에는 K5 principal/ownership 기반 authorize가 아직 없다.
  따라서 이 단계에서 shell action 명령이나 편의용 apply 우회를 추가하지 않는다.
- Kernel Room gate는 현재 분류 메타데이터이며 per-call authorize로 과장하지 않는다.
- native K2-a binding은 read-only boot-local oracle이며 source refresh/reconcile이나
  행동 authorize/apply 입력으로 사용하지 않는다.

## 6. 확장과 선행조건

다음 작업은 [전역 큐](../meta/minimal_io_and_maturity_workflow_ko.md#agent-consumer-next)에서
선택한다. 이 문서는 별도 구현 순서표를 유지하지 않는다. 첫 제품 소비 흐름과 후속
작업 중 상호작용·연속성의 acceptance는 제품 목적과 전역 작업흐름을 따른다.

관측·기존 허용 요청·결과 설명의 사용자 경험은 현재 공개 표면으로 시작할 수 있다.
새 resource apply나 광범위한 관리 권한을 추가할 때는 K축의 identity·binding·attribution,
principal·ownership 계약을 갖추고 `observe → propose → authorize → apply → verify →
commit/rollback`의 해당 경계를 증명한다. 모든 사용자 대화를 kernel action proposal로
바꾸거나 K5 구현을 기다려야만 대화 인터페이스를 시작할 수 있다고 해석하지 않는다.

### 6.1 UUID Task의 조회·취소 계약 (개발 `PARTIAL`)

v0.10의 `ask`는 질문 UUID와 접수 기록을 먼저 저장하고 비동기 worker를 시작한 뒤
CLI 프롬프트로 돌아온다. `task status|result|cancel <UUID>`는 같은 접수 질문을 가리킨다.
MAIN 6에 직접 보내는 과거 동기 `ask` IPC는 `request-task-required`로 거부한다.
같은 CLI의 실제 모델 Task 한정 흐름은 PASS이며, source 39개 운영 이미지 acceptance는 아직 없다.
버전·source·fixture·이전 실제 문맥 소비의 범위는 [환경 문맥 가이드](../os/aios_space_context_guide_ko.md)가 소유한다.

- 요청 ID, 실제 peer UID/PID 수명, MAIN instance, 관리·binding 세대, 관측 packet과
  backend descriptor를 연결한다. MAIN의 한 제어 루프만 상태·source·authority·결과를 변경한다.
- 한 번에 활성 질문 하나만 허용한다. 접수·event·worker 예산은 bounded이며, 조회가
  새 revision·완료 수·결속을 만들지 않는다. 완료·취소 경합에서도 완료 처리와 자원 정리는 한 번이다.
- 취소 접수를 저장한 뒤 worker 정지를 요청한다. 취소 접수, worker 종료, MAIN이 별도
  결속으로 관측한 원래 backend의 실제 종료는 서로 다른 증거다. 단일 요청만의 모델 취소를
  구현했다고 표현하지 않는다. IPC 단절이나 worker 종료만으로 성공·미실행을 확정하지 않는다.
- backend 정지는 시작한 동일 CLI의 retained lease/child/process handle에 한정한다.
  다른 세대나 다른 소유자의 backend를 종료하지 않으며 자동 재시도·재시작·재결속을 하지 않는다.
- 확인된 답변을 취소 경합으로 지우지 않는다. 결과가 미확정이면 `UNKNOWN`을 보존하고,
  worker가 끝난 뒤의 늦은 `UNKNOWN` 취소도 같은 원래 backend의 한 번의 명시적 정지에 한정한다.
  저장된 Task 기록은 CLI 소실·재부팅 뒤 자동 실행 권한이나 작업 연속성의 증거가 아니다.
- Task RPC 한 번의 왕복은 전체 5초 제한이다. 취소 전체에는 backend 정지·독립 재조회
  시간이 추가될 수 있다. `RUNNING`은 제어·worker 상태이며 모델 HTTP 접수·토큰 계산의 직접 증거가 아니다.
- 입력 거부·재관측 필요·대상 관계 복구·결과 불명을 구분해 안내한다. 오류 이름만으로
  결과를 확정하지 않으며 새 질문을 자동 재전송하지 않는다.

아래 고정 서버의 정적 검토는 보존된 별도 근거다. 연결 종료나 slot 관측은 현재 Task
취소의 독립 backend 종료 증거를 대체하지 않는다.


2026-09-09 정적 검토에서 현재 llamafile 0.10.5 pin의 non-stream `/completion`은 연결
종료를 확인한 뒤 내부 CANCEL을 enqueue하고, server loop가 해당 작업을 처리할 때 slot을
해제하는 경로를 확인했다. 포함 llama.cpp 기준은 `c588c4f47683e73ad2d69f50480bec6cc85fd0f7`이며
[고정 non-stream handler](https://github.com/ggml-org/llama.cpp/blob/c588c4f47683e73ad2d69f50480bec6cc85fd0f7/tools/server/server-context.cpp#L4134),
[대기·취소 queue](https://github.com/ggml-org/llama.cpp/blob/c588c4f47683e73ad2d69f50480bec6cc85fd0f7/tools/server/server-queue.cpp#L389)와
[고정 llamafile patch](https://github.com/mozilla-ai/llamafile/tree/486e6c5f9356eae50b851b07517bfae1f2420193/llama.cpp.patches)를 대조했다.
이는 실제 AIOS 취소 실행의 검증이 아니다. 1초 polling은 진행 중 decode까지 포함한
중단 상한이 아니며, 현재 Task는 단일 모델 요청의 slot 취소 완료 receipt를 제공하지 않는다.

현재 pin의 `GET /slots`는 조회 가능하지만 같은 backend·task의 전후 연결이 필요하다.
idle만으로 자연 완료와 취소를 구분하지 않는다. cache save/restore/erase나 resumable stream
삭제의 204도 현재 non-stream 요청의 취소 완료 확인으로 재해석하지 않는다.
[고정 서버 API 문서](https://github.com/ggml-org/llama.cpp/blob/c588c4f47683e73ad2d69f50480bec6cc85fd0f7/tools/server/README.md)의
범위와 실제 관측을 함께 검증하고, pin·요청 방식·slot 설정이 바뀌면 이 근거를 재검토한다.

## 7. 검증 계약

- host unit test는 `state autonomy`의 schema, 기본 mode, support matrix, last-event 없음과
  중복 key 반례를 검사한다.
- strict shell lane은 실제 QEMU에서 이 토픽을 질의하고 같은 response record에서 판정한다.
- native 커널·부트 계약 변경은 영향 profile, strict shell, 정적 분석 등 해당 정규 gate를
  따른다. hosted 변경은 해당 제품 workflow/consumer 검증을, 문서 변경은 링크·정본·
  신선도 검사를 선택한다. 정확한 범위는 통합 작업 진입 가이드 §7을 따른다.
- Windows와 Python 정상 verdict는 같은 IDE evidence grammar를 사용해야 한다.

- 후속 에이전트 소비 검증은 명령을 host가 대신 수행한 기록과 구분한다. 실제 에이전트가
  무엇을 관측하고 어느 대상으로 요청했으며 결과/거부 이유를 어떻게 해석했는지 확인한다.
- 작업 중 수정·취소는 요청 수신과 실제 반영/중단 결과를 각각 확인한다. 이벤트 순서,
  대상 교체, 이미 완료된 작업, 결과 불명과 중복 요청을 해당 구현 범위의 반례로 삼는다.
- 정보 신선도와 문서 갱신은 [문서 신선도 원장](../meta/document_freshness_registry_ko.md)을 따른다.
