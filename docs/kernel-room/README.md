# Kernel Room 문서 모음

최종 갱신: 2026-08-10

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
- Cell/Node 입력 후보가 되는 Memory Fabric, SLM agent tree, 두 NodeBit subsystem의
  각자 독립된 현재 표면

### `PARTIAL`

- Kernel Room 전체 토폴로지
  - aggregate substrate는 존재
  - Cell registry와 hierarchy는 없음

### `SCAFFOLD`

- Memory Fabric domain/window, SLM agent tree, runtime NodeBit, SLM NodeBit을
  Cell/Node/NodeBit 관리 record로 투영할 adapter seam
- 이 subsystem들은 각자 구현돼 있지만, Kernel Room 관리 계층과의 결속은 scaffold다.

### `PLANNED`

- Room-to-Cell registry와 Cell lifecycle/state
- Node-to-Cell binding
- typed management NodeBit view와 generation/validity
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

## 다음 조각

다음 Kernel Room 조각은 enforcement가 아니다.

`management_only read-only hierarchy registry v0`로 시작한다.

v0는 Cell-only table이 아니다. 최소 Cell 1개, 그 Cell에 exact-one binding된 managed
Node 1개, 그 Node를 부모로 가리키는 typed NodeBit 1~2개를 한 snapshot에서 함께
증명해야 한다.

- bounded Room/Cell/Node/NodeBit record
- typed namespace와 explicit binding
- schema/size/generation/validity
- `observation_only=1`, `management_only=1`
- duplicate/orphan/stale/overflow fail-closed selftest

이 조각이 검증되기 전에는 aggregate snapshot의 `domains`, `nodes`, `nodebit_active`
count를 Cell/Node hierarchy가 존재한다는 증거로 사용하지 않는다.

## 금지되는 방향

- Cell/Node identity보다 per-syscall enforcement를 먼저 구현하는 것
- gate descriptor와 NodeBit을 같은 정책표로 취급하는 것
- 서로 다른 Node namespace를 숫자 일치만으로 연결하는 것
- Orbit를 scheduler의 다른 이름으로 구현하는 것
- 커널 실행 기반의 성숙도를 Kernel Room 관리 모델의 성숙도로 대신하는 것
