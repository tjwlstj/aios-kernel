# AIOS Kernel Room Topology 개발 가이드

작성일: 2026-04-18

## 목적

이 문서는 `Kernel Room Topology` 관련 개발 규칙을 정규화한다.

핵심은 세 가지다.

1. 현재 구현 범위를 과장하지 않는다
2. 구조 이름과 로그 marker를 안정적으로 유지한다
3. 다음 단계 구현이 같은 방향으로 이어지게 한다

## 현재 구현 범위

2026-04-18 기준 현재 코드에 실제로 들어간 기반은 아래까지다.

- `kernel/kernel_room.c`
  - read-only `Kernel Room snapshot`
  - `Axis Gate descriptor` table
  - boot / `sys_info_dump()` observable marker
- `include/kernel/kernel_room.h`
  - snapshot / gate public contract
- `runtime/ai_syscall.c`
  - `SYS_INFO_ROOM` read-only info surface

아직 없는 것:

- explicit Orbit runtime
- Cell lifecycle runtime
- Node-to-Cell binding runtime
- ring3 userspace supervisor integration

즉, 이 가이드는 "완성된 새 런타임"이 아니라
"작은 foundation을 흔들리지 않게 키우기 위한 규칙"을 다룬다.

## 파일 책임

### `include/kernel/kernel_room.h`

여기에는 아래만 둔다.

- gate id
- gate risk enum
- read-only snapshot struct
- descriptor struct
- public helper / dump / snapshot API

여기에는 두지 않는다.

- syscall dispatch 구현
- SLM 정책 로직
- memory_fabric의 세부 상태 머신

### `kernel/kernel_room.c`

여기에는 아래만 둔다.

- 현재 커널 상태를 모으는 snapshot glue
- gate descriptor 정적 테이블
- boot / dump용 관측 출력

여기에는 두지 않는다.

- 새로운 policy apply 로직
- 드라이버 직접 제어
- userspace handoff

### `runtime/ai_syscall.c`

여기서는 아래만 연결한다.

- `SYS_INFO_ROOM`
- `sys_info_dump()`에서의 room dump 호출

즉, `runtime/`는 Kernel Room을 "사용"만 하고,
정의와 집계는 `kernel/`에 둔다.

## 네이밍 규칙

상위 구조:

- `Kernel Room Topology`

하위 실행 모델:

- `Orbit-Cell Node Model`

코드 심볼:

- `kernel_room_*`
- `KERNEL_ROOM_GATE_*`

로그 marker:

- `[ROOM] snapshot ...`
- `[ROOM] gates ...`

이 marker는 testkit과 문서가 참조하므로
필드 순서와 키 이름을 자주 바꾸지 않는 편이 좋다.

## Gate 규칙

`Axis Gate`는 "정책 엔진"이 아니라 "정적 메타데이터"로 먼저 유지한다.

초기 단계에서 gate는 아래만 가져야 한다.

- syscall range
- risk class
- minimum stability
- completion/shared-memory 가능 여부
- risky I/O 여부

초기 단계에서 gate에 넣지 않는 것:

- 동적 권한 계산
- per-process capability bitmap
- live policy mutation

즉, gate는 먼저 "설명 가능한 지도"가 되고,
그 다음에야 verifier/capability와 연결한다.

## Snapshot 규칙

`Kernel Room snapshot`은 가능한 한
기존 모듈의 read-only API만 재사용해서 만들어야 한다.

좋은 입력:

- `kernel_health_get_summary()`
- `memory_fabric_profile()`
- `memory_fabric_domain_count()`
- `memory_fabric_window_count()`
- `driver_model_snapshot_read()`
- `slm_snapshot_read()`
- `slm_plan_list()`
- `ai_infer_ring_runtime()`
- `user_mode_scaffold_info()`

피할 것:

- 내부 static 배열을 직접 노출
- 다른 모듈의 private lock에 직접 의존
- snapshot을 위해 새 mutable state를 늘리는 것

## 로그 / 테스트 규칙

새 기반 구조는 반드시 boot-observable 해야 한다.

최소 기준:

- serial log에 `[ROOM] snapshot`
- serial log에 `[ROOM] gates`
- testkit strict smoke가 `[ROOM] snapshot`을 요구
- boot summary JSON에 `kernel_room` 필드가 들어감

즉, 이 구조는 "코드만 존재"하는 게 아니라
"부팅에서 관측 가능"해야 한다.

## 다음 구현 순서

현재 foundation 위에서 다음으로 자연스러운 순서는 아래다.

1. `Cell metadata`
   - `memory_fabric` domain과 연결
2. `Node-to-Cell binding`
   - `slm_orchestrator` plan / agent tree에 logical binding 추가
3. `room snapshot UAPI` 확장
   - 필요 시 read-only dump 구조 확장
4. `Axis Gate verifier`
   - health / risk / capability를 gate 메타데이터와 연결

반대로 아직 이른 것:

- full Orbit scheduler/runtime rewrite
- distributed node mesh
- self-modifying kernel narrative

## 문서 동기화 규칙

`Kernel Room` 관련 문서를 갱신할 때는 아래를 함께 본다.

- `docs/kernel-room/README.md`
- `docs/kernel-room/kernel_room_topology_ko.md`
- `docs/kernel-room/orbit_cell_node_feasibility_ko.md`
- 이 문서

그리고 구현 변화가 있으면 최소한 아래 중 하나는 같이 갱신한다.

- 루트 `README.md`
- `testkit/README.md`
- `docs/testkit_guide_ko.md`

## 결론

현재 AIOS에서 `Kernel Room Topology`는
"큰 서사를 먼저 넣는 구조"가 아니라
"작은 read-only foundation을 흔들림 없이 키우는 구조"로 다루는 편이 맞다.

그래서 지금 개발 기준도
`snapshot -> gate metadata -> test observable -> doc sync`
순서로 유지하는 것이 가장 안전하다.
