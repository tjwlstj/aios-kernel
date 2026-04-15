# AIOS Kernel Room / Orbit / Cell / Node 구현 가능성 검토

작성일: 2026-04-12

## 목적

이 문서는 지금까지 논의한 아래 개념을 현재 AIOS 저장소 기준으로 다시 정리한다.

- Kernel Room
- Axis Gate
- Orbit
- Cell
- Node

이번 정리는 두 기준을 함께 사용했다.

1. 현재 저장소 구현 상태
2. Gemini CLI 보조 검토 결과

단, Gemini 의견은 그대로 채택하지 않고 현재 코드에서 확인되는 사실만 남겼다.

## 한 줄 결론

`Kernel Room / Gate / Orbit / Cell / Node`는 AIOS에 맞는 운영 모델로는 유효하다.
하지만 2026-04-12 현재 저장소에서 바로 코드로 옮길 수 있는 범위는
`Kernel Room registry`, `Gate metadata`, `Cell metadata`, `Node-to-Cell binding` 정도까지다.

즉, 지금은 "입체적인 운영 메타모델"로 쓰기 좋고,
실제 구현은 단일 머신 안에서의 상태/격리/정책 단위부터 시작하는 편이 맞다.

## 개념 정의

### Kernel Room

커널의 중심 핵이다.
시간원, 인터럽트, 메모리 맵, health, core snapshot처럼
시스템 전체가 공유하는 최소 기반 상태를 가진다.

### Axis Gate

아무 경로로나 커널을 건드리지 않고,
기능별 게이트를 통해서만 커널 기능에 접근하는 모델이다.

예:

- memory gate
- device gate
- scheduler gate
- policy gate
- telemetry gate

### Orbit

권한, 지연 민감도, 위험도, 응답성을 함께 나타내는 거리 개념이다.
안쪽일수록 더 빠르고 더 강한 권한을 가지며,
바깥일수록 더 느리지만 더 안전하고 격리된다.

### Cell

실제 자원 분배와 격리의 최소 관리 단위다.
단순 태스크보다 넓고, 전체 OS 서비스보다 작다.

Cell은 최소한 아래를 묶는 단위로 보는 편이 좋다.

- 메모리 hotset / budget
- queue / ring
- risk class
- health 상태
- policy 적용 범위

### Node

실제로 동작하는 실행 주체다.
AI agent, planner, broker, model worker, device service 같은 존재가 여기에 해당한다.
Node는 Cell 안에서 움직이고, Cell은 Orbit 위에 배치되는 식으로 보는 편이 자연스럽다.

## 현재 코드 기준 대응표

| 개념 | 현재 코드의 대응물 | 판정 |
|---|---|---|
| Kernel Room | `kernel/main.c`, `kernel/health.c`, 일부 `runtime/slm_orchestrator.c` | 부분 구현 |
| Axis Gate | `runtime/ai_syscall.c`, health gate, memory/domain 정보 노출부 | 부분 구현 |
| Orbit | `sched/ai_sched.c`의 우선순위/큐 개념, `kernel/health.c`의 stability 단계 | 개념 일부만 존재 |
| Cell | `mm/memory_fabric.c`의 domain/window, infer ring 등록 정보 | 직접 구현 없음, 기반만 존재 |
| Node | `runtime/slm_orchestrator.c`의 plan / agent tree / action 단위 | 부분 구현 |

## 현재 저장소에서 실제로 확인되는 것

### 1. Kernel Room에 가까운 기반은 이미 있다

- `kernel/main.c`는 부팅, 시간원, ACPI, PCI, 메모리, 스케줄러, health, SLM 초기화까지
  하나의 순차 흐름으로 묶는다.
- `kernel/health.c`는 각 서브시스템을 required/optional, io path, stability로 정리한다.
- 아직 "Kernel Room"이라는 이름의 중앙 레지스트리는 없지만,
  중심 상태를 모으는 핵심 기반은 이미 있다.

### 2. Gate에 가까운 경계도 일부 있다

- `runtime/ai_syscall.c`는 syscall 범위별 진입점을 구분한다.
- health는 `autonomy_allowed`, `risky_io_allowed` 같은 bounded gate 역할을 한다.
- 드라이버와 메모리 쪽도 snapshot/summary를 읽어 정책을 만드는 틀이 있다.

하지만 아직 Gate가
"권한 + 검증 + 상태 + completion"이 묶인 공통 구조로 정식 정의되지는 않았다.

### 3. Cell의 가장 가까운 기반은 memory fabric이다

- `mm/memory_fabric.c`는 domain, shared window, local budget, inflight ops 같은 값을 가진다.
- 기본 domain도 `main`, `memory`, `device`, `worker`로 seed된다.

이건 Cell을 만들기 좋은 기반이다.
다만 아직은 메모리와 window 중심일 뿐,
실행 단위의 생명주기나 권한 경계까지 포괄하는 Cell 런타임은 아니다.

### 4. Node의 가장 가까운 기반은 SLM orchestration이다

- `runtime/slm_orchestrator.c`는 snapshot, plan table, action confidence, agent tree를 가진다.
- 장치 상태와 health를 읽고, SLM action confidence를 갱신하며, seeded plan도 만든다.

이건 Node를 배치할 운영 평면으로는 좋다.
하지만 아직 실제 ring3 userspace node나 독립 주소공간 노드를 운영하는 수준은 아니다.

### 5. Orbit는 아직 문서 모델에 더 가깝다

- `sched/ai_sched.c`의 큐와 우선순위는 있다.
- `kernel/health.c`의 stable / degraded / unsafe도 있다.

하지만 "권한 거리 + 위험도 + 지연 민감도 + 배치 위치"를 하나로 묶는
명시적 Orbit 모델은 아직 코드에 없다.

## Gemini CLI 논의에서 채택한 것

Gemini 검토에서 현재 저장소와 맞는 부분만 남기면 아래 정도다.

- `Kernel Room`은 중앙 커널 상태를 정리하는 개념으로 유효하다.
- `Axis Gate`는 syscall/health/policy 경계를 묶는 이름으로 유효하다.
- `Cell`은 메모리 fabric domain과 실행 메타데이터를 결합한 단위로 시작하기 좋다.
- `Node`는 먼저 분산 노드가 아니라 단일 머신 안의 logical execution unit으로 보는 편이 맞다.
- 전체 구조는 "분산 시스템"보다 "단일 머신의 격리/정책 구조"부터 시작해야 한다.

## Gemini CLI 논의에서 그대로 채택하지 않은 것

Gemini 답변 중에는 현재 저장소 기준으로 과한 매핑도 있었다.
다음 항목은 그대로 구현 계획으로 받아들이지 않았다.

### 1. `task_struct` 전제

Gemini는 `task_struct`에 `CellID`, `RoomID`를 추가하는 식의 예를 들었지만,
현재 AIOS 저장소에는 그런 범용 프로세스 구조체가 없다.
따라서 그 제안은 "추상 예시"로만 보고 채택하지 않았다.

### 2. `kernel/user_mode.c`를 이미 완성된 Cell 격리 기반으로 보는 해석

현재 저장소의 user mode 쪽은 ring3 scaffold에 가깝다.
실제 userspace handoff, 주소공간 분리, 프로세스 런타임이 완성된 상태는 아니다.

### 3. `Orbit == scheduler` 단순 치환

스케줄러는 Orbit의 일부 재료가 될 수는 있지만,
Orbit 전체를 설명하지는 못한다.
Orbit는 스케줄뿐 아니라 권한, 위험도, 자원 거리까지 포함해야 하기 때문이다.

## 구현 가능성 판정

### 지금 바로 구현 가능한 것

아래는 현재 C + ASM 기반 저장소에 무리 없이 추가할 수 있는 범위다.

1. `Kernel Room registry`
   - 핵심 subsystem snapshot
   - health summary
   - fabric profile
   - driver stack summary
   - active plan summary

2. `Axis Gate descriptor`
   - gate id
   - required capability
   - risk class
   - health precondition
   - completion / wait policy

3. `Cell metadata`
   - cell id
   - bound memory domain
   - queue/ring binding
   - local budget
   - health / risk state

4. `Node-to-Cell binding`
   - SLM plan 또는 agent tree node가 어떤 Cell에 묶여 있는지 표시
   - bootstrap stage에서는 logical binding만 먼저 도입

### 지금은 문서로만 두는 편이 맞는 것

1. 전역 `Orbit privilege model`
   - 커널 전체 권한 질서를 새로운 추상 계층으로 다시 쓰는 작업

2. 분산형 `Node mesh`
   - 단일 머신 경계를 넘는 cell/node fabric

3. 자기 수정형 커널 서사
   - 현재 구현은 bounded policy apply 수준이지,
     커널이 스스로 구조를 재작성하는 단계가 아니다

## 현재 프로젝트에 맞는 최소 패치 순서

### 1. Kernel Room snapshot header 추가

추천 방향:

- `include/kernel/kernel_room.h`
- `kernel/kernel_room.c`

첫 버전은 "실제 제어면"보다 "읽기 전용 중앙 상태 스냅샷"으로 시작하는 편이 안전하다.

### 2. Axis Gate 메타데이터 추가

추천 방향:

- `include/runtime/ai_syscall.h` 또는 새 `include/runtime/axis_gate.h`
- 기존 syscall 범위를 gate id와 risk class에 매핑

이 단계에서는 실제 동작을 크게 바꾸지 않고,
"이 요청이 어떤 문을 통과하는가"만 먼저 가시화하면 된다.

### 3. Memory Fabric domain을 Cell metadata와 연결

추천 방향:

- `mm/memory_fabric.c`
- `runtime/slm_orchestrator.c`

실행 단위를 바로 완성하려 하지 말고,
먼저 `domain -> cell`의 관리 메타데이터를 붙이는 게 좋다.

### 4. SLM plan / agent node에 Cell 바인딩 추가

이 단계부터 `Node`가 추상 명칭이 아니라
현재 런타임 구조에 걸리는 단위가 된다.

### 5. 이후 ring3 handoff가 붙은 뒤 userspace supervisor로 확장

이 순서를 지키면,
지금 없는 userspace/proc/runtime를 억지로 먼저 만들지 않고도
현재 저장소 위에서 구조를 키울 수 있다.

## 검증 경로

문서 모델을 코드로 옮길 때는 아래 순서가 가장 안전하다.

1. selftest / boot summary에 새 snapshot 항목 추가
2. `SYS_INFO_*` 계열에서 room / gate / cell read-only dump 제공
3. `testkit`에서 full/minimal/storage-only 부팅 시 summary 비교
4. ring3 handoff가 준비된 뒤에만 userspace caller 검증 추가

현재 단계에서 가장 좋은 검증 기준은
"실제 동작을 과장하지 않는 read-only snapshot + boot log observable"이다.

## 구현 가능 여부 최종 정리

최종 판정은 이렇다.

- **가능한 것**
  - 단일 머신 기반의 `Kernel Room / Gate / Cell / Node` 메타구조
  - memory fabric / health / syscall / SLM plan을 묶는 중앙 상태 모델
  - 나중의 userspace supervisor를 위한 경계 정리

- **아직 이른 것**
  - 완전한 Orbit privilege runtime
  - 독립 userspace process mesh
  - 분산형 node fabric
  - 자기 수정형 커널 서사

즉, 이 모델은 AIOS에서 충분히 쓸 만하다.
다만 지금은 "운영 철학 전체"를 한 번에 구현하려 하지 말고,
현재 저장소에 이미 있는 `health`, `memory_fabric`, `ai_syscall`, `slm_orchestrator`
위에 작은 registry와 metadata를 얹는 순서가 맞다.
