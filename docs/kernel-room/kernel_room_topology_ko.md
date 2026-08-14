# AIOS Kernel Room Topology

작성일: 2026-04-18
재정비: 2026-08-10
최종 갱신: 2026-08-15 (native K2-a source binding oracle 반영)
문서 성격: 정본 관리 모델을 설명하는 개념 뷰

> 용어, 성숙도, 구현 순서의 정본은
> [AIOS Kernel Room 관리 모델](kernel_room_management_model_ko.md)이다.

## 목적

이 문서는 Kernel Room을 시각적·개념적 토폴로지로 설명한다. 구현 권위는 정본에
두고, 여기서는 각 단위가 어떤 관계에 놓이는지만 정리한다.

## 한 줄 정의

`Kernel Room Topology`는 `Room -> Cell -> Node -> NodeBit` 관리 계층과, 그 상태를
바꾸려는 요청의 `Axis Gate`, 상태로부터 파생되는 선택적 `Orbit` 관점을 구분하는
운영 구조다.

## 기본 구조

```text
                         Axis Gate
                    transition request
                             │
                             ▼
Kernel Room ── owns identity / relation / generation / validity
└─ Cell A
   ├─ cell state
   ├─ Node A1
   │  └─ NodeBits
   └─ Node A2
      └─ NodeBits
└─ Cell B
   └─ Node B1
      └─ NodeBits

Orbit = 위 상태의 risk / latency / authority / locality를 읽어 만든 파생 뷰
```

## 핵심 용어

### Kernel Room

Cell, Node, NodeBit의 namespace와 관계, schema, generation, validity를 관리하는 root다.
기존 subsystem의 private mutable state를 모두 가져오는 중앙 god object는 아니다.

현재 코드의 `kernel_room_snapshot_read()`는 health, Memory Fabric, driver, SLM,
ring, user-mode, NodeBit의 aggregate count를 모으는 read-only substrate다. 별도 K1
`kernel_room_management_snapshot_t`는 1024B 안에 bootstrap Cell/Node/NodeBit 1/1/2를
명시적 parent와 generation으로 보존한다. 별도 native K2-a snapshot은 Node 101을
producer-owned SLM MAIN source에 boot-local로 결속한다. 전체 관리 topology와 live
lifecycle/reconciliation은 여전히 `PARTIAL`이다.

### Cell

상태와 자원 책임을 묶는 최소 관리 범위다. lifecycle, health, budget/usage source,
pressure source, risk, generation과 bound Node 집합을 가진다.

Memory Fabric domain은 Cell source 후보다. 그러나 domain 하나를 자동으로 Cell이라
부르지 않으며, process/ring/service도 명시적인 binding을 거쳐야 한다.

현재 상태: K1 bootstrap Cell record와 그 Node 101의 bounded native SLM source binding은
구현됐다. hosted source와 live lifecycle/reconciliation은 `PLANNED`; 나머지 subsystem
seam은 `SCAFFOLD`다.

### Node

Cell 안에서 주소를 가지는 실행 또는 자원 단위다. agent, planner, worker, device
service, pipeline owner가 후보지만, managed Node가 되려면 typed namespace와 하나의
active Cell binding을 가져야 한다.

현재 agent tree, runtime NodeBit node, SLM NodeBit node, pipeline owner, process PID는
서로 다른 namespace다. K1 bootstrap management Node의 exact Cell binding과 Node 101의
typed SLM MAIN source binding은 구현됐다. 숫자 일치가 아니라 명시적 변환을 사용하며,
그 밖의 subsystem/hosted source binding은 `PLANNED`다.

### NodeBit

Node에 속한 가장 작은 typed 관리 상태다. state, capability, eligibility, risk,
validity class를 구분하며 각 bit의 source와 generation을 알아야 한다.

runtime NodeBit과 SLM NodeBit은 현재 독립된 `CURRENT` subsystem이다. 두 체계를 같은
ID 공간이나 단일 정책 원본으로 보지 않으며, management NodeBit adapter는 `PLANNED`다.

### Axis Gate

Cell/Node 상태를 변경하려는 요청이 통과하는 전이·보안 경계다. 현재 9개 static
descriptor가 syscall range와 risk를 분류하는 메타데이터로 존재하며 이 좁은 범위는
`CURRENT`다.

dispatcher의 per-call enforcement와 principal authorize는 `PLANNED`다. Axis Gate는
관리 모델을 소비하는 후속 단계이지 Cell/Node identity를 정의하는 선행 단계가 아니다.

### Orbit

Node와 Cell의 authority, risk, latency, locality를 거리처럼 표현하는 파생 관점이다.
명시적 runtime과 verifier가 없으므로 `RESEARCH`다. 기본 registry의 필수 저장 필드나
scheduler의 동의어로 사용하지 않는다.

## 기존 커널 기반의 위치

현재까지 성숙해진 boot, paging, trapframe, ring3 bootstrap process, hardening, driver
probe와 testkit은 Kernel Room의 경쟁 목표가 아니라 substrate다. 이 기반은 향후
Cell/Node state source와 안전한 userspace consumer를 제공한다.

하지만 아래 등식은 성립하지 않는다.

```text
process switching maturity == Kernel Room management maturity
Axis Gate descriptor count == Cell/Node hierarchy proof
aggregate node count         == Node-to-Cell binding proof
```

## 현재 구현 대응표

| 관리 개념 | 현재 코드의 가까운 기반 | 관리 모델 판정 |
|---|---|---|
| Room aggregate view | `kernel/core/kernel_room.c` | `CURRENT` bounded snapshot |
| Cell state source | `kernel/mm/memory_fabric.c`, pressure/resource snapshots | `SCAFFOLD` adapter |
| execution Node source | SLM agent tree, process/pipeline ownership | `SCAFFOLD` adapter |
| NodeBit source | runtime NodeBit, SLM NodeBit catalog | `SCAFFOLD` adapter |
| Axis classification | Kernel Room gate descriptor table | `CURRENT` metadata |
| K1 bootstrap hierarchy | `kernel_room_management.c`의 Cell 1/Node 1/NodeBit 2 | `CURRENT` (2026-08-11 strict QEMU/shell 검증) |
| native K2-a source binding | `kernel_room_source_binding.c`의 Node 101 ↔ SLM MAIN | `CURRENT` bounded boot-local oracle |
| hosted/live source reconciliation | 없음 | `PLANNED` |
| legacy NodeBit projection | 없음 | `PLANNED` |
| Orbit runtime | 없음 | `RESEARCH` |

## K1 적용 형태

첫 적용은 `management_only read-only hierarchy registry v0`로 구현됐다.

Cell-only table은 완료가 아니다. Cell ID 1, 그 Cell에 exact-one binding된 Node ID 101,
그 Node를 부모로 가리키는 typed NodeBit ID 1001/1002가 동일한 1024B hierarchy
snapshot에 함께 있다.

1. bounded Cell record와 typed `cell_id`
2. bounded Node record와 explicit `cell_id` binding
3. typed NodeBit view와 source/validity/generation
4. read-only Room snapshot
5. duplicate, orphan, stale, unknown, overflow negative proof

이 단계에서는 scheduler, allocator, quota, capability, policy, device를 변경하지 않는다.
Axis Gate enforcement도 붙이지 않는다.

## 명칭 사용 기준

- 상위 관리 구조: `Kernel Room Management Model`
- 구조를 설명하는 뷰: `Kernel Room Topology`
- 계층: `Room -> Cell -> Node -> NodeBit`
- 후속 전이 경계: `Axis Gate`
- 선택적 연구 관점: `Orbit`

기존 `Orbit-Cell Node Model`이라는 표현은 2026-04 탐색 과정의 이름으로 보존할 수
있지만, 현재 정본의 실행 모델명으로 사용하지 않는다. Orbit가 Cell보다 선행하는
것처럼 읽히고 NodeBit 관리 단위가 빠지기 때문이다.

## 결론

Kernel Room의 중심은 gate 수나 snapshot field 수가 아니다. Cell 상태를 기준으로
Node를 결속하고, Node를 NodeBit으로 세분화해 유효한 관리 뷰를 제공하는 것이 중심이다.
현재 커널 substrate, K1 bootstrap hierarchy, native K2-a oracle은 보존하되 다음 작업은
OS-neutral H1 trace/replay로 semantic contract를 옮긴 뒤 K2 lifecycle/reconciliation을
확대하는 것이다.
