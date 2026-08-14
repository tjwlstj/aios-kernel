# Kernel Room 문서 모음

최종 갱신: 2026-08-15

## 정본

Kernel Room의 정체성, 용어, 성숙도, 구현 순서는
[AIOS Kernel Room 관리 모델](kernel_room_management_model_ko.md)이 정본이다.

한 줄로 말하면 Kernel Room은 단순한 커널 상태 계기판이나 시스콜 방화벽이 아니라,
`Room -> Cell -> Node -> NodeBit` 계층의 식별자, 관계, 상태, 유효성, 세대를 관리하는
운영 핵이다.

```text
Room
└─ Cell state
   └─ Node
      └─ NodeBit
```

`Axis Gate`는 이 상태를 바꾸려는 요청의 후속 전이·보안 경계다. `Orbit`는 현재
구현 약속이 아닌 `RESEARCH` 관점이다.

## 현재 정확한 범위

### `CURRENT`

- aggregate read-only `kernel_room_snapshot_read()`
- `[ROOM] snapshot`과 `[ROOM] gates` 부트 관측
- syscall range를 분류하는 9개 Axis Gate descriptor
- K1 bounded `kernel_room_management_snapshot_t` (2026-08-11 strict QEMU/shell 검증): 1024B 안에 bootstrap Cell 1,
  exact-bound Node 1, parent-bound typed NodeBit 2를 보존하는 read-only 계층
- `[ROOM] management hierarchy selftest PASS`, structured
  `kernel_room_management`, `state room` full-row 검증 계약
- native K2-a `kernel_room_source_binding_snapshot_t`: K1 1024B ABI를 바꾸지 않는
  별도 schema 1/256B snapshot으로 Node 101과 producer-owned SLM MAIN source를 결속
- `[ROOM] source binding selftest PASS`, structured `kernel_room_binding`,
  `state binding` exact full-row 검증 계약
- Cell/Node 입력 후보가 되는 Memory Fabric, SLM agent tree, 두 NodeBit subsystem의
  각자 독립된 현재 표면

### `PARTIAL`

- Kernel Room 전체 토폴로지
  - aggregate substrate, K1 bootstrap hierarchy v0, bounded native K2-a oracle은 존재
  - K2 live lifecycle/reconciliation, hosted source, attribution은 없음

### `SCAFFOLD`

- Memory Fabric domain/window, SLM policy tree, runtime NodeBit, SLM NodeBit을
  Cell/Node/NodeBit 관리 record로 투영할 adapter seam
- 이 subsystem들은 각자 구현돼 있지만, SLM MAIN의 bounded native K2-a 결속을 제외한
  관리 계층 projection은 scaffold다.

### `PLANNED`

- K2 source lifecycle/reconciliation과 hosted source 확대
- K3 runtime/SLM NodeBit namespace projection
- per-Cell/per-Node pressure와 resource attribution
- principal과 Axis Gate authorize/enforcement

### `RESEARCH`

- Orbit runtime
- 분산 Cell/Node mesh
- 프랙탈·거리 기반 배치 모델

## 문서 구성

- [kernel_room_management_model_ko.md](kernel_room_management_model_ko.md)
  - **정본**: 관리 권위, 핵심 단위, 불변식, 첫 구현 조각, 성숙도
- [kernel_room_topology_ko.md](kernel_room_topology_ko.md)
  - 정본을 설명하는 개념·토폴로지 뷰
- [development_guide_ko.md](development_guide_ko.md)
  - 실제 작업 순서, 파일 경계, 검증과 문서 동기화 규칙
- [orbit_cell_node_feasibility_ko.md](orbit_cell_node_feasibility_ko.md)
  - `REVIEW`: 2026-04 탐색 기록. 현재 구현 지침으로 사용하지 않는다.

## K1과 native K2-a 구현 범위, 다음 조각

K1 `management_only read-only hierarchy registry v0`는 다음 고정 계약을 구현한다.

v0는 Cell-only table이 아니다. Cell ID 1, 그 Cell에 exact-one binding된 Node ID 101,
그 Node를 부모로 가리키는 typed NodeBit ID 1001/1002를 한 snapshot에서 함께 증명한다.

- 1024B snapshot, capacity Cell 2 / Node 4 / NodeBit 8
- typed namespace와 explicit binding
- schema 1, generation/source validity
- `observation_only=1`, `management_only=1`
- duplicate/orphan/unknown/stale/overflow와 non-zero unused tail fail-closed selftest

native K2-a는 이 bootstrap fixture의 Node 101을 exact-one active/persistent SLM MAIN
source에 명시적으로 bind한다. canonical, binding, source generation을 분리하고
producer-owned copied snapshot, boot-order, malformed/duplicate/orphan/mismatch/zero/
rollback/stale/tail 거부를 검증한다. 이 oracle은 boot-local immutable proof일 뿐 live
refresh/reconcile이나 hosted source는 아니다. 다음 직접 조각은 같은 semantic field와
reject 의미를 OS-neutral H1 trace/replay로 옮기는 것이다. aggregate snapshot의
`domains`, `nodes`, `nodebit_active` count는 여전히 canonical hierarchy나 binding
증거가 아니다.

## 금지되는 방향

- Cell/Node identity보다 per-syscall enforcement를 먼저 구현하는 것
- gate descriptor와 NodeBit을 같은 정책표로 취급하는 것
- 서로 다른 Node namespace를 숫자 일치만으로 연결하는 것
- Orbit를 scheduler의 다른 이름으로 구현하는 것
- 커널 실행 기반의 성숙도를 Kernel Room 관리 모델의 성숙도로 대신하는 것
