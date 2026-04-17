# Kernel Room Topology

작성일: 2026-04-18

## 목적

이 디렉토리는 AIOS의 `Kernel Room Topology` 관련 문서를 한곳에서 관리하기 위한
전용 문서 공간이다.

이 이름은 커널을 단순한 평면 레이어가 아니라,
중심 핵과 접근 축, 권한 거리, 격리 단위, 실행 주체가 함께 작동하는
입체적 구조로 보는 관점을 담는다.

## 현재 범위

2026-04-18 기준 이 디렉토리에 있는 문서는
"현재 구현된 것"과 "개념적으로 유효하지만 아직 구현되지 않은 것"을 구분해 다룬다.

- 구현 또는 부분 구현:
  - `include/kernel/kernel_room.h`
  - `kernel/kernel_room.c`
  - `kernel/main.c`
  - `kernel/health.c`
  - `runtime/ai_syscall.c`
  - `runtime/slm_orchestrator.c`
  - `mm/memory_fabric.c`
- 개념/계획 중심:
  - explicit Orbit runtime
  - full Cell lifecycle/runtime
  - ring3 userspace node execution model

즉, 지금 이 디렉토리는 "완성된 새로운 커널 계층" 문서가 아니라
"현재 코드 위에 어떤 구조를 얹을 수 있는가"를 정리하는 설계 공간에 가깝다.

## 문서 구성

- `kernel_room_topology_ko.md`
  - 상위 개념명, 용어 정의, 디렉토리 운영 기준
- `development_guide_ko.md`
  - 구현 경계, marker, 확장 순서, 문서 동기화 규칙
- `orbit_cell_node_feasibility_ko.md`
  - 현재 저장소 기준 구현 가능성 검토

## 네이밍 기준

- 상위 개념명:
  - `Kernel Room Topology`
- 세부 구조명:
  - `Orbit-Cell Node Model`
- 보조 용어:
  - `Axis Gate`

이 기준을 쓰면,
`Kernel Room`은 전체 철학과 구조를 담고,
`Orbit-Cell Node`는 실제 분배/배치/격리/실행 단위를 설명하는 하위 모델로
분리해 다룰 수 있다.

## 다음 확장 후보

구현과 문서가 더 자라면 아래 파일을 이 디렉토리 아래에 추가하는 방식이 자연스럽다.

- `axis_gate_registry_ko.md`
- `cell_metadata_registry_ko.md`
- `node_binding_runtime_ko.md`
- `kernel_room_snapshot_uapi_ko.md`

## 주의

이 디렉토리 문서는 구현 상태를 과장하지 않는다.
`Orbit`, `Cell`, `Node`는 모두 같은 성숙도에 있지 않으며,
현재 AIOS에서는 일부는 기반만 있고 일부는 부분 적용 상태다.
