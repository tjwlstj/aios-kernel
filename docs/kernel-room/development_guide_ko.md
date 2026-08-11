# AIOS Kernel Room 개발 가이드

작성일: 2026-04-18
재정비: 2026-08-10
최종 갱신: 2026-08-11 (K2 source adapter 착수 gate)

> 이 가이드는 [AIOS Kernel Room 관리 모델](kernel_room_management_model_ko.md)을
> 따른다. 정체성, 용어, 성숙도 또는 구현 순서가 충돌하면 정본을 우선한다.

## 목적

Kernel Room 작업이 커널 작동 증명이나 enforcement 자체를 목표로 삼지 않고,
`Room -> Cell -> Node -> NodeBit` 관리 계층을 작은 검증 조각으로 완성하도록 한다.

핵심 규칙은 다섯 가지다.

1. 관리 모델과 커널 substrate를 분리한다.
2. Cell/Node identity와 binding을 gate enforcement보다 먼저 고정한다.
3. 기존 subsystem의 원본 상태와 Kernel Room 관리 record를 구분한다.
4. `CURRENT`, `PARTIAL`, `SCAFFOLD`, `PLANNED`, `RESEARCH`를 섞지 않는다.
5. 첫 구현은 management-only, read-only, fail-closed다.

## 현재 출발점

### `CURRENT`

- `kernel/core/kernel_room.c`
  - aggregate read-only `kernel_room_snapshot_read()`
  - 9개 Axis Gate descriptor
  - `[ROOM] snapshot`, `[ROOM] gates`
- `kernel/include/kernel/kernel_room.h`
  - 현재 aggregate snapshot과 gate public contract
- `kernel/core/kernel_room_management.c`
  - K1 immutable-after-init bounded hierarchy registry와 negative selftest
  - exact `[ROOM] management hierarchy selftest PASS ...` producer
- `kernel/include/kernel/kernel_room_management.h`
  - schema 1의 Cell/Node/NodeBit typed record와 1024B snapshot contract
- `kernel/core/shell.c`
  - `state room` exact read-only mirror
- `kernel/runtime/ai_syscall.c`
  - `SYS_INFO_ROOM` read-only surface

### subsystem에서는 `CURRENT`, 관리 adapter로는 `SCAFFOLD`

- Memory Fabric domain/window
- SLM agent tree
- runtime NodeBit capability table와 per-node stats
- SLM NodeBit catalog와 generation
- process/pipeline ownership, pressure/resource snapshot

### K1 `CURRENT` (2026-08-11)

- capacity 2/4/8의 Cell/Node/NodeBit registry
- bootstrap Cell 1 + bound Node 1 + parent-bound typed NodeBit 2
- schema/size/source/generation/parent validity와 immutable snapshot
- duplicate/orphan/unknown/stale/overflow와 non-zero unused tail rejection
- default full/minimal/storage-only와 max-smap minimal strict kernel, default/max-smap
  strict shell 17/17 검증 및 structured summary export

### 아직 `PLANNED`

- external source Node-to-Cell binding과 Cell lifecycle/reconciliation
- runtime/SLM NodeBit projection
- per-Cell/per-Node pressure와 resource ownership
- principal/authorize와 Axis Gate enforcement

### `RESEARCH`

- Orbit runtime
- distributed Cell/Node mesh
- 프랙탈·거리 기반 배치

## 작업 전 분류

Kernel Room 변경을 시작하기 전에 요청을 아래 중 하나로 분류한다.

### A. management model 변경

- Cell/Node/NodeBit ID, record, binding, generation, validity
- hierarchy snapshot과 verifier
- source adapter와 ownership 관계

이 가이드의 주 대상이다.

### B. substrate 변경

- process switching, storage, driver, MM, scheduler, hardening
- 각 subsystem의 raw snapshot

Kernel Room 입력을 개선할 수 있지만, 그 자체로 관리 모델을 승격하지 않는다.

### C. transition/enforcement 변경

- principal, authorize, per-syscall gate, apply, rollback

관리 registry와 identity 계약 뒤의 단계다. Cell/Node 의미가 정해지지 않았다면 먼저
enforcement를 구현하지 않는다.

## 관리 record 규칙

### Namespace

- Cell, management Node, source Node, NodeBit의 ID 타입을 구분한다.
- public numeric ID는 append-only로 유지한다.
- 서로 다른 namespace의 숫자가 같다는 이유로 binding하지 않는다.
- source binding은 `source_kind + source_id`처럼 명시적이어야 한다.

### 관계

- managed Node는 정확히 하나의 active Cell에 결속한다.
- 아직 결속되지 않은 source는 `UNBOUND`로 관측하되 managed/bound count에서 제외한다.
- NodeBit은 존재하는 managed Node를 가리켜야 한다.
- duplicate, orphan, unknown source는 fail-closed다.

### State와 validity

- lifecycle, health, risk, resource, pressure, capability, eligibility를 한 scalar나
  무구분 bitmap으로 합치지 않는다.
- 각 필드는 source와 validity를 가져야 한다.
- 값 0을 지원됨, 없음, 미관측의 세 의미로 동시에 사용하지 않는다.
- pressure ranking과 eligibility bitmap은 별도 축으로 유지한다.

### Versioning과 consistency

- public snapshot은 `schema_version`, `struct_size`, `generation`을 가진다.
- bounded capacity와 실제 count를 분리한다.
- extension, truncation, stale generation을 검출할 수 있어야 한다.
- current aggregate room snapshot은 multi-source atomic transaction이 아니다. 새로운
  hierarchy snapshot이 best-effort이면 그렇게 기록하고, 강한 원자성을 주장하려면
  writer가 참여하는 generation/seqlock 계약을 별도로 검증한다.

## 파일 책임

### `kernel/include/kernel/kernel_room.h`

현재 aggregate snapshot과 gate descriptor ABI를 보존한다. management registry v0를
같은 ABI에 무심코 끼워 넣지 않는다. 새 public record가 필요하면 별도 versioned contract로
정의하고 기존 snapshot 호환성을 유지한다.

### `kernel/core/kernel_room.c`

현재 책임은 aggregate snapshot glue, static gate metadata, boot/dump 관측이다.
이 파일에 다른 subsystem의 private state machine이나 lock을 옮기지 않는다.

### `kernel/include/kernel/kernel_room_management.h`

K1 public typed record와 1024B snapshot을 소유한다. Cell/Node/NodeBit의 namespace,
append-only enum, record size static assert, capacity 2/4/8을 바꿀 때는 schema와 모든
consumer를 함께 검토한다.

### `kernel/core/kernel_room_management.c`

K1 bootstrap identity/relation/generation과 immutable-after-init snapshot을 소유한다.
여기에 external subsystem의 private state나 Axis Gate authorize를 넣지 않는다.

아래 책임은 계속 분리한다.

- registry가 소유하는 identity/relation/generation
- subsystem adapter가 제공하는 copied read-only state
- 후속 Axis Gate가 수행하는 transition/authorize

### 기존 subsystem

Memory Fabric, SLM, scheduler, resource, pressure, process, pipeline은 자기 mutable state의
원본 소유자다. Kernel Room 때문에 private 배열을 외부에 노출하지 말고 bounded read API나
명시적인 adapter를 사용한다.

## 첫 수직 조각

첫 조각은 `management_only read-only hierarchy registry v0`로 구현됐다.

Cell table만 있는 상태는 이 조각의 완료가 아니다. 동일한 hierarchy snapshot에서
`Cell 1개`, 그 Cell에 exact-one binding된 `managed Node 1개`, 그 Node를 부모로
가리키는 typed `NodeBit 2개`를 함께 증명한다. 이후 adapter 단계는 이 최소
계층을 새로 만드는 단계가 아니라 실제 source coverage를 확대하는 단계다.

### 목표

- bounded Room/Cell/Node/NodeBit record
- typed namespace와 explicit source binding
- boot-seeded Cell ID 1, exact-one binding을 가진 managed Node ID 101,
  parent-bound typed NodeBit ID 1001/1002
- schema/size/generation/validity
- read-only hierarchy snapshot
- `observation_only=1`, `management_only=1`

### 금지

- per-syscall Axis Gate enforcement
- runtime/SLM NodeBit table 통합 또는 mutation
- scheduler migration과 budget apply
- allocator reserve/release/throttle
- process principal 도입
- Orbit scheduling

### 최소 불변식

- unique Cell/Node ID
- managed Node 전부 exact-one Cell binding
- NodeBit 전부 valid managed Node 참조
- source namespace collision 없음
- capacity overflow와 stale generation 거부
- 사용하지 않는 capacity tail의 non-zero record 거부
- 기존 aggregate Room marker와 ABI 유지

## 관측과 검증

새 관리 구조는 boot-observable이어야 하지만 marker를 만드는 것 자체가 목표는 아니다.
marker는 hierarchy invariant를 증명해야 한다.

현재 exact record:

```text
[ROOM] management hierarchy selftest PASS schema=1 struct_size=1024 generation=1 cells=1 nodes=1 bound_nodes=1 nodebits=2 bound_nodebits=2 source_valid=1 generation_valid=1 duplicate_rejected=1 orphan_rejected=1 unknown_rejected=1 stale_rejected=1 overflow_rejected=1 tail_rejected=1 observation_only=1 management_only=1
```

검증기는 최소한 아래 반례를 거부한다.

- required record 누락, 중복, 순서 위반
- 잘린 record와 예상하지 않은 필드 확장
- duplicate Cell/Node ID
- orphan Node와 unknown NodeBit target
- namespace를 무시한 implicit binding
- stale generation
- capacity overflow 또는 dropped record
- non-zero unused capacity tail
- `observation_only=0`이나 `management_only=0`

관측면 권장 순서:

1. internal selftest
2. exact boot marker
3. structured boot summary의 별도 `kernel_room_management` field
4. `state room`의 같은 의미 mirror
5. 필요성이 증명된 뒤에만 read-only UAPI

기존 `[ROOM] snapshot`, `[ROOM] gates`, `SYS_INFO_ROOM`은 새 hierarchy proof와 별개의
호환 표면으로 유지한다.

## K2 source adapter 착수 gate

K2 source를 canonical hierarchy에 연결하기 전에 아래를 모두 증명한다.

- canonical Node/Cell kind와 source의 semantic kind가 일치한다.
- source namespace와 ID는 typed이며 canonical ID와 분리된다.
- source producer가 instance/generation을 직접 소유하고 copied read API로 제공한다.
- init ordering, refresh/reconcile, source exit/recreate 의미가 정의된다.
- missing, duplicate, role mismatch, orphan, zero/regressed/stale generation을
  fail-closed fixture로 거부한다.
- binding record는 별도 bounded/versioned snapshot이며 K1 1024B ABI와 기존 aggregate
  Room ABI를 변경하지 않는다.

Node 101 `AI_SERVICE`의 우선 후보는 SLM agent-tree MAIN source다. 현재 SLM
`policy_generation`은 agent-tree source generation 계약이 아니므로 재사용하지 않는다.
Memory Fabric main domain은 Cell/resource source 후보이고 bootstrap process는
execution-instance source 후보다. 구현이 쉽거나 숫자가 같다는 이유로 Node 101에
결속하지 않는다. Linux PID/cgroup/pidfd/PSI도 `source_only`이며 같은 gate를 우회하지
않는다.

## 후속 구현 순서

1. 정본과 typed namespace 대응표
2. management-only hierarchy registry v0 — `CURRENT` (2026-08-11)
3. Cell state adapter와 validity/source flags
4. Node-to-Cell binding source 확대
5. runtime/SLM NodeBit의 typed management view와 generation 연결
6. per-Cell/per-Node pressure·resource attribution observation
7. principal과 state-transition request 계약
8. Axis Gate authorize/enforcement + deny/rollback proof
9. 선택적 Orbit 연구

2단계의 완료 조건은 최소 Cell/Node/NodeBit 전체 hierarchy proof다. 3~5단계를
Cell-only -> Node-only -> NodeBit-only의 별도 완성 단계로 해석하지 않는다.

`snapshot -> gate metadata -> enforcement`를 Kernel Room의 기본 성장 순서로 쓰지 않는다.
현재 snapshot과 gate는 보존할 substrate이며, 다음 중심 순서는
`identity -> relation -> state -> observation -> transition -> enforcement`다.

## Axis Gate 규칙

현재 descriptor는 `CURRENT` classification metadata다. dispatcher가 per-call로 이를
검사한다고 서술하지 않는다.

후속 enforcement는 아래 조건이 모두 갖춰진 뒤에만 시작한다.

- principal/caller identity
- target managed Node와 Cell binding
- NodeBit capability/validity/generation
- Cell health/risk state
- deny reason과 rollback boundary
- bypass 불가를 증명하는 negative test

Gate descriptor와 NodeBit은 같은 정책표가 아니다. Gate는 요청의 종류와 전이 경계를,
NodeBit은 Node의 상태와 capability를 표현한다. 둘은 typed decision contract로 연결하되
무조건 하나의 bitmap으로 합치지 않는다.

## Orbit 규칙

Orbit는 `RESEARCH`다. 다음이 생기기 전에는 runtime이나 ABI를 만들지 않는다.

- 실제 Cell/Node hierarchy evidence
- risk/latency/locality source와 validity
- 기존 scheduler로 표현할 수 없는 구체적 요구
- 비교 가능한 실험과 rollback 기준

## 문서 동기화

Kernel Room 문서를 바꿀 때는 아래 다섯 파일을 함께 확인한다.

- `docs/kernel-room/kernel_room_management_model_ko.md`
- `docs/kernel-room/README.md`
- `docs/kernel-room/kernel_room_topology_ko.md`
- `docs/kernel-room/development_guide_ko.md`
- `docs/kernel-room/orbit_cell_node_feasibility_ko.md`

구현이나 maturity가 바뀌는 후속 패치에서는 mirror 문서와 verifier를 같은 패치에서
갱신한다.

- `README.md`
- `CLAUDE.md`
- `PROJECT.md`
- `docs/meta/codex_handoff_tips_ko.md`
- 현재 roadmap
- `tools/testkit` parser/verdict/summary/shell 문서

## 완료 보고 규칙

작업자는 다음을 분리해 보고한다.

- 새로 `CURRENT`가 된 정확한 record와 verifier
- 여전히 `PARTIAL`인 전체 모델
- adapter만 있는 `SCAFFOLD`
- 구현하지 않은 `PLANNED`
- 선택적 `RESEARCH`
- 실행하지 않은 검증 lane

정상 부팅, marker 개수, process switching 또는 driver probe를 Cell/Node hierarchy가
완성됐다는 증거로 사용하지 않는다.
