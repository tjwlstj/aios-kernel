# AIOS Kernel Room 관리 모델

문서 상태: 정본
전체 토폴로지 성숙도: `PARTIAL` (aggregate substrate, K1 hierarchy, bounded native K2-a oracle `CURRENT`; K2 lifecycle/reconcile `PARTIAL`)
작성일: 2026-08-10
최종 갱신: 2026-09-03 (H1 원격 acceptance 완료와 남은 live 경계)
적용 범위: `docs/kernel-room/`의 용어, 성숙도, 구현 순서

## 문서 권위

이 문서는 AIOS `Kernel Room`의 정체성과 다음 구현 순서를 정하는 정본이다.
같은 디렉터리의 문서가 이 문서와 충돌하면 이 문서를 우선한다.

현재 코드의 read-only room snapshot과 Axis Gate descriptor는 보존해야 할
기반이지만, 그 둘만으로 Kernel Room의 관리 모델이 구현됐다고 보지 않는다.

## 한 줄 정의

`Kernel Room`은 `Room -> Cell -> Node -> NodeBit` 계층의 식별자, 관계, 상태,
유효성, 세대를 일관된 관리 뷰로 묶는 AIOS의 운영 핵이다.

```text
Kernel Room
└─ Cell
   ├─ lifecycle / health / budget / pressure / generation
   └─ Node
      ├─ execution or resource identity
      └─ NodeBit
         └─ fine-grained state / capability / eligibility / validity

Axis Gate : 위 상태를 바꾸려는 요청의 전이·보안 경계
Orbit     : 위 상태로부터 계산할 수 있는 배치·거리 관점
```

여기서 `관리`는 곧바로 스케줄링, 할당, 권한 변경을 수행한다는 뜻이 아니다.
첫 단계의 관리는 안정된 ID와 관계를 등록하고, 읽기 전용 상태를 같은 세대의
계층으로 조회할 수 있게 한다는 뜻이다.

## 관리 권위와 원본 상태

Kernel Room이 정본으로 소유해야 하는 것은 다음과 같다.

- Cell, Node, NodeBit의 타입이 구분된 ID namespace
- Room-to-Cell, Cell-to-Node, Node-to-NodeBit 관계
- record의 schema, size, generation, validity 규칙
- 중복, orphan, stale generation을 거부하는 관리 불변식
- 각 상태가 어느 subsystem에서 왔는지 나타내는 source binding

Kernel Room이 곧바로 소유하지 않는 것은 다음과 같다.

- Memory Fabric, scheduler, driver, SLM 내부의 mutable 배열과 lock
- allocator나 scheduler의 실제 적용 정책
- 자연어 plan 해석
- 디바이스 직접 제어

원본 상태는 기존 subsystem이 계속 소유한다. Kernel Room은 public read API나
명시적인 adapter를 통해 그 상태를 관리 record로 투영한다. 내부 배열을 몰래
공유하거나 숫자 ID가 같다는 이유로 서로 다른 namespace를 같은 Node로 보지 않는다.

Linux-hosted userspace service는 의도된 기본 delivery 방향이지만 Kernel Room
계층에서는 external source adapter다. Linux PID/cgroup/pidfd/PSI/namespace도 같은
external source 규칙을 따른다. H0 upstream resource manifest와 guard의 `CURRENT`는
K2 binding이나 hosted runtime 성숙도를 승격시키지 않는다. Linux 기준선과
`source_only`·`code_import=0` 경계는
[Linux-hosted substrate 정책](../os/linux_hosted_substrate_and_resource_policy_ko.md)을
따른다.

## 핵심 단위

### Room

전체 관리 계층의 root다. Room은 schema와 generation, Cell 집합, 전체 유효성 및
집계 상태를 제공한다. 현재 `kernel_room_snapshot_t`는 여러 subsystem의 수를 모으는
aggregate snapshot이며, 이 관리 계층 전체와 동일하지 않다.

### Cell

상태와 자원 책임을 묶는 최소 관리 범위다. Cell은 단순 task나 memory domain의
별칭이 아니다. 첫 관리 record는 최소한 아래 의미를 구분할 수 있어야 한다.

- stable `cell_id`와 cell kind
- lifecycle / health / risk state와 각각의 validity
- resource budget / usage source reference
- pressure source reference
- generation과 last-observed time
- bound Node 수와 capacity

Memory Fabric domain, process, ring, service는 Cell의 source 또는 binding 대상이 될 수
있지만, 그중 하나가 자동으로 Cell 그 자체가 되지는 않는다.

### Node

Cell 안에서 주소를 가질 수 있는 실행 또는 자원 단위다. agent, planner, worker,
device service, pipeline owner 같은 기존 개념은 Node source 후보다.

관리 대상으로 승격된 Node는 정확히 하나의 active Cell에 결속되어야 한다. 아직
결속할 수 없는 기존 항목은 `UNBOUND` source로 관측할 수 있지만, bound Node 수에
포함하거나 배치·적용 대상으로 사용하지 않는다.

`agent_tree_node_t`, runtime `nodebit_entry_t`, `slm_nodebit_t`, pipeline owner와 process
PID는 현재 서로 다른 namespace다. 명시적인 binding 없이 숫자 값만 비교하지 않는다.

### NodeBit

Node에 속한 가장 작은 관리 상태 단위다. NodeBit은 하나의 의미가 섞인 임의 bitmap이
아니라, 최소한 아래 class를 구분하는 typed view여야 한다.

- state: present, ready, active, degraded 같은 현재 상태
- capability: 허용 가능한 동작 범위
- eligibility: health, affinity, budget 등 현재 후보 가능성
- risk / mediation: 위험도와 중재 필요성
- validity: 어떤 bit가 실제 source에 의해 뒷받침되는지

pressure score와 gate eligibility는 계속 별도 축으로 둔다. 현재 runtime NodeBit과 SLM
NodeBit은 각각 유효한 subsystem 표면이지만, Kernel Room 관리 NodeBit의 동일 원본으로
간주하지 않는다. 후속 binding과 generation 계약을 통해 adapter로 연결한다.

### Axis Gate

Axis Gate는 Kernel Room의 정체성이 아니라 후속 전이·보안 경계다.

현재 9개 descriptor의 syscall range와 risk 정보는 `CURRENT`인 분류 메타데이터다.
dispatcher가 이를 호출마다 강제하지 않으므로 per-syscall enforcement는 `PLANNED`다.
후속 Axis Gate는 Cell 상태와 NodeBit을 소비할 수 있지만, Cell/Node identity를 대신
정의해서는 안 된다.

따라서 다음 순서를 금지한다.

```text
gate enforcement -> 나중에 principal / Cell / Node 의미를 끼워 맞춤
```

먼저 관리 대상과 관계를 고정하고, 그 뒤에 누가 어떤 상태 전이를 요청할 수 있는지
정의한다.

### Orbit

Orbit는 권한, 위험, 지연, 자원 거리로부터 계산되는 배치 관점이다. 명시적인 runtime,
ABI, verifier가 없으므로 현재 상태는 `RESEARCH`다. Cell/Node 모델의 필수 저장 단위로
두거나 scheduler의 다른 이름으로 사용하지 않는다.

## 현재 구현과 성숙도

| 대상 | 상태 | 정확한 경계 |
|---|---|---|
| `kernel_room_snapshot_read()`와 `[ROOM] snapshot`/`[ROOM] gates` | `CURRENT` | aggregate read-only 관측과 정적 gate 요약 |
| 9개 Axis Gate descriptor | `CURRENT` | syscall range 분류 메타데이터만 해당 |
| Kernel Room 전체 토폴로지 | `PARTIAL` | aggregate substrate, K1 bootstrap hierarchy, 한 native K2-a 결속은 있으나 live lifecycle/reconcile/attribution은 없음 |
| Memory Fabric domain/window | `CURRENT` subsystem, `SCAFFOLD` adapter | Cell source 후보일 뿐 Cell identity가 아님 |
| SLM agent tree | `CURRENT` subsystem, bounded K2-a source `CURRENT` | exact-one active/persistent MAIN을 producer-owned boot-local instance/generation으로 복사 조회 |
| runtime NodeBit | `CURRENT` subsystem, `SCAFFOLD` adapter | capability table과 per-node 통계, pipeline gate 범위 |
| SLM NodeBit catalog | `CURRENT` subsystem, `SCAFFOLD` adapter | API/tool/device action policy view, 별도 namespace |
| K1 management hierarchy registry v0 | `CURRENT` | schema 1/1024B producer, exact host 계약, strict QEMU/shell 검증 완료 |
| K2-a native `AI_SERVICE` source binding oracle | `CURRENT` | 별도 schema 1/256B snapshot이 Node 101을 SLM MAIN source에 결속; exact boot/summary/`state binding` 검증 |
| K2 전체 source lifecycle / reconciliation | `PARTIAL` | boot-local immutable native 결속만 있으며 refresh, exit/recreate, rebind, hosted source는 없음 |
| H1 OS-neutral lifecycle trace/replay | `CURRENT` | H1-a/b/c contract·12 fixtures·artifact/parity가 동일 run·exact SHA의 원격 Linux/Windows/parity 및 세 artifact 검증을 통과; live producer는 없음 |
| Linux-hosted source adapter | `PLANNED` | 검증된 H1 계약을 소비하며 producer-owned service lifecycle로만 결속할 후속 조각 |
| legacy management NodeBit projection | `PLANNED` | runtime/SLM source adapter와 generation binding 없음 |
| per-Cell/per-Node pressure와 resource ownership | `PLANNED` | pressure는 system-to-plane, resource owner는 unattributed |
| Axis Gate enforcement / authorize / apply | `PLANNED` | management identity와 principal 계약 뒤에만 착수 |
| Orbit runtime, 분산 Cell/Node mesh | `RESEARCH` | 선택적 미래 연구이며 현재 구현 약속이 아님 |

`CURRENT subsystem, SCAFFOLD adapter`는 그 subsystem의 구현을 낮춰 말하는 표현이
아니다. 그 기능은 현재 동작하지만 Kernel Room 계층에 연결하는 adapter 역할은 아직
준비 재료라는 뜻이다.

## 관리 불변식

첫 registry부터 아래를 지킨다.

1. public schema와 numeric ID는 타입별로 분리하고 기존 값은 재사용하지 않는다.
2. 서로 다른 namespace의 ID가 우연히 같아도 같은 Node로 간주하지 않는다.
3. managed Node는 정확히 하나의 active Cell에 결속된다.
4. NodeBit은 존재하는 managed Node와 명시적인 class/validity를 가져야 한다.
5. duplicate ID, orphan binding, unknown source, overflow, stale generation은 성공으로
   축약하지 않는다.
6. snapshot은 `schema_version`, `struct_size`, `generation`, source/validity 정보를
   가져야 하며 확장 필드는 versioning한다.
7. `observation_only=1`과 `management_only=1`인 동안 scheduler, allocator, quota,
   migration, capability, policy를 변경하지 않는다.
8. pressure ranking과 eligibility bitmap은 별도 축으로 유지한다.
9. cross-subsystem snapshot이 원자적이지 않다면 best-effort라고 명시한다. SMP나
   concurrent writer 전에 generation/seqlock 등 실제 일관성 계약을 별도로 증명한다.

## K1 구현 조각

첫 수직 조각은 `management_only read-only hierarchy registry v0`로 구현됐다.

이 조각은 Cell 표만 만든 뒤 나머지 계층을 미래로 미루는 단계가 아니다. v0는
`Cell 1개`, 그 Cell에 결속된 `managed Node 1개`, 그 Node를 부모로 가리키는 typed
`NodeBit 2개`를 한 1024B snapshot 안에 함께 둔다. capacity는 각각 2/4/8이다. 이후 단계는 이
최소 hierarchy를 새로 완성하는 단계가 아니라 실제 subsystem source와 상태를 더 넓게
연결하는 단계다.

포함할 것:

- bounded Room / Cell / Node / NodeBit record capacity
- typed ID와 source kind/source ID
- boot-seeded Cell ID 1, exact-one binding을 가진 managed Node ID 101,
  parent-bound typed NodeBit ID 1001/1002
- schema/size/generation/validity
- read-only snapshot
- duplicate/orphan/unknown/stale/overflow와 non-zero unused tail negative selftest
- boot marker, structured summary, `state room` mirror

포함하지 않을 것:

- per-syscall Axis Gate enforcement
- NodeBit capability 변경
- scheduler migration 또는 budget apply
- allocator reserve/release/throttle
- process principal 또는 distributed Node mesh
- Orbit scheduling

exact 부트 증거는 아래처럼 producer와 Python/PowerShell verifier에 함께 고정한다.

```text
[ROOM] management hierarchy selftest PASS schema=1 struct_size=1024 generation=1 cells=1 nodes=1 bound_nodes=1 nodebits=2 bound_nodebits=2 source_valid=1 generation_valid=1 duplicate_rejected=1 orphan_rejected=1 unknown_rejected=1 stale_rejected=1 overflow_rejected=1 tail_rejected=1 observation_only=1 management_only=1
```

정상 marker만 확인하지 않는다. host verifier는 missing, duplicate, truncation,
unexpected extension, orphan, stale generation, capacity overflow, non-zero unused tail을
fail-closed로 거부해야 한다. 기존 `[ROOM] snapshot`과 `[ROOM] gates`는 호환성을 위해
그대로 유지한다.

## K2-a bounded native semantic oracle

첫 K2 조각은 K1 ABI를 확장하지 않고 별도 계약으로 구현됐다.

- SLM producer는 계산된 agent tree에서 `node_id=1`, role/model `MAIN`,
  active/persistent인 source를 exact-one으로 선택한다.
- source는 64B copied snapshot에서 typed namespace, semantic kind `AI_SERVICE`,
  role, lifecycle, boot-local `source_instance=1`, 전용 `source_generation=1`을
  소유한다. `policy_generation`, timestamp, PID를 재사용하지 않는다.
- Kernel Room은 schema 1/256B, capacity 2의 별도 binding snapshot에서 canonical
  Node 101과 parent Cell 1의 generation, source identity/generation, kind/role
  일치를 고정한다.
- public reject reason ID는 append-only이며 init-order, missing, schema/malformed,
  overflow, duplicate, orphan, namespace/kind/role/instance, zero generation,
  rollback, stale, non-zero tail을 구분한다.
- boot marker와 structured `kernel_room_binding`, `state binding`은 exact-one
  full-row로 검증하며 순서는 `entry-AC < K1 management < K2 binding < aggregate ROOM`이다.

```text
[ROOM] source binding selftest PASS schema=1 struct_size=256 binding_generation=1 bindings=1 capacity=2 canonical_namespace=2 canonical_id=101 canonical_kind=1 canonical_generation=1 parent_cell_id=1 parent_generation=1 source_namespace=1 source_id=1 source_instance=1 source_generation=1 source_kind=1 source_role=1 kind_match=1 role_match=1 producer_owned=1 copied_read=1 missing_rejected=1 duplicate_rejected=1 orphan_rejected=1 namespace_rejected=1 kind_rejected=1 role_rejected=1 instance_rejected=1 zero_generation_rejected=1 generation_rollback_rejected=1 stale_rejected=1 init_order_rejected=1 schema_rejected=1 overflow_rejected=1 tail_rejected=1 source_valid=1 generation_valid=1 binding_valid=1 observation_only=1 management_only=1
```

이 oracle은 한 부팅 안의 immutable 결속만 증명한다. source refresh,
exit/recreate, explicit rebind, lease, cross-reboot uniqueness, Linux service,
resource attribution, authorize/apply는 증명하지 않으므로 K2 전체는 `PARTIAL`이다.

## 후속 순서

1. 관리 정본과 namespace 대응표 고정 — 완료
2. management-only read-only hierarchy registry v0 — `CURRENT` (2026-08-11)
3. bounded native K2-a semantic oracle — `CURRENT` (2026-08-15)
4. H1 OS-neutral lifecycle trace/replay와 stable reason fixture — `CURRENT` (2026-09-03)
5. Linux-hosted observe-only source adapter와 explicit reconcile
6. legacy NodeBit typed projection과 source generation 결속
7. per-Cell/per-Node pressure·resource attribution 관측
8. principal과 상태 전이 계약
9. Axis Gate authorize/enforcement와 rollback proof
10. 선택적 Orbit 연구

2단계의 완료 조건은 앞서 정의한 최소 Cell/Node/NodeBit 전체 hierarchy proof다.
3~5단계는 Cell-only, Node-only, NodeBit-only 구현 순서가 아니라 v0에 이미 존재하는
관계와 typed view를 실제 source에 연결하고 coverage를 확대하는 순서다.

K2 semantic field/reject 의미는 substrate-neutral을 목표로 한다. 현재 native producer의
`CURRENT` 증거는 Node 101 `AI_SERVICE`를 SLM agent-tree MAIN에 묶은 K2-a oracle뿐이며,
OS-neutral lifecycle trace 계약은 H1 verifier/fixture와 exact-SHA 원격 cross-OS acceptance로
고정됐다. 근거는 [H1 원격 acceptance 증거 (§13.2)](../os/h1_binding_trace_replay_workplan_ko.md#132-2026-09-03-원격-acceptance-완료)를
따르며 live producer는 아직 없다. 기본 delivery 경로의 hosted
source 후보는 producer-owned service instance/generation을 가진 실제 Linux userspace
service다.
native oracle은 전용 producer-owned instance/generation과 copied read API를 사용한다.
기존 `policy_generation`, timestamp, Memory Fabric domain ID,
process PID/run generation, Linux PID/pidfd/cgroup/PSI를 편의상 generation이나
canonical identity로 대신 쓰지 않는다. hosted adapter도 K1 1024B immutable
snapshot과 분리된 같은 semantic field/reason 계약을 소비해야 하며
missing, duplicate, role mismatch, orphan, zero/regressed/stale generation,
init-order failure를 거부한다.

커널 실행 기반, storage, userspace 작업은 이 모델을 지탱하는 substrate 축으로 계속
발전할 수 있다. 다만 그 축의 완료 수가 Kernel Room 관리 모델의 성숙도를 대신하지
않는다.

## 비목표

- Kernel Room을 모든 subsystem의 mutable god object로 만드는 것
- gate descriptor를 Cell/Node identity처럼 취급하는 것
- 현재 aggregate count를 hierarchy proof로 해석하는 것
- NodeBit lookup 하나를 전체 정책 강제로 과장하는 것
- 구현 없는 Orbit 용어에 맞춰 scheduler를 재작성하는 것
- 정상 부트 marker만으로 control/apply가 안전하다고 주장하는 것
