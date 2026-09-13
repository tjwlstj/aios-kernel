# Kernel Room 문서 모음

최종 갱신: 2026-09-13 (환경 문맥·Task의 한정 실제 모델 PASS 경계 동기화)

## 정본

Kernel Room의 관리 의미, 용어, 성숙도, 분야별 구현 의존 순서는
[AIOS Kernel Room 관리 모델](kernel_room_management_model_ko.md)이 정본이다.
AI가 작업 공간·상태·가능한 행동을 이해하고 사용자와 지속 상호작용한다는 목적은
[제품 방향 정본](../meta/aios_product_direction_ko.md)을 따른다. 이 목적에서 로컬 자원 효율과
상호작용은 함께 평가하며, 관리축의 `DIRECT` 여부가 제품 가치의 우선순위를 대신하지 않는다.

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
사용자에게 보이는 공간·아이콘·거리와 NodeBit·Orbit를 자동으로 동일시하지 않는다.

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
- H1 OS-neutral trace/replay (2026-09-03)
  - H1-a/b/c contract·12 fixtures·artifact/parity의 동일 run·exact SHA 원격 acceptance
  - [H1 원격 acceptance 증거 (§13.2)](../os/h1_binding_trace_replay_workplan_ko.md#132-2026-09-03-원격-acceptance-완료)의
    Linux/Windows/parity 세 job과 세 artifact 검증; live producer는 없음

### `PARTIAL`

- Kernel Room 전체 토폴로지
  - aggregate substrate, K1 bootstrap hierarchy v0, bounded native K2-a oracle은 존재
  - 별도 hosted MAIN의 실제 모델·명시적 결속/재결속, Cell 1 관리 전이와 backend 수명/실행 결속이 있음
  - MAIN/backend CPU·RSS는 명시적 관계 아래 관측하지만 system PSI는 unattributed, ownership은 없음
  - native live lifecycle, 전체 source coverage·H2/H3 및 부팅 간 canonical 상태 복원은 미완료
- MAIN의 제한된 환경 문맥·UUID Task 접수/조회/결과/취소 기능
  - 보존된 v0.8 실제 문맥 소비 재생과 v0.10 프로세스 fixture는 별도 증거
  - 실제 모델 Task의 한정 흐름은 PASS; source 39개 운영 이미지 acceptance는 미완료

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
- 이전 대화·범용 작업 수정·CLI 소실/재부팅 뒤 자동 재개

현재 MAIN의 환경 문맥·Task는 [환경 문맥 가이드](../os/aios_space_context_guide_ko.md)가
계약과 증거를 소유한다. 이전 대화·선택 workspace 자동 문맥은 없다. 실제 전달 문맥·
소비자/모델·답변/행동은 각 source의 실행으로 연결하며 fixture만으로 완료하지 않는다. 자원 관측은 효율 개선의 비교 증거와 구분한다.

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
- [제품 방향 정본](../meta/aios_product_direction_ko.md)
  - AI의 작업 공간 이해, 로컬 효율, 사용자와의 지속 상호작용이라는 제품 결과
- [성숙도 작업흐름](../meta/minimal_io_and_maturity_workflow_ko.md#agent-consumer-next)
  - 전역 다음 작업과 완료 기준. 관리 분야의 확장 목록과 구분
- [orbit_cell_node_feasibility_ko.md](orbit_cell_node_feasibility_ko.md)
  - `REVIEW`: 2026-04 탐색 기록. 현재 구현 지침으로 사용하지 않는다.

## K1, native K2-a와 H1 구현 범위

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
refresh/reconcile이나 hosted source는 아니다. 같은 semantic field와 reject 의미는
OS-neutral H1 trace/replay로 옮겨 원격 cross-OS acceptance까지 통과했다. 별도 hosted
MAIN/Cell/backend의 bounded 실행 증거는 관리 모델과 각 운영 가이드가 소유한다.
전체 native/hosted lifecycle·reconciliation은 후속이며, 전역 다음 조각은
[에이전트 소비 흐름](../meta/minimal_io_and_maturity_workflow_ko.md#agent-consumer-next)이다.
aggregate snapshot의
`domains`, `nodes`, `nodebit_active` count는 여전히 canonical hierarchy나 binding
증거가 아니다.

## 금지되는 방향

- Cell/Node identity보다 per-syscall enforcement를 먼저 구현하는 것
- gate descriptor와 NodeBit을 같은 정책표로 취급하는 것
- 서로 다른 Node namespace를 숫자 일치만으로 연결하는 것
- Orbit를 scheduler의 다른 이름으로 구현하는 것
- 커널 실행 기반의 성숙도를 Kernel Room 관리 모델의 성숙도로 대신하는 것
