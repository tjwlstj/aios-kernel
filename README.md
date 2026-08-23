# AIOS - AI-Native Operating System

<p align="center">
  <strong>Room → Cell → Node → NodeBit 관리 모델을 위한 AI-native 관리·런타임 프로젝트</strong>
</p>

---

## Overview

AIOS(AI-Native Operating System)는 AI 워크로드를 **1급 시민(First-class citizen)**으로
취급하고 **Kernel Room → Cell → Node → NodeBit** 관리 모델을 중심에 둔 AI-native
관리·런타임 프로젝트입니다. 의도된 기본 delivery substrate는 Linux-hosted userspace
service입니다. 현재 저장소의 runtime 실행 증거는 x86_64 native reference/proof
kernel에 있고, Linux 쪽은 H0 upstream source policy만 `CURRENT`입니다. 현재 베타에는 부팅 가능한 native kernel,
텐서 지향 메모리 메타데이터, 메모리 패브릭, 헬스/SLM 스냅샷, 서로 독립적인 SLM
policy catalog와 runtime capability NodeBit, 관측 전용 AI pressure tracker와 resource
ledger, 제한된 AI 시스콜 표면이 있습니다. `CURRENT`인 K1은 1KiB 고정 snapshot에
bootstrap Cell 1개, 그 Cell에 명시적으로 bound된 Node 1개, 그 Node를 부모로 하는
typed NodeBit 2개를 함께 보존하는 management-only hierarchy v0입니다. 기존 subsystem을
이 계층으로 투영하는 첫 bounded native K2-a oracle도 `CURRENT`입니다. 별도 256B
snapshot이 Node 101을 producer-owned SLM MAIN source에 boot-local immutable하게
결속합니다. K2 전체 lifecycle/reconciliation은 `PARTIAL`, H1 replay와 Linux-hosted
backend는 `PLANNED`입니다.

장기 방향은 embodied AI OS입니다. LLM/SLM 에이전트는 유저스페이스에서 단기 기억과 장기 기억을 분리해 유지하고, 세션을 넘어 연속성을 보존하며, 하드웨어에는 커널이 중재하는 정책 경계를 통해 접근합니다.

이 저장소는 아직 범용 상용 OS가 아닙니다. 다만 bounded ring3 실행 조각은 동작합니다. 두 정적 bootstrap process descriptor가 각자 private CR3와 16KiB ring0 entry stack을 소유하고, PID 1/slot 0 다음 PID 2/slot 1이 커널 내장 static ELF64 데모를 각자의 주소공간에서 순차 실행해 `int 0x80` 관측 시스콜과 `exit(42)`를 왕복합니다. 각 descriptor는 ISR에서 검증된 176B trap evidence snapshot을 소유하며, per-boot process event journal v1은 이 두 실행의 acquire/capture/release 증거 6개를 덮어쓰지 않고 기록합니다. 둘 다 재개 가능한 실행 상태가 아니며 journal은 `evidence_only=1 switch_events=0 resume_ready=0`입니다. 장기 실행 유저스페이스 서비스, live continuation/switch, 실제 A→B→A, 두 process의 타이머 선점, 동적 주소공간/PMM, 디스크 프로그램, 영속 기억 런타임, 실시간 학습 승격 루프는 후속 영역입니다.

AI 작업자는 루트 [`AGENTS.md`](AGENTS.md)의 저장소 규칙과
[`.agents/README.md`](.agents/README.md)의 프로젝트 스킬 색인을 먼저
확인한 뒤 [통합 작업 진입 가이드](docs/meta/integrated_work_guide_ko.md)에서
요청 유형, 주제별 정본과 검증 경로를 고릅니다. 체크포인트는 `beta`에서 검증하고
승인된 동일 SHA만 `main`으로 fast-forward합니다.

## GitHub Description

Suggested repository description:

> AI-native management/runtime project centered on Room → Cell → Node → NodeBit, with Linux-hosted userspace as the intended delivery path and a native proof kernel.

## Current Status (2026-08-15)

- **Current beta:** `v0.2.0-beta.6` (`0.2.0-beta.6 "Genesis"` boot banner).
- **Boot path:** x86_64 Multiboot2 커널, GDT/IDT/TSS, 페이징, PIT IRQ0 scheduler tick bootstrap, QEMU 스모크 테스트 기반.
- **Hardening:** stack protector, NX/W^X 2MB identity-map marking, SMEP/UMIP/SMAP 감지/활성화 경로, uaccess STAC/CLAC fence, 검증된 CPL3 `#BP`/`int 0x80` entry AC 제거, #PF CR2 dump, #DF IST1, cppcheck CI.
- **Memory:** 물리/가상 할당 기반, 텐서 메모리 메타데이터, 수명 프로파일링, 메모리 패브릭 노드, 공유 영역 스캐폴딩.
- **Kernel Room topology maturity:** 전체 topology는 계속 `PARTIAL`이다. 기존 aggregate와 9개 syscall-range descriptor, `CURRENT` K1 schema 1/1024B hierarchy에 더해 bounded native K2-a가 `CURRENT`다. K2-a는 K1 ABI를 바꾸지 않고 schema 1/256B snapshot에서 Node 101/Cell 1 generation과 SLM MAIN의 typed namespace, semantic kind/role, producer-owned boot-local instance/generation을 결속한다. exact boot marker, structured `kernel_room_binding`, `state binding`으로 검증된다. source refresh/exit/recreate/rebind, Linux source, resource attribution, principal/ownership는 아직 없다.
- **Identity boundary:** Memory Fabric `domain_id`, SLM `agent_tree.node_id`, SLM policy `slm_nodebit_id`, 런타임 capability `node_id`, pipeline `owner_node`, scheduler task/PID/ring ID는 독립 네임스페이스다. 숫자가 같아도 같은 주체가 아니며, 명시적 namespace/binding/generation 없이 결합하지 않는다.
- **Linux delivery direction:** Linux-hosted userspace service는 의도된 기본 delivery
  경로로 결정됐다. schema v1의 13개 upstream source row와 fail-closed guard만
  `CURRENT`이고 Linux-hosted backend는 계속 `PLANNED`다. Linux `6.12.y` primary,
  `6.18.y` forward, QEMU `11.1.0`, VirtIO 1.2 CS01 selected baseline은 source
  기준선이며 PID/cgroup/pidfd/PSI는 source-only, `code_import=0`이다.
- **Pressure observation:** schema 1의 `state pressure`가 workload queue, Memory Fabric reader/writer 중첩, 누적 NodeBit 거부율을 0..1024 정수 벡터로 읽는다. 현재는 system→plane 2단계 관측만 `CURRENT`이며 task migration이나 budget apply는 하지 않는다.
- **Resource observation:** schema 1의 커널 내부 `ai_resource_snapshot_t`가 heap bytes, tensor bytes, active Memory Fabric windows, inference ring registrations, runnable scheduler tasks를 고정 5개 aggregate row로 읽는다. 모든 owner는 아직 `NONE/UNATTRIBUTED`이며 read-only `SYS_INFO_RESOURCE`(0x706) syscall과 `state resource` 셸 토픽은 `CURRENT`, owner attribution과 quota/reserve/apply는 `PLANNED`다.
- **Autonomy and policy:** 헬스 스냅샷, 제한된 자율 제안/롤백 경로, SLM 하드웨어 스냅샷, 두 종류의 NodeBit 조회/통계, Kernel Room syscall-range 분류 메타데이터. Kernel Room Axis Gate의 dispatcher-level 강제는 아직 없다.
- **Userspace:** bounded bootstrap process pair slice 완료. 정적 descriptor 2개가 각자 private 2MiB user leaf/CR3와 16KiB ring0 entry stack을 소유하며, PID 1/slot 0과 PID 2/slot 1이 순차적으로 static ELF64 데모의 `int 0x80` 왕복, uaccess 거부, CR3·BSP `rsp0` 복원을 검증한다. M3-b-3b2c 진입 게이트의 trapframe C/NASM offset·크기 계약(176B), `from_user` CPL0/CPL3 판별, process-owned trap evidence snapshot v0, process event journal v1, CPL3 entry AC hardening은 `CURRENT`다. pair의 ring3 `#BP` 2회와 `int 0x80` 6회는 saved user RFLAGS를 바꾸지 않으면서 entry live AC가 항상 0임을 증명한다. SMAP active이면 `clac`, 비활성·미지원이면 `pushfq/btr/popfq` fallback을 사용해 `#UD` 없이 같은 결과를 내며, `default`/`max-smap` CPU profile verifier가 두 분기를 재현한다. 각 ring3 `int3`의 full frame은 ISR 시점 current owner/private CR3/TSS `rsp0`/IF=0 검사를 통과한 descriptor에 복사되고, per-boot capture sequence 1,2와 각 finish 뒤 보존, 두 번째 실행까지 끝난 최종 pair 경계의 양쪽 재조회를 증명한다. capacity 8/no-overwrite journal은 acquire/capture/release의 exact 6-event vector를 보존하며 `state user`의 `event_*` mirror와 fail-closed Python/PowerShell/structured 검증으로 고정된다. 그 owner lifecycle은 `0→1→0→2→0`이며 CPU switch가 아니다. prepare 코드 경로는 이전 snapshot을 지우고 run generation을 올리지만, 같은 slot을 다시 prepare하는 실부팅 증거는 아직 `PLANNED`다. journal은 `evidence_only=1 switch_events=0 resume_ready=0`이고 전체 process 모델은 `PARTIAL`이다. 실행 substrate 레인의 잔여는 live continuation/switch, 실제 A→B→A, timer preemption이다. 이는 Kernel Room 관리축보다 자동으로 우선하는 “다음 프로젝트 방향”이 아니다. future ring3 IRQ/NMI/IST entry와 실기기까지 이 proof가 일반화된 것은 아니다. `aios-init`, 디스크 기반 ELF 적재, 동적 주소공간 수명주기, 장기 실행 유저스페이스 런타임도 아직 없다.
- **Hardware AI access:** 가속기 인터페이스는 추상화/탐색 스캐폴딩 단계. 실제 GPU/NPU/TPU 드라이버와 직접 클럭 제어 백엔드는 계획 상태.
- **I/O:** e1000은 RX ring bootstrap + bounded RX poll/rearm + TX smoke 수준. USB/storage는 bootstrap/probe 중심이며, 다음 storage 목표는 virtio-blk 최소 read path.
- **Continuity runtime:** 단기/장기 기억 분리, 저널링, distillation, self-learning promotion flow는 유저스페이스 AI 런타임 로드맵.

## Project Direction

AIOS의 우선 방향은 커널 기능을 더 많이 나열하는 것이 아니라, Kernel Room이 살아 있는 AI 구조를 **관리 가능한 계층**으로 다루게 만드는 것입니다.

- **Room:** 전체 Cell inventory, 상태 요약, lifecycle/reconciliation의 정본 소유자. K1은 bounded bootstrap hierarchy만 소유하며 live lifecycle/reconciliation은 아직 없다.
- **Cell:** 격리·수명·자원·건강 상태를 함께 관리하는 bounded 단위. K1은 Cell ID 1 + 그 안에 bound된 Node ID 101 + parent-bound NodeBit ID 1001/1002를 하나의 management-only hierarchy proof로 고정했다. 이는 Cell-only 성공이 아니라 전체 최소 계층 증거다.
- **Node:** Cell 안에서 역할을 가진 agent/service/runtime 단위. 기존 여러 `node_id`는 canonical Node가 아니라 입력 스캐폴드이므로 명시적으로 bind한다.
- **NodeBit:** Node 안의 가장 작은 capability/policy/resource projection. 기존 SLM policy node와 runtime capability node를 곧바로 동일시하지 않고 namespace가 있는 adapter로 연결한다.
- **Execution substrate:** ring3 process, scheduler, memory, storage, network, HAL은 위 관리 모델이 실제 일을 수행하도록 받치는 기반이다. M3~M5의 완성도는 계속 높이되 방향 선택을 독점하지 않는다.
- **Hosted substrate:** Linux-hosted userspace service는 의도된 기본 delivery
  구현축이다. H0 source policy만 `CURRENT`이며 H1~H5와 실행 backend는
  `PLANNED`다. bounded native K2-a semantic oracle은 `CURRENT`이며, 다음 H1이
  OS-neutral lifecycle trace와 공통 negative fixture를 고정하면 H2
  observe-only service를 시작한다. 광범위한 native process/storage 확장은 선행조건이
  아니다. H4 validation과 H5 apply는
  K5와 별도 승인 전까지 열지 않는다.
- **Policy boundary:** Axis Gate의 실제 authorize/enforcement는 canonical identity, parent binding, generation, principal과 ownership이 생긴 뒤의 `PLANNED` 단계다. 현재 9개 descriptor는 분류 메타데이터다.
- **Orbit:** Cell/Node placement와 분산 배치를 탐구하는 `RESEARCH` 축이다. Cell 관리 기반과 검증 증거 없이 지원 기능으로 선언하지 않는다.

## Architecture

```
Management plane (overall PARTIAL)
Kernel Room
└── Cell
    └── Node
        └── NodeBit
             │ K1 bootstrap hierarchy v0 implemented
             │ native K2-a binding oracle implemented
             │ lifecycle/reconcile remain PARTIAL/PLANNED
             ▼
                 ┌─ Linux-hosted userspace service
                 │  intended default delivery · PLANNED
Canonical model ─┤
                 └─ native x86_64 reference/proof kernel
                    bounded executable evidence · CURRENT/PARTIAL

Current evidence inputs
memory fabric · health · SLM · runtime NodeBit · pipeline
resource/pressure observation · scheduler · ring3 · drivers · H0 source policy
```

## Key Features

### Tensor Memory Manager Foundation
- 텐서 중심 메모리 메타데이터와 64바이트 정렬 정책
- 용도별 메모리 풀 분리 (Model / Inference / DMA / KV-Cache)
- 수명 기반 프로파일링 (SHORT_TERM / LONG_TERM / REALTIME / RANDOM)
- 2MB 페이지 경로와 일반 페이지 경로를 나눌 수 있는 기반 구조
- 커널 내부 selftest와 호스트 테스트를 통한 회귀 검증

### Memory Fabric Foundation
- 멀티 AI 에이전트용 메모리 도메인(seed)과 공유 텐서 window 추적
- 복사보다 zero-copy / shared window 우선 정책
- ACPI / PCIe / selftest를 바탕으로 hotset / staging / worker 수 추천
- 미래의 NUMA / CXL 확장을 막지 않는 fallback-first 설계

### AI Pressure Tracker v0
- `sched`, `memory`, `policy`의 append-only plane ID와 versioned snapshot
- workload queue occupancy, shared-window fanout/writer pair, NodeBit denial ratio 관측
- `max_levels=4` 중 system→plane 두 단계만 활성화한 확장 가능한 고정 계층
- gate eligibility bitmap과 pressure score를 분리하고 `observation_only=1`로 고정
- required boot selftest, structured boot summary, `state pressure` shell lane으로 검증

### AI Resource Ledger v0
- append-only resource kind 5개와 unit ID 2개를 가진 versioned fixed snapshot
- heap/tensor 사용 bytes, Memory Fabric active-window 수, ring registration 수, runnable task 수를 관측
- limit/used는 5종 모두 유효하지만 source-native high-water는 tensor 1종만 유효하고 denial counter는 아직 없음
- owner 필드는 future attribution을 위해 존재하지만 현재 모든 row는 `NONE/UNATTRIBUTED`
- exact required boot selftest와 structured `resource` boot summary로 검증
- syscall, shell topic, reserve/release/throttle, allocator/scheduler policy 변경은 아직 없음

### Kernel Room Management Hierarchy v0
- `kernel_room_management_snapshot_t`는 schema 1의 bounded 1024B read-only snapshot
- capacity는 Cell 2 / Node 4 / NodeBit 8, bootstrap seed는 Cell 1 / bound Node 1 / parent-bound typed NodeBit 2
- canonical ID는 Cell 1, Node 101, NodeBit 1001/1002이며 source kind와 generation을 별도로 기록
- duplicate/orphan/unknown/stale/overflow와 non-zero unused tail을 내부 selftest에서 fail-closed로 거부
- exact `[ROOM] management hierarchy selftest PASS ...`와 structured `kernel_room_management`, `state room` full-row 계약으로 검증
- legacy SLM/runtime NodeBit projection, resource attribution, authorize/apply edge는 없음

### Kernel Room Native Source Binding K2-a
- SLM producer는 exact-one active/persistent MAIN agent를 64B copied source snapshot으로 제공
- source namespace/kind/role/lifecycle와 boot-local `source_instance`/`source_generation`은 policy generation·timestamp·PID와 분리
- `kernel_room_source_binding_snapshot_t`는 schema 1의 bounded 256B read-only snapshot, capacity 2
- canonical/source/binding generation과 append-only reject reason을 분리하고 missing/duplicate/orphan/kind/role/instance/rollback/stale/init-order/tail 반례를 거부
- exact `[ROOM] source binding selftest PASS ...`, structured `kernel_room_binding`, `state binding`과 K1→K2→aggregate Room 순서로 검증
- refresh/reconcile, hosted source, resource attribution, authorize/apply edge는 없음

### SLM Snapshot and Two Distinct NodeBit Surfaces
- `slm_hw_snapshot_t`로 커널 health, 메모리 패브릭, agent tree, device readiness를 한 번에 노출
- SLM 런타임이 없거나 준비되지 않은 경우에도 안정적인 snapshot/fallback 값 제공
- `SYS_SLM_NODEBIT_LOOKUP`는 `slm_orchestrator.c`의 API/tool/device/memory/clock/policy catalog에서 effective policy node를 읽는다
- `runtime/nodebit.c`는 별도 runtime capability registry이며 `SYS_NODEBIT_REGISTER/UPDATE/STATS`와 pipeline capability gate를 제공한다
- 두 NodeBit ID 공간과 Memory Fabric/pipeline Node ID, SLM의 나머지 agent는 아직
  canonical Room→Cell→Node→NodeBit 계층에 bind되지 않았다. exact-one SLM MAIN만
  별도 native K2-a snapshot으로 Node 101에 명시적으로 결속된다.

### AI Workload Scheduler Foundation
- 다단계 피드백 큐와 virtual runtime 추적 기반
- 데드라인 인식 추론 작업 메타데이터
- 가속기 친화도(Affinity)와 우선순위 조정용 스캐폴딩
- PIT IRQ0 100Hz tick/accounting bootstrap (`[TIMER] PIT IRQ ready` smoke checkpoint)
- 실제 production-grade 선점/멀티코어 스케줄링은 후속 단계

### Accelerator HAL Scaffold
- PCI 버스 탐색과 가속기 디바이스 추상화 인터페이스
- GPU/TPU/NPU를 같은 capability 모델로 다루기 위한 ABI 기반
- MatMul, Attention 등 AI 핵심 연산 API 표면
- 실제 벤더 드라이버와 DMA 실행 경로는 아직 계획 상태

### Autonomy / Policy Control Plane
- L0~L3 자율 제어 레벨 (관찰 -> 안전 적용 -> 자율 최적화)
- 정책 제안/승인/적용/롤백 파이프라인
- 이벤트 로깅 및 텔레메트리 프레임 수집
- 스케줄러/드라이버 액추에이터를 바로 실행하지 않고 검증 가능한 정책 gate로 통제

### Interrupt & Exception Handling
- x86_64 IDT (Interrupt Descriptor Table) 완전 구현
- 32개 CPU 예외 핸들러 (Divide Error, Page Fault, GPF 등)
- legacy PIC IRQ 32~47 스텁과 PIT IRQ0 timer handler bootstrap
- kernel_panic() 안전 정지 메커니즘
- 시리얼 + VGA 이중 출력 디버깅

### Userspace Boundary Status
- ring3 진입을 위한 TSS/segment/user access guard 기반
- static ELF64 header/program-header 검증과 단일 PT_LOAD 세그먼트 적재 경로
- CPL3 `int 0x80` -> `ai_syscall_dispatch` -> ring3 buffer copy -> `exit(42)` 왕복 smoke
- uaccess window + SMAP STAC/CLAC fence 기반의 유저 포인터 경계 검증
- CPL3 `#BP` 공통 entry 2회와 `int 0x80` entry 6회에서 saved user RFLAGS 보존 + live AC=0을 exact `[SEC] ring3 entry AC hardening PASS ...`와 `state sec entry_*`로 검증. SMAP active는 `clac`, 비활성·미지원은 `pushfq/btr/popfq` fallback이며 `default`/`max-smap` CPU profile이 양쪽을 재현
- 정적 2개 bootstrap descriptor의 private CR3/run state/16KiB ring0 entry-stack ownership, PID 1→PID 2 순차 실행과 각 실행 사이/최종 BSP TSS `rsp0`·CR3·current owner 복원 증명
- 각 ring3 `int3`의 176B frame을 ISR 시점 owner/current/private CR3/TSS `rsp0`/IF=0 검사 뒤 해당 descriptor에 복사하고, sequence 1,2·finish 뒤 보존·distinct storage·`resume_ready=0`을 `[PROC] trap evidence snapshot PASS`로 증명
- capacity 8/no-overwrite process event journal v1이 여섯 acquire/capture/release record를 exact 순서로 보존하고 `[PROC] process event journal PASS`, structured `process_event_journal`, `state user event_*`로 검증됨
- snapshot과 journal은 증거만 `CURRENT`이며 `evidence_only=1 switch_events=0 resume_ready=0`이다. `g_active_user_run_state`까지 포함한 live continuation/switch, 실제 A→B→A, 두 process 선점 교대, `aios-init`, 디스크 ELF, 동적 주소공간/PMM, long-running userspace AI runtime은 후속 구현 대상
- entry AC hardening의 `CURRENT` 범위는 실제 QEMU CPL3 `#BP`/`int 0x80`과 `default`/`max-smap` 재현까지다. future ring3 IRQ/NMI/IST entry, 실기기, resumable context, A→B→A와 preemption은 계속 `PARTIAL`/`PLANNED`다

### AI System Call Interface
> 이 표는 현재 ABI 공간과 스캐폴딩을 함께 보여줍니다. 모든 카테고리가 production-grade 구현을 의미하지는 않습니다.

| 범위 | 카테고리 | 주요 시스콜 |
|------|----------|------------|
| `0x100-0x1FF` | 모델 관리 | `SYS_MODEL_LOAD`, `SYS_MODEL_UNLOAD` |
| `0x200-0x2FF` | 텐서 조작 | `SYS_TENSOR_CREATE`, `SYS_TENSOR_DESTROY` |
| `0x300-0x3FF` | 추론 | `SYS_INFER_SUBMIT`, `SYS_INFER_STREAM` |
| `0x400-0x4FF` | 학습 | `SYS_TRAIN_FORWARD`, `SYS_TRAIN_BACKWARD` |
| `0x500-0x5FF` | 가속기 | `SYS_ACCEL_LIST`, `SYS_ACCEL_SELECT` |
| `0x600-0x6FF` | 파이프라인 | `SYS_PIPE_CREATE`, `SYS_PIPE_EXECUTE` |
| `0x700-0x7FF` | 시스템 정보 | `SYS_INFO_MEMORY`, `SYS_INFO_SYSTEM`, `SYS_INFO_ROOM`, `SYS_INFO_BOOTSTRAP` |
| `0x710-0x715` | 자율 제어 | `SYS_AUTONOMY_ACTION_PROPOSE`, `SYS_AUTONOMY_ACTION_COMMIT`, `SYS_AUTONOMY_ROLLBACK_LAST` |
| `0x720-0x725`, `0x729` | SLM policy catalog | `SYS_SLM_HW_SNAPSHOT`, `SYS_SLM_PLAN_SUBMIT`, `SYS_SLM_PLAN_APPLY`, `SYS_SLM_NODEBIT_LOOKUP`, `SYS_SLM_PLAN_OBSERVE` |
| `0x726-0x728` | Runtime NodeBit capability | `SYS_NODEBIT_REGISTER`, `SYS_NODEBIT_UPDATE`, `SYS_NODEBIT_STATS` |

## Project Structure

저장소는 7개 도메인으로 구성된 모노레포다. 도메인 경계·의존 규칙·"어디에 두나" 결정 가이드는
**[PROJECT.md](PROJECT.md)** 에 정리되어 있다.

```
aios-kernel/
├── PROJECT.md          # 도메인 맵 / 경계 규칙 (먼저 읽기)
├── Makefile            # 루트 위임 빌드 (→ kernel/)
│
├── kernel/             # ① 베어메탈 커널 (boot, core, mm, sched, hal, runtime, drivers, include)
│   ├── Makefile        #    커널 빌드 시스템
│   ├── boot/           #    Multiboot2 엔트리, GDT, 페이징, long mode
│   ├── core/           #    main, health, acpi, time, shell, kernel_room, user_*, linker.ld
│   ├── interrupt/  mm/  sched/  hal/  runtime/  drivers/  lib/
│   └── include/        #    커널 공개 헤더
│
├── os/                 # ② AIOS native ring3 런타임 + 전용 프로그램
│   ├── runtime/  main_ai/  compat/  examples/  tools/
│   └── apps/           #    전용 프로그램 (스캐폴드)
│
├── hosted/             # ③ Linux-hosted 기본 delivery 도메인 (runtime PLANNED)
│   └── README.md       #    책임·의존·첫 구현 경계
├── models/             # ④ AI/SLM 모델 매니페스트 (가중치는 비추적)
│   └── manifests/
├── store/              # ⑤ 부팅 후 온라인 드라이버/프로그램 다운로드 카탈로그
│   └── catalog/
├── tools/              # ⑥ 테스트·빌드 + 외부 source 정책 검증
│   ├── testkit/
│   └── platform/       # manifest/guard only; hosted runtime 아님
├── docs/               # ⑦ 설계 문서 (kernel/ autonomy/ os/ models/ tools/ meta/ kernel-room/)
└── .github/workflows/  # CI (linux-boot-check)
```

## Build & Run

### Prerequisites
```bash
sudo apt install nasm gcc-12 binutils qemu-system-x86 grub-pc-bin xorriso mtools
```

### Build
```bash
CC=gcc-12 make all          # 커널 바이너리 빌드
CC=gcc-12 make iso          # 부팅 가능한 ISO 이미지 생성
CC=gcc-12 make test         # QEMU 스모크 테스트
```

> 참고: 기본 컴파일러는 `gcc`이며, 다른 툴체인을 사용할 경우 `make CC=clang LD=ld.lld` 또는 `make TOOLCHAIN_PREFIX=x86_64-elf-` 형태로 지정할 수 있습니다.

### Windows (PowerShell)

Windows에서도 빌드 점검이 가능합니다. 현재 저장소에는 PowerShell 기반 헬퍼 스크립트가 포함되어 있으며,
검증된 조합은 다음과 같습니다.

- `make`: `winget install --id ezwinports.make`
- `nasm`: `winget install --id BrechtSanders.WinLibs.POSIX.UCRT`
- `qemu-system-x86_64`: `winget install --id SoftwareFreedomConservancy.QEMU`
- Unix 유틸리티(`head`, `grep`, `timeout`): Git for Windows
- bare-metal 크로스 컴파일러: `x86_64-elf-gcc`, `x86_64-elf-ld`, `x86_64-elf-objcopy`

가장 쉬운 실행 방법:

```powershell
pwsh -File .\tools\testkit\kernel\build-windows.ps1 -Target all
pwsh -File .\tools\testkit\kernel\build-windows.ps1 -Target test
py -3 .\tools\testkit\aios-testkit.py all --strict
```

Windows용 자세한 설치 및 경로 설정 방법은 [docs/tools/windows_build.md](docs/tools/windows_build.md)를 참고하세요.

### Python Testkit

Claude/Codex 작업과 CI에서 쓰는 주요 진입점은 `tools/testkit/aios-testkit.py`입니다.

```powershell
py -3 .\tools\testkit\aios-testkit.py all --strict
py -3 .\tools\testkit\aios-testkit.py kernel --target test --strict
py -3 .\tools\testkit\aios-testkit.py boot-matrix --profiles full minimal storage-only --strict
py -3 .\tools\testkit\aios-testkit.py boot-inventory --profiles full minimal storage-only --strict
py -3 .\tools\testkit\aios-testkit.py boot-perf --profiles full minimal storage-only --strict
py -3 .\tools\testkit\aios-testkit.py shell --strict
py -3 .\tools\testkit\aios-testkit.py shell --strict --skip-build
py -3 .\tools\testkit\aios-testkit.py os
```

Static analysis uses the same cppcheck lane as CI. If `cppcheck` is on `PATH`, run:

```powershell
cppcheck --std=c11 --platform=unix64 --enable=warning,performance,portability --inline-suppr --suppress=missingIncludeSystem --error-exitcode=1 -Ikernel/include kernel/
```

On Windows hosts where Cppcheck is installed but not on `PATH`, the Program Files install can be called directly:

```powershell
& 'C:\Program Files\Cppcheck\cppcheck.exe' --std=c11 --platform=unix64 --enable=warning,performance,portability --inline-suppr --suppress=missingIncludeSystem --error-exitcode=1 -Ikernel/include kernel/
```

### Run in QEMU
```bash
make run            # QEMU에서 커널 실행 (VGA + 시리얼)
make run-headless   # Headless 모드 (시리얼 출력만)
make debug          # GDB 디버깅 모드로 실행
```

> 참고: 이 커널은 Multiboot2 기반이므로 `run`, `run-headless`, `debug`, `test`는 모두 GRUB ISO를 통해 부팅합니다. `grub-mkrescue`가 없는 환경에서는 `make all`까지만 가능하며, 실제 부팅 테스트는 `make iso` 이후에 수행됩니다.

## Technical Specifications

| 항목 | 사양 |
|------|------|
| 타겟 아키텍처 | x86_64 (Long Mode) |
| 부트 규격 | Multiboot2 |
| 커널 언어 | C + x86_64 Assembly |
| 페이지 크기 | 4KB (일반) / 2MB (거대 페이지) |
| 텐서 정렬 | 64바이트 정렬 정책 |
| AI 작업 슬롯 | 256개 규모의 scheduler foundation |
| 가속기 슬롯 | 16개 디바이스 규모의 HAL/SLM snapshot ABI |
| 모델 슬롯 | 64개 규모의 model registry scaffold |
| 유저스페이스 | 첫 ring3 static ELF64 demo + `int 0x80` 왕복 완료, full service/runtime 예정 |
| 기본 delivery 방향 | Linux-hosted userspace service (제품 결정 완료, backend 구현 `PLANNED`) |
| AI 가속기 | PCI/capability abstraction scaffold, 실제 벤더 드라이버 예정 |
| CI | GitHub Actions (cppcheck + OS tool smoke + QEMU smoke + shell lane) |

## Planning Documents

전체 설계 문서 색인은 **[docs/README.md](docs/README.md)**, 저장소 도메인 구조는
**[PROJECT.md](PROJECT.md)** 를 참고하세요. 현재 작업의 핵심 진입점은 다음과 같습니다.

- [통합 작업 진입 가이드](docs/meta/integrated_work_guide_ko.md) — 요청 분류, 정본·스킬·검증 선택과 문서 관리
- [AIOS 성숙도 우선 작업흐름](docs/meta/minimal_io_and_maturity_workflow_ko.md) — K/M/C/W/H축과 전역 우선순위 정본
- [H1 OS-neutral binding trace/replay 작업 준비서](docs/os/h1_binding_trace_replay_workplan_ko.md) — 현재 bounded H1 계약; H1-a transport 조각은 `PARTIAL`, lifecycle replay(H1-b) 진행 전
- [Kernel Room 관리 모델 정본](docs/kernel-room/kernel_room_management_model_ko.md)
- [Linux-hosted substrate와 upstream resource 정책 정본](docs/os/linux_hosted_substrate_and_resource_policy_ko.md)
- [검증 도구 진화 설계](docs/tools/verification_tooling_evolution_design_ko.md)와 [Testkit 가이드](docs/tools/testkit_guide_ko.md)
- [문서 전체 인덱스와 수명주기](docs/README.md) — 도메인별 활성·`REVIEW`·`OLD` 문서

이전 로드맵, 과거 점검, 외부 조사와 분야별 참고 계획은 문서 인덱스에서 수명주기를
확인한 뒤 사용합니다.

## License

MIT License

이 표기는 프로젝트의 라이선스 의도다. 현재 저장소에는 canonical tracked root
`LICENSE`/`COPYING`/`NOTICE` 파일과 외부 코드 import-compatibility 정책이 없으므로,
upstream 코드 반입 승인을 뜻하지 않는다.

## Acknowledgments

이 프로젝트는 AI 워크로드에 최적화된 운영체제의 가능성을 탐구하기 위한 실험적 프로젝트입니다.
