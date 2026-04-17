# AIOS Kernel Room Topology 정리

작성일: 2026-04-18

## 목적

이 문서는 AIOS에서 논의 중인
`Kernel Room Topology`라는 상위 개념의 이름과 구조를 정리한다.

목표는 두 가지다.

1. 앞으로 관련 문서를 같은 이름 아래에서 관리할 수 있게 한다
2. 구현된 것, 부분 구현, 계획 상태를 구분한 채 공통 언어를 만든다

## 한 줄 정의

`Kernel Room Topology`는
커널을 중심 핵과 접근 축, 권한 거리, 격리 단위, 실행 주체가 함께 움직이는
입체적 운영 구조로 보는 이름이다.

## 왜 이 이름을 쓰는가

`Kernel Room`만 쓰면 중심 핵의 느낌은 좋지만,
실제 운영 단위인 거리, 셀, 노드가 충분히 드러나지 않을 수 있다.

`Orbit`만 쓰면 권한 거리의 이미지는 강하지만,
중심 핵과 게이트, 셀 경계까지 담기에는 좁다.

그래서 AIOS에서는 아래처럼 나누는 편이 가장 안정적이다.

- 상위 이름:
  - `Kernel Room Topology`
- 하위 실행 모델:
  - `Orbit-Cell Node Model`

## 핵심 용어

### Kernel Room

커널의 중심 핵이다.
시스템 전체가 공유하는 최소 기반 상태를 묶는다.

현재 코드에서 가장 가까운 기반:

- `include/kernel/kernel_room.h`
- `kernel/kernel_room.c`
- `kernel/main.c`
- `kernel/health.c`
- 일부 `runtime/ai_syscall.c`

판정:

- 부분 구현

### Axis Gate

아무 경로로나 커널을 건드리지 않고,
기능별 게이트를 통해서만 접근하는 모델이다.

예:

- memory gate
- device gate
- scheduler gate
- policy gate
- telemetry gate

현재 코드에서 가장 가까운 기반:

- `include/kernel/kernel_room.h`
- `kernel/kernel_room.c`
- `runtime/ai_syscall.c`
- `kernel/health.c`

판정:

- 부분 구현

### Orbit

권한, 지연 민감도, 위험도, 응답성을 함께 나타내는 거리 개념이다.

현재 코드에서 가장 가까운 기반:

- `sched/ai_sched.c`의 큐/우선순위
- `kernel/health.c`의 stability 단계

판정:

- 개념 일부만 존재

즉, 명시적 Orbit runtime은 아직 없다.

### Cell

자원 분배와 격리의 최소 관리 단위다.
메모리 도메인, 로컬 budget, ring/queue 바인딩, risk/health 범위를 함께 다루는 단위로 본다.

현재 코드에서 가장 가까운 기반:

- `mm/memory_fabric.c`의 domain/window

판정:

- 직접 구현 없음, 기반만 존재

### Node

실제로 동작하는 실행 주체다.
AI agent, planner, broker, worker, device service 같은 단위를 여기로 본다.

현재 코드에서 가장 가까운 기반:

- `runtime/slm_orchestrator.c`의 plan / action / agent tree

판정:

- 부분 구현

## 현재 AIOS에 실제로 적용된 수준

정확히 말하면, 지금 AIOS에는
`Kernel Room Topology`가 "정식 런타임 구조"로 완성돼 있지는 않다.

있는 것은 아래와 같은 기반들이다.

- 중심 상태를 모으는 부팅/health 흐름
- `kernel_room_snapshot_read()` 기반의 read-only Kernel Room registry
- syscall과 health를 통한 bounded gate
- `Axis Gate descriptor` 정적 테이블
- memory domain/window 기반의 Cell 재료
- SLM plan/action 기반의 Node 재료

즉, 현재 상태는 다음 표현이 가장 맞다.

```text
Topology model: adopted as design language
Runtime model: not fully applied yet
```

## 디렉토리 운영 기준

이 구조 관련 문서는 앞으로 `docs/kernel-room/` 아래에서 관리한다.

문서 분류 기준:

- 상위 개념 정리:
  - `kernel_room_topology_ko.md`
- 개발 규칙 정리:
  - `development_guide_ko.md`
- 구현 가능성/현실 체크:
  - `orbit_cell_node_feasibility_ko.md`
- 이후 세부 설계:
  - gate
  - cell metadata
  - node binding
  - room snapshot/UAPI

## 구현 우선순위

현재 코드 기준 가장 먼저 붙이기 좋은 순서는 아래다.

1. `Kernel Room registry`
2. `Axis Gate descriptor`
3. `Cell metadata`
4. `Node-to-Cell binding`

반대로 아래는 아직 이르다.

- full Orbit privilege runtime
- distributed node mesh
- self-modifying kernel narrative

## 결론

AIOS에서 이 구조를 부를 이름은
`Kernel Room Topology`가 가장 안정적이다.

그리고 실제 분배/격리/실행 모델은
그 아래의 `Orbit-Cell Node Model`로 구분해서 다루는 편이 좋다.

이렇게 나누면 문서도 확장하기 쉽고,
현재 구현 상태를 과장하지 않으면서 다음 단계를 연결하기도 쉬워진다.
