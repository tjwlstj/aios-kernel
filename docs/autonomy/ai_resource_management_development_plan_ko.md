# AI 친화 리소스 관리 개발 계획

작성일: 2026-04-27

최종 갱신: 2026-08-11 (K2-first 전역 우선순위와 Linux-hosted 정책 분리)

## 목적

이 문서는 AIOS가 부팅 가능한 커널 기준선을 유지하면서,
AI workload와 agent runtime에 맞는 리소스 관리를 어떤 순서로 확장할지 정리한다.

이 문서의 Slice 순서는 **native resource subsystem 내부 계획**이다. 프로젝트의 다음
직접 마일스톤은 Kernel Room K2 source binding이며, upstream Linux/QEMU/VirtIO
resource 선정과 Linux-hosted H축은
[별도 정본](../os/linux_hosted_substrate_and_resource_policy_ko.md)을 따른다. H0
manifest/guard가 `CURRENT`여도 native resource policy나 hosted backend가 구현된 것은
아니다.

핵심 판단은 다음과 같다.

- 지금 당장 커널 구현 언어를 늘리지 않는다.
- 커널 핵심은 계속 `C + asm`으로 작고 검증 가능하게 유지한다.
- 먼저 필요한 것은 새 범용 언어가 아니라, AI 리소스 요청을 제한된 구조로 표현하는 정책 schema다.
- 실제 Rust, Zig, WASI 같은 언어/런타임 선택은 ring3 handoff 이후 userspace 서비스 단계에서 다룬다.

## 현재 구현 기준

현재 저장소 기준으로 이미 있는 것:

- x86_64 Multiboot2 boot path와 QEMU smoke 가능한 커널
- GDT / IDT / TSS scaffold와 `[USER] Ring3 scaffold ready=1` marker
- `kernel/user_access.c`의 구조적 `access_ok`, `copy_from_user`, `copy_to_user`
- `mm/tensor_mm.c`의 tensor allocation metadata와 64-byte alignment 기준
- `mm/memory_fabric.c`의 agent domain / shared window scaffold
- `mm/heap.c`의 2 MiB static kernel heap
- `sched/ai_sched.c`의 AI task metadata, run queue, PIT tick accounting
- `runtime/ai_pressure.c`의 schema 1 관측 전용 pressure snapshot
- `runtime/ai_resource.c`의 schema 1 관측 전용 aggregate resource ledger
- `SYS_INFO_RESOURCE=0x706`의 versioned read request와 staging-copy 반환 경로
- `state resource`의 aggregate-only 한 줄 관측면
- `runtime/ai_syscall.c`의 AI syscall dispatcher와 bootstrap snapshot surface
- `state autonomy` schema 1의 read-only mode/support/counter/last-decision 관측면
- `kernel/health.c`, `kernel/kernel_room.c`의 health / room snapshot
- `runtime/nodebit.c`의 capability policy gate
- `runtime/slm_orchestrator.c`의 boot-time SLM/hardware snapshot
- 정적 ELF64 loader, 두 private CR3, process별 16 KiB ring0 entry stack,
  PID 1→PID 2의 bounded 순차 ring3 실행/정리 증거

아직 없는 것:

- 장기 실행 `aios-init`과 일반 userspace task 수명주기
- 동적 per-process address space/PMM, page fault recovery
- 두 ring3 process의 timer-preemptive 전환과 trapframe 기반 saved-state 교대(176B frame 계약·from_user 판별 자체는 2026-08-02 `CURRENT`)
- SMP/per-CPU TSS와 물리 CPU 간 task migration
- per-node/task/model/ring resource attribution과 quota / budget accounting
- 리소스 reserve / release / throttle UAPI
- userspace `aios-resourced` 또는 policy broker 구현체
- WASI component runtime 또는 bytecode interpreter

따라서 이 계획은 `구현 완료`가 아니라,
현재 부팅 가능한 커널 위에 올릴 다음 개발 순서를 정의한다.

## 언어 선택 원칙

### 커널

커널은 계속 `C + asm`을 기본으로 둔다.

이유:

- 현재 boot, interrupt, paging, driver code가 모두 이 경계 위에 있다.
- 새로운 커널 언어를 도입하면 toolchain, ABI, panic path, allocator, unwind, build flags를 동시에 검증해야 한다.
- AI 리소스 관리는 먼저 enum, bitmask, fixed-size table, snapshot ABI로 충분히 시작할 수 있다.

### 정책 schema

AI가 직접 자연어로 리소스 변경을 지시하지 않게 한다.
대신 다음처럼 제한된 구조를 사용한다.

```text
target: tensor_pool | memory_fabric | infer_ring | scheduler_slice | device_io | kv_cache
action: observe | reserve | release | throttle | promote | demote | revoke
delta: bounded numeric value
risk_level: observe | bounded | risky
reason: fixed-size text or code
support_state: unsupported | observe_only | staged | apply_ready
```

초기 구현은 C 구조체와 enum으로 시작하고,
userspace가 올라온 뒤 JSON, WIT, 또는 compact binary format으로 확장할 수 있다.

### Userspace

ring3 이후에는 언어 선택지를 둘 수 있다.

- `C`: 첫 `aios-init`, syscall smoke, 최소 runtime에 가장 단순하다.
- `Zig`: freestanding target과 C ABI 연동이 쉬워 bootstrap binary 실험에 적합하다.
- `Rust`: `aios-resourced`, `aios-osd`, policy broker처럼 상태가 많고 안전성이 중요한 userspace 서비스에 적합하다.
- `WASI/WIT`: verifier, summarizer, tool adapter 같은 sandbox component 단계에 적합하다.

결론적으로, **새 언어는 커널 내부가 아니라 userspace service plane에서 점진 도입**한다.

## 리소스 모델

AIOS의 첫 AI resource model은 아래 단위로 시작한다.

| Resource | 현재 기반 | 첫 관리 목표 |
|---|---|---|
| Kernel heap | `mm/heap.c` | boot/runtime metadata 사용량 관측 |
| Tensor pool | `mm/tensor_mm.c` | model/infer/training allocation 사용량 관측 |
| Memory fabric | `mm/memory_fabric.c` | agent domain/window별 사용량 관측 |
| Inference ring | `include/runtime/ai_ring.h`, `runtime/ai_syscall.c` | SQ/CQ entry budget과 notify count 관측 |
| Scheduler slice | `sched/ai_sched.c` | workload policy별 time slice / queue pressure 관측 |
| Device I/O | driver bootstrap, SLM plans | risky I/O budget은 NodeBit와 health gate로 제한 |
| KV cache | planned userspace runtime | 커널은 backing/window primitive만 제공하고 policy는 userspace로 이동 |

## AI Pressure Tracker v0

상태: `CURRENT` (2026-07-26), 관측 전용.

이번 조각은 실제 배치 정책보다 먼저 “어디에 일이 몰리는가”를 빠르게 읽는
고정 크기 압력 표면을 추가한다.

현재 두 단계 계층은 다음과 같다.

```text
system
  ├─ sched   : AI workload queue occupancy
  ├─ memory  : Memory Fabric window/reader/writer overlap
  └─ policy  : cumulative NodeBit denial ratio
```

- public plane ID는 `sched=0`, `memory=1`, `policy=2`로 명시하고 append-only로 유지한다.
- snapshot은 `schema_version`, `struct_size`, `source_flags`를 가져 확장 시
  기존 독자가 구조 크기와 source 의미를 구분할 수 있다.
- 점수는 부동소수점/SIMD가 아닌 `0..1024` 정수 고정소수점 비율이다.
- `max_levels=4`, `active_levels=2`다. 즉 system→plane만 `CURRENT`이고,
  plane→domain→task/window/ring은 구조적 확장 용량일 뿐 아직 구현이 아니다.
- system hotspot은 세 plane 점수의 최댓값이고, `concentration_q10`은
  `max / sum`이다. 모든 점수가 0이면 hotspot은 `none`, valid는 0이다.
- NodeBit 수치는 누적 counter에서 계산하므로 `source_flags`가 이를 명시한다.
  fast/slow EWMA나 시간 창 pressure로 과장하지 않는다.

Memory Fabric 중첩 증거는 정확한 fixed array를 한 번 순회해 계산한다.

- participant fanout: `reader_mask | writer_mask` popcount
- writer conflict: writer 쌍의 수
- read/write overlap: writer와 writer가 아닌 reader 사이의 쌍
- weighted shared bytes: shared window bytes × participant fanout

`map_count`는 attach에 대응하는 detach API가 없어 누적 logical-map 수다.
따라서 순간 동시성이나 현재 과밀도 입력으로 사용하지 않는다.

게이트 bitmap과 압력 점수도 합치지 않는다. pressure가 후보를 관측/순위화한
뒤 eligibility는 `online & affinity & policy_gate & health & budget`의 별도
교집합으로 계산한다. v0에는 이 결과를 소비하는 scheduler migration/apply
edge가 없다.

관측/검증 표면:

- required boot proof:
  `[PRESSURE] tracker selftest PASS schema=1 ... observation_only=1`
- structured boot summary: `pressure.ready`, schema/계층/selftest 필드
- runtime shell: `state pressure`
- host negative: marker 누락, `observation_only=0`, `gate_mask=0`,
  불완전 pressure record는 PASS하지 않는다.

다음 확장은 실제 SMP보다 먼저 다음 순서로 제한한다.

1. fast/slow integer EWMA와 sample generation
2. scheduler wait/stall, Memory Fabric domain/window별 exact child cell
3. I/O/ring source가 실제 counter를 제공할 때 plane ID append
4. full trapframe + per-CPU 기반 뒤에만 migration proposal 연결
5. proposal은 NodeBit/health/budget gate와 rollback verifier를 통과한 뒤에만 apply

## AI Resource Ledger v0

상태: `CURRENT` (2026-08-02), aggregate 관측 전용 + read-only UAPI/shell.

`ai_resource_snapshot_t`는 capacity 8의 고정 snapshot에 현재 구현된 다섯
resource kind를 한 row씩 기록한다. public kind와 unit ID는 append-only다.

| kind | unit | 현재 `used` 의미 | 현재 `limit` 의미 |
|---|---|---|---|
| `kernel-heap` | bytes | heap allocated bytes | heap capacity |
| `tensor-pool` | bytes | tensor managed allocation bytes | tensor managed capacity |
| `memory-fabric-windows` | count | active window count | fixed window slots |
| `inference-ring-registrations` | count | registered ring count | fixed registration slots |
| `scheduler-runnable` | count | queued + running AI workload count | fixed AI task slots |

- `limit`/`used` validity는 5종 모두 켜져 있다.
- source-native high-water는 tensor `peak_usage` 한 종류만 유효하다. read 호출
  빈도에 따라 달라지는 가짜 high-water를 만들지 않는다.
- 공통 per-resource denial counter가 아직 없으므로 `denied` validity는 0이다.
- `node_id`, `task_id`, `model_id`, `ring_id`는 future attribution 자리다.
  각 owner에는 별도 validity bit가 있으므로 ID 0도 future valid ID가 될 수 있다.
  현재 row는 `OWNER_UNATTRIBUTED=1`, owner-valid bit 0인 aggregate이며 owner
  값의 0은 validity가 없을 때만 placeholder다.
- heap/fabric/ring reader는 내부 동기화되고 scheduler는 local IRQ를 막고
  복사한다. tensor stats와 전체 cross-source 조합은 현재 single-BSP에서의
  best-effort snapshot이며 원자적 multi-source transaction이 아니다.
- 별도 versioned snapshot을 사용해 `kernel_room_snapshot_t`와 bootstrap ABI는
  바꾸지 않았다. info 범위 끝에 `SYS_INFO_RESOURCE=0x706`만 append-only로
  추가하고 Kernel Room info gate 끝도 같은 번호로 확장했다.
- `ai_resource_snapshot_request_t`는 16바이트 고정 request이며 schema 1,
  정확한 `output_size`, non-null `output_addr`만 허용한다. kernel staging
  snapshot이 내부 계약을 다시 통과한 뒤에만 `copy_to_user`한다.
- `state resource`와 shell same-record 계약은 CURRENT다. quota/reserve/release/
  throttle 및 allocator/scheduler policy 변경은 아직 없다.

필수 부팅 증거는 다음 exact record다.

```text
[RESOURCE] ledger selftest PASS schema=1 kinds=5 units=2 entries=5 capacity=8 source_flags=31 limit_kinds=5 used_kinds=5 high_water_kinds=1 denied_kinds=0 owners_unattributed=1 observation_only=1
```

Python/PowerShell 정상 verdict와 직접 Make smoke가 행 전체를 요구한다. marker
누락, 축약, 선행 공백, 중복 exact record, `observation_only=0`, 뒤에
`apply_enabled=1`을 붙인 상충 레코드는 PASS하지 않는다. boot summary의
`resource.ready`도 같은 전체 필드를 검사한다.

## 개발 순서

### Slice 0. 부팅 기준선 유지

상태: 구현됨.

목표:

- resource management 변경이 기존 boot smoke를 흔들지 않게 한다.

완료 기준:

- `kernel --target test --strict --export-boot-summary`
- `boot-matrix --profiles full minimal storage-only --strict`
- 기존 `[ROOM]`, `[HEALTH]`, `[SHELL]`, `[NODEBIT]` marker 유지

### Slice 1. AI Resource Ledger 관측 전용 도입

상태: `CURRENT` (2026-08-02), aggregate-only.

목표:

- 리소스 사용량을 먼저 읽기 전용으로 모은다.
- 향후 AI node/model/task attribution을 추가할 자리를 두되, 현재 값은
  `NONE/UNATTRIBUTED`로 명시한다.

최소 패치:

- `include/runtime/ai_resource.h`
- `runtime/ai_resource.c`
- 고정 크기 ledger table
- resource kind enum
- owner fields: `node_id`, `task_id`, `model_id`, `ring_id`
- counters와 validity: `limit`, `used`, `high_water`, `denied`, `last_observed_ns`
- 별도 `ai_resource_snapshot_t`에 entry count와 fixed table 포함
- exact boot selftest, structured boot summary, host negative test
- synthetic source mapping, owner-validity 충돌, high-water 상한, unused tail
  zero 계약을 포함한 fail-closed kernel selftest

주의:

- reserve/apply 기능은 넣지 않는다.
- 실제 allocator policy를 바꾸지 않는다.
- source-native 값이 없는 high-water/denied를 0만 보고 유효하다고 하지 않는다.
- `kernel_room_snapshot_t`와 bootstrap ABI는 바꾸지 않는다.

### Slice 2. Read-only resource snapshot UAPI

상태: `CURRENT` (2026-08-02), read-only.

목표:

- userspace와 testkit이 AI resource 상태를 읽을 수 있는 안정 표면을 만든다.

최소 패치:

- append-only `SYS_INFO_RESOURCE=0x706`
- 16바이트 `ai_resource_snapshot_request_t`의 schema/size/output 검증
- kernel staging snapshot 검증 후 `copy_to_user`
- Kernel Room info gate의 `syscall_end=SYS_INFO_RESOURCE`
- `state resource` 한 줄 관측면과 shell lane 교환

완료 기준:

- null request/output은 `AIOS_ERR_INVAL`
- 정상 dispatcher path는 schema 1, entry 5, `observation_only=1` ledger를 반환
- unknown schema와 undersized/oversized output을 명시적으로 거부
- 기존 exact `[RESOURCE] ledger selftest PASS ...`와 `observation_only=1` 유지
- `state resource`는 owner row 0, unattributed row 5와 source별 used/limit를
  같은 한 줄에서 검증

현재 성공 경로 proof는 모든 subsystem 초기화 뒤 real dispatcher를 호출하는
kernel-internal boot selftest다. embedded ring3 demo program은 아직 0x706 request를
직접 만들지 않으므로 실제 page-backed userspace caller proof로 과장하지 않는다.

### Slice 3. Bounded policy schema 고정

상태: `PLANNED` / `SUPPORTING`. 프로젝트 전체의 다음 직접 우선순위가 아니다.

목표:

- AI/SLM이 제안할 수 있는 리소스 action space를 enum으로 제한한다.

최소 패치:

- `ai_resource_target_t`
- `ai_resource_action_t`
- `ai_resource_risk_t`
- `ai_resource_policy_request_t`
- `ai_resource_policy_result_t`

필수 규칙:

- unsupported target은 명시적으로 거부한다.
- NodeBit와 health는 risky action 검증의 입력일 뿐 authorize 결과가 아니다. K5
  principal/ownership/target generation과 stale-token 거부 전에는 risky action을
  지원하지 않는다.
- 자연어 plan은 hot path에 들어오지 않는다.
- 모든 numeric delta는 clamp 가능해야 한다.
- 모든 capability는 기본 `UNSUPPORTED`이며 이 slice에서 apply handler를 연결하지 않는다.

### Slice 4. Reserve / release / throttle 적용

상태: planned.

목표:

- observe-only ledger에서 제한된 resource control로 확장한다.

최소 패치:

- `SYS_RESOURCE_RESERVE`
- `SYS_RESOURCE_RELEASE`
- `SYS_RESOURCE_THROTTLE`
- 실패 시 counter 증가
- rollback 가능한 action만 apply

초기 적용 대상:

1. inference ring queue depth
2. scheduler time slice hint
3. memory fabric window budget

아직 적용하지 않을 대상:

- raw MMIO
- direct clock control
- full training loop
- persistent memory mutation

### Slice 5. Userspace `aios-resourced`

상태: planned, ring3 이후.

목표:

- 커널은 mechanism만 제공하고 resource policy는 userspace daemon으로 옮긴다.

역할:

- `SYS_INFO_BOOTSTRAP`, `SYS_SLM_HW_SNAPSHOT`, resource snapshot 읽기
- 모델 / agent / task별 budget 계산
- NodeBit와 health 상태를 같이 보고 reserve 요청
- 실패한 후보나 과도한 사용자를 demote

권장 언어:

- 첫 smoke는 C 또는 Zig
- 장기 daemon은 Rust 후보

완료 기준:

- `aios-resourced`가 부팅 후 resource snapshot을 읽고 serial log를 남김
- 커널 panic 없이 invalid request를 거부
- degraded health에서는 risky resource action이 apply되지 않음

### Slice 6. Component sandbox lane

상태: planned.

목표:

- verifier, summarizer, tool adapter 같은 하위 agent worker를 WASI/WIT component로 격리한다.

전제:

- native userspace lane이 먼저 안정화되어야 한다.
- resource broker가 component별 budget을 부여할 수 있어야 한다.

## 검증 경로

문서 변경:

- `git diff --check`

커널 구조 변경:

- `python .\tools\testkit\aios-testkit.py kernel --target all --strict`
- `python .\tools\testkit\aios-testkit.py kernel --target test --strict --export-boot-summary --timeout 60`

부트 프로필 변경:

- `python .\tools\testkit\aios-testkit.py boot-matrix --profiles full minimal storage-only --strict --timeout 60`

OS/tooling 변경:

- `python .\tools\testkit\aios-testkit.py os`

Slice 1/2 필수 negative test:

- null internal snapshot output
- oversized entry count internal contract
- missing/truncated resource marker
- `observation_only=0`
- canonical marker 뒤 `apply_enabled=1` 상충 필드
- 중복 exact resource/pressure record와 선행 공백 증거
- null request/output, unknown schema, 크기가 다른 output request
- `state resource`의 owner/attribution/validity 상충 레코드

후속 policy negative test:

- unknown resource target
- unsupported action
- over-limit reserve
- risky action under degraded health

## 리소스 subsystem 내부 후속 후보 — 전역 우선순위 아님

Slice 2까지 완료한 리소스 레인 안의 가장 작은 후속 후보는 Slice 3 bounded policy
schema 고정이다. 다만 전역 다음 직접 마일스톤은 K2-a native source binding이다.
Slice 3은 K2 identity/generation 계약을 소비하는 design-only `SUPPORTING` 작업으로만
병행할 수 있으며 K5 전에는 apply 가능성을 열지 않는다.

1. target/action/risk ID를 append-only enum으로 먼저 정의
2. request/result에 schema와 struct size를 포함
3. 모든 action은 기본 unsupported로 두고 handler/apply는 아직 연결하지 않음
4. unknown target/action과 크기 불일치 host/kernel 반례를 먼저 추가
5. owner attribution source가 생기기 전까지 aggregate row를 분할하지 않음

이 후보는 정책 언어의 모양만 고정하는 scaffold다. allocator/scheduler 적용,
quota accounting, reserve/release syscall은 별도 검증 조각 전까지 PLANNED다.

단, pressure를 실제 scheduler apply에 연결하기 전에는
`calc_weight()`와 `delta_ns / weight`의 priority 의미를 별도 selftest로 먼저
고정해야 한다. 현재 v0은 이 경로를 읽기만 하므로 기존 vruntime 동작을
변경하지 않는다.

## 하지 말아야 할 것

- 커널에 Rust/Zig/WASI runtime을 바로 넣기
- AI 모델이 직접 포인터, 레지스터, MMIO 주소를 생성하게 하기
- resource action을 자연어 문자열로 dispatch하기
- health gate 없이 risky I/O나 driver reset budget을 열기
- userspace handoff 전에 service plane을 구현 완료처럼 문서화하기

## 결론

AIOS의 AI 친화 리소스 관리는 새 범용 언어 도입보다 먼저,
작고 검증 가능한 resource ledger와 enum-backed policy schema로 시작해야 한다.

커널은 계속 부팅 가능한 기반을 지키고,
userspace가 열린 뒤 `aios-resourced`와 component sandbox가 정책을 확장하는 흐름이 가장 안전하다.
