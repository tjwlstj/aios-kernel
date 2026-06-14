# AI 친화 리소스 관리 개발 계획

작성일: 2026-04-27

## 목적

이 문서는 AIOS가 부팅 가능한 커널 기준선을 유지하면서,
AI workload와 agent runtime에 맞는 리소스 관리를 어떤 순서로 확장할지 정리한다.

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
- `runtime/ai_syscall.c`의 AI syscall dispatcher와 bootstrap snapshot surface
- `kernel/health.c`, `kernel/kernel_room.c`의 health / room snapshot
- `runtime/nodebit.c`의 capability policy gate
- `runtime/slm_orchestrator.c`의 boot-time SLM/hardware snapshot

아직 없는 것:

- 실제 ring3 handoff와 userspace task 실행
- static ELF loader와 `aios-init`
- per-process address space, user page mapping, page fault recovery
- CPU context switcher
- AI resource ledger / quota / budget accounting
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

상태: planned.

목표:

- 리소스 사용량을 먼저 읽기 전용으로 모은다.
- 어떤 AI node, model, task가 어떤 자원을 쓰는지 future-proof metadata를 둔다.

최소 패치:

- `include/runtime/ai_resource.h`
- `runtime/ai_resource.c`
- 고정 크기 ledger table
- resource kind enum
- owner fields: `node_id`, `task_id`, `model_id`, `ring_id`
- counters: `limit`, `used`, `high_water`, `denied`, `last_update_ns`
- `kernel_room_snapshot_t` 또는 별도 snapshot에 summary count 추가

주의:

- reserve/apply 기능은 넣지 않는다.
- 실제 allocator policy를 바꾸지 않는다.
- boot log에는 read-only marker만 추가한다.

### Slice 2. Read-only resource snapshot UAPI

상태: planned.

목표:

- userspace와 testkit이 AI resource 상태를 읽을 수 있는 안정 표면을 만든다.

최소 패치:

- `SYS_RESOURCE_SNAPSHOT` 또는 `SYS_INFO_RESOURCE`
- staging copy 후 `copy_to_user`
- testkit boot summary parser에 optional `resource` section 추가

완료 기준:

- snapshot syscall null output은 `AIOS_ERR_INVAL`
- 정상 path는 ledger summary를 반환
- QEMU smoke에서 `[RESOURCE] ledger ready` marker 확인

### Slice 3. Bounded policy schema 고정

상태: planned.

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
- risky action은 NodeBit와 health gate가 모두 허용해야 한다.
- 자연어 plan은 hot path에 들어오지 않는다.
- 모든 numeric delta는 clamp 가능해야 한다.

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

- `python .\testkit\aios-testkit.py kernel --target all --strict`
- `python .\testkit\aios-testkit.py kernel --target test --strict --export-boot-summary --timeout 60`

부트 프로필 변경:

- `python .\testkit\aios-testkit.py boot-matrix --profiles full minimal storage-only --strict --timeout 60`

OS/tooling 변경:

- `python .\testkit\aios-testkit.py os`

필수 negative test:

- null output pointer
- unknown resource target
- unsupported action
- over-limit reserve
- risky action under degraded health

## 당장 다음 패치 후보

가장 작은 구현 후보는 다음이다.

1. `include/runtime/ai_resource.h` 추가
2. `runtime/ai_resource.c` read-only ledger 추가
3. boot init에서 `[RESOURCE] ledger ready kinds=...` marker 출력
4. `kernel_room_snapshot_t`에 resource ledger count만 추가
5. testkit parser에 optional resource marker 추가

이 후보는 allocator 정책을 바꾸지 않으면서도,
AI 친화 리소스 관리의 첫 관측 표면을 만든다.

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
