# AIOS - AI-Native Operating System

<p align="center">
  <strong>AI가 자신의 작업 공간을 이해하고 사용자와 함께 활동하는 AI-native 운영 환경</strong>
</p>

---

## Overview

AIOS는 **AI가 자신의 작업 공간과 상태를 이해하고, 로컬 자원을 효율적으로 사용하며,
사용자와 지속적으로 상호작용하는 운영 환경**을 만듭니다. 공간 인지, 편안하고 안정적인
실행, 작업 중 사용자 개입을 제품의 중심 가치로 둡니다.
[제품 목적 정본](docs/meta/aios_product_direction_ko.md)이 의미와 성공 기준을 소유합니다.

관리 핵심은 **Room → Cell → Node → NodeBit**입니다. 현재 Linux-hosted userspace가
기본 제품 실행 경로이고 자체 x86_64 커널은 별도의 reference/proof 경로로 유지합니다.
현재 운영 이미지의 하드웨어 특권 커널과 드라이버 실행 주체는 Linux입니다.
자체 관리 모델, 자체 native 커널, Linux 위에서의 제품 실행 성과를 구분합니다.

현재 제공하는 범위는 고유 `aios>` CLI, Linux-visible 환경 관측, DNS·HTTP(S), 실제
MAIN 모델 요청, 제한된 서비스·Cell·backend 수명과 명시적 결속입니다(`PARTIAL`).
이 체크포인트의 개발 소스 v0.10은 MAIN 환경 문맥 전달과 질문 UUID의 접수·조회·결과·명시적 취소를
연결했습니다(`PARTIAL`). 보존된 v0.8 실제 문맥 소비의 수정 검증기 재생과 이 개발 소스의
Windows·Linux fixture 결과는 [환경 문맥 가이드](docs/os/aios_space_context_guide_ko.md)가 소유합니다.
실제 모델의 정상 답변 1회와 별도 진행 중 Task의 취소·worker 종료·전체 backend 독립 종료 관측을 같은 CLI에서 검증했습니다. source 39개 운영 이미지 acceptance는 아직 없습니다.
이전 대화·범용 작업 수정·CLI 소실/재부팅을 넘는 자동 재개는 `PLANNED`입니다.

사용자가 승인한 장기 실증은 자기 참조·관측·행동·피드백을 실제 사용으로 연결합니다.
현재 첫 단계는 같은 pinned Qwen을 Windows CPU에서 쓰는 private sandbox 연구 실험이며
개발 구현은 `PARTIAL`입니다. 첫 pilot은 귀속 정답의 입력 노출로 중단되어 원본
FAIL/NOT_EVALUABLE을 보존했습니다. 입력 정정 후 calibration은 실행 무결성 PASS였지만
모델 두 조건의 확인된 목표 완료는 0입니다. 최종 Windows 검사는 135개 중 133 PASS·
권한 조건 2 skip이며 실제 grammar 독립 감사도 통과했습니다. 후속 ON은 관측만 반복했고
OFF는 생성 미완료로 실패했습니다.
전체 ON/OFF 비교는 NOT_EVALUABLE입니다. 이 체크포인트는 검증된 연구 기반과 실패
증거를 보존하며, 다음 단계는 관측에서 행동으로 전이하는 공통 prompt 비교입니다.
[실험 운영 가이드](docs/meta/self_reference_research_workflow_ko.md)는
반복 연구·검토 절차를, [전역 큐](docs/meta/minimal_io_and_maturity_workflow_ko.md#self-reference-acceptance)는
전체 다섯 단계 수락 기준을 소유합니다. 첫 pilot의 응용 owner 검사를 native 권한 구현으로 보지 않습니다.


### 시작하기

| 목적 | 진입점 |
|---|---|
| 제품 목적과 사용자·AI 경험 이해 | [제품 목적 정본](docs/meta/aios_product_direction_ko.md) |
| 새 작업에서 읽을 문서·검증 경로 선택 | [통합 작업 진입 가이드](docs/meta/integrated_work_guide_ko.md) |
| 다음 개발 조각 확인 | [전역 작업흐름](docs/meta/minimal_io_and_maturity_workflow_ko.md) |
| 문서의 검토 범위와 재검토 필요 확인 | [문서 신선도 원장](docs/meta/document_freshness_registry_ko.md) |
| 운영 이미지 생성·검증·실행 | [운영 이미지 가이드](docs/os/aios_operating_image_guide_ko.md) |
| 개발 VM과 CLI·인터넷 사용 | [CLI 가이드](docs/os/aios_cli_internet_guide_ko.md) |
| 실제 모델·관리 관계·복구 사용 | [MAIN 가이드](docs/os/aios_agent_binding_guide_ko.md), [backend 가이드](docs/os/aios_backend_lifecycle_guide_ko.md) |
| MAIN이 실제로 받는 환경 문맥·현재/미관측/오래된 정보 | [환경 문맥 가이드](docs/os/aios_space_context_guide_ko.md) |

준비된 Windows 환경에서는 [Start-AiosImage.cmd](tools/hosted/Start-AiosImage.cmd)로
사용자 디스크를 실행하고, 모델 profile은 `-Agent`로 선택합니다.
[Start-AiosConsole.cmd](tools/hosted/Start-AiosConsole.cmd)는 별도 임시 개발 VM입니다.
`build/`의 이미지·전용 실행기·과거 보고서와 사용자 캐시는 Git checkout에 포함되지
않습니다. 새 환경의 의존성 준비와 Build·Smoke·Run은 운영 이미지 가이드를 따릅니다.

이미지 선택, 이전 버전, 실패 원본, source snapshot 및 게시 시점 차이는 운영 이미지
가이드가 소유합니다. 과거 이미지의 PASS와 문서 검토일은 현재 checkout의 새 실행
검증을 대신하지 않습니다. 2026-09-09 목적·문서 개정 뒤의 v0.10 변경은 별도로 검증하며,
보존된 v0.7 운영 이미지의 PASS를 새 코드의 실행 검증으로 사용하지 않습니다.

## GitHub Description

Suggested repository description:

> AI-native environment for grounded workspace awareness, efficient local execution, and continuous human–agent interaction, with an independent management model, Linux-hosted runtime, and native proof kernel.

## Current Status

- **Native boot banner:** `v0.2.0-beta.6` (`0.2.0-beta.6 "Genesis"`). 이 표기는 native banner이며 hosted CLI 버전이나 최신 beta commit을 뜻하지 않습니다.
- **Boot path:** x86_64 Multiboot2 커널, GDT/IDT/TSS, 페이징, PIT IRQ0 scheduler tick bootstrap, QEMU 스모크 테스트 기반.
- **Hardening:** stack protector, NX/W^X 2MB identity-map marking, SMEP/UMIP/SMAP 감지/활성화 경로, uaccess STAC/CLAC fence, 검증된 CPL3 `#BP`/`int 0x80` entry AC 제거, #PF CR2 dump, #DF IST1, cppcheck CI.
- **Memory:** 물리/가상 할당 기반, 텐서 메모리 메타데이터, 수명 프로파일링, 메모리 패브릭 노드, 공유 영역 스캐폴딩.
- **Kernel Room topology maturity:** 전체 topology는 계속 `PARTIAL`이다. 기존 aggregate와 9개 syscall-range descriptor, `CURRENT` K1 schema 1/1024B hierarchy에 더해 bounded native K2-a가 `CURRENT`다. K2-a는 K1 ABI를 바꾸지 않고 schema 1/256B snapshot에서 Node 101/Cell 1 generation과 SLM MAIN의 typed namespace, semantic kind/role, producer-owned boot-local instance/generation을 결속한다. exact boot marker, structured `kernel_room_binding`, `state binding`으로 검증된다. 이 native oracle에는 source refresh/exit/recreate/rebind, Linux source, resource attribution, principal/ownership가 없다. 별도 hosted MAIN 결속·Cell 관리 수명은 `PARTIAL`이며 관리 모델 정본의 범위를 따른다.
- **Identity boundary:** Memory Fabric `domain_id`, SLM `agent_tree.node_id`, SLM policy `slm_nodebit_id`, 런타임 capability `node_id`, pipeline `owner_node`, scheduler task/PID/ring ID는 독립 네임스페이스다. 숫자가 같아도 같은 주체가 아니며, 명시적 namespace/binding/generation 없이 결합하지 않는다.
- **Linux delivery direction:** Linux-hosted userspace service는 의도된 기본 delivery
  경로로 결정됐다. schema v1의 13개 upstream source row와 fail-closed guard는
  `CURRENT`이다. H1-a transport와 H1-b semantic replay, exact 12-fixture manifest,
  H1-c self-contained artifact/독립 재생 parity는 전용 Linux/Windows fixture/parity
  CI의 동일 run·exact SHA terminal 성공과 세 artifact로 검증돼 `CURRENT`다.
  H2-a startup/inventory/CLI·DNS·HTTP(S)는 `PARTIAL`, 전체 H2 결속 backend는 `PLANNED`다. Linux
  `6.12.y` primary, `6.18.y` forward, QEMU `11.1.0`, VirtIO 1.2 CS01 selected
  baseline은 source 기준선이며 PID/cgroup/pidfd/PSI는 source-only,
  `code_import=0`이다.
- **Pressure observation:** schema 1의 `state pressure`가 workload queue, Memory Fabric reader/writer 중첩, 누적 NodeBit 거부율을 0..1024 정수 벡터로 읽는다. 현재는 system→plane 2단계 관측만 `CURRENT`이며 task migration이나 budget apply는 하지 않는다.
- **Resource observation:** schema 1의 커널 내부 `ai_resource_snapshot_t`가 heap bytes, tensor bytes, active Memory Fabric windows, inference ring registrations, runnable scheduler tasks를 고정 5개 aggregate row로 읽는다. 모든 owner는 아직 `NONE/UNATTRIBUTED`이며 read-only `SYS_INFO_RESOURCE`(0x706) syscall과 `state resource` 셸 토픽은 `CURRENT`, owner attribution과 quota/reserve/apply는 `PLANNED`다.
- **Autonomy and policy:** 헬스 스냅샷, 제한된 자율 제안/롤백 경로, SLM 하드웨어 스냅샷, 두 종류의 NodeBit 조회/통계, Kernel Room syscall-range 분류 메타데이터. Kernel Room Axis Gate의 dispatcher-level 강제는 아직 없다.
- **Userspace:** bounded bootstrap process pair slice 완료. 정적 descriptor 2개가 각자 private 2MiB user leaf/CR3와 16KiB ring0 entry stack을 소유하며, PID 1/slot 0과 PID 2/slot 1이 순차적으로 static ELF64 데모의 `int 0x80` 왕복, uaccess 거부, CR3·BSP `rsp0` 복원을 검증한다. M3-b-3b2c 진입 게이트의 trapframe C/NASM offset·크기 계약(176B), `from_user` CPL0/CPL3 판별, process-owned trap evidence snapshot v0, process event journal v1, CPL3 entry AC hardening은 `CURRENT`다. pair의 ring3 `#BP` 2회와 `int 0x80` 6회는 saved user RFLAGS를 바꾸지 않으면서 entry live AC가 항상 0임을 증명한다. SMAP active이면 `clac`, 비활성·미지원이면 `pushfq/btr/popfq` fallback을 사용해 `#UD` 없이 같은 결과를 내며, `default`/`max-smap` CPU profile verifier가 두 분기를 재현한다. 각 ring3 `int3`의 full frame은 ISR 시점 current owner/private CR3/TSS `rsp0`/IF=0 검사를 통과한 descriptor에 복사되고, per-boot capture sequence 1,2와 각 finish 뒤 보존, 두 번째 실행까지 끝난 최종 pair 경계의 양쪽 재조회를 증명한다. capacity 8/no-overwrite journal은 acquire/capture/release의 exact 6-event vector를 보존하며 `state user`의 `event_*` mirror와 fail-closed Python/PowerShell/structured 검증으로 고정된다. 그 owner lifecycle은 `0→1→0→2→0`이며 CPU switch가 아니다. prepare 코드 경로는 이전 snapshot을 지우고 run generation을 올리지만, 같은 slot을 다시 prepare하는 실부팅 증거는 아직 `PLANNED`다. journal은 `evidence_only=1 switch_events=0 resume_ready=0`이고 전체 process 모델은 `PARTIAL`이다. 실행 substrate 레인의 잔여는 live continuation/switch, 실제 A→B→A, timer preemption이다. 이는 Kernel Room 관리축보다 자동으로 우선하는 “다음 프로젝트 방향”이 아니다. future ring3 IRQ/NMI/IST entry와 실기기까지 이 proof가 일반화된 것은 아니다. `aios-init`, 디스크 기반 ELF 적재, 동적 주소공간 수명주기, 장기 실행 유저스페이스 런타임도 아직 없다.
- **Hardware AI access:** 가속기 인터페이스는 추상화/탐색 스캐폴딩 단계. 실제 GPU/NPU/TPU 드라이버와 직접 클럭 제어 백엔드는 계획 상태.
- **I/O:** e1000은 RX ring bootstrap + bounded RX poll/rearm + TX smoke 수준. USB/storage는 bootstrap/probe 중심이며, 다음 storage 목표는 virtio-blk 최소 read path.
- **Continuity runtime:** 단기/장기 기억 분리, 저널링, distillation, self-learning promotion flow는 유저스페이스 AI 런타임 로드맵.

## Project Direction

[제품 목적](docs/meta/aios_product_direction_ko.md)은 AI의 공간·상태 인지, 로컬 효율,
안정적인 활동, 사용자와의 상호작용을 함께 평가합니다. [Kernel Room 관리 모델](docs/kernel-room/kernel_room_management_model_ko.md)은
그 경험에 필요한 정체성·관계·세대·유효성을 소유합니다.

다음 작업과 선행조건은 [전역 작업흐름](docs/meta/minimal_io_and_maturity_workflow_ko.md#agent-consumer-next)에서
한 번만 정합니다. 각 변경은 제품 경험에 주는 효과와 관리 모델의 `DIRECT`/`SUPPORTING`/
`ORTHOGONAL`/`RESEARCH` 관계, 구현 성숙도를 따로 기록합니다. 상호작용 개선도 제품 성과이며,
관리 계층 수정이나 커널 기능 확장을 동반해야만 가치가 생기는 것은 아닙니다.

자체 native 커널의 실행 책임은 별도 증거로 넓혀 갑니다. Linux가 제공하는 드라이버 지원을
자체 커널의 직접 호환으로 표현하지 않습니다. 공간 UI를 Cell 또는 Orbit runtime의
구현으로 해석하지 않으며, 권한·자원 적용에는 해당 계약과 검증이 필요합니다.

## Architecture

```
Management plane (overall PARTIAL)
Kernel Room
└── Cell
    └── Node
        └── NodeBit
             │ K1 bootstrap hierarchy v0 implemented
             │ native K2-a binding oracle implemented
             │ H1 lifecycle semantics implemented; live reconcile remains PARTIAL/PLANNED
             ▼
                 ┌─ Linux-hosted userspace service
                 │  bootstrap/MAIN PARTIAL · full H2/H3 PLANNED
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
- read-only `SYS_INFO_RESOURCE`(0x706)와 `state resource`는 `CURRENT`; owner attribution, quota, reserve/release/throttle, allocator/scheduler policy apply는 후속

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
├── hosted/             # ③ Linux-hosted 기본 delivery (H2-a/MAIN/자원 관측/Cell 1/backend PARTIAL; full H2/H3 PLANNED)
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
| 기본 delivery 방향 | Linux-hosted userspace service (H2-a/MAIN 결속·자원 관측·Cell 1 관리 전이·backend 수명/실행 결속 `PARTIAL`, 전체 H2/H3 `PLANNED`) |
| AI 가속기 | PCI/capability abstraction scaffold, 실제 벤더 드라이버 예정 |
| CI | GitHub Actions (cppcheck + resource/platform/H1 host suite + 전용 Linux/Windows fixture bundle/parity + OS tool/QEMU/shell lanes; H1 exact-SHA 원격 세 job·artifact 검증 완료) |

## Planning Documents

전체 설계 문서 색인은 **[docs/README.md](docs/README.md)**, 저장소 도메인 구조는
**[PROJECT.md](PROJECT.md)** 를 참고하세요. 현재 작업의 핵심 진입점은 다음과 같습니다.

- [통합 작업 진입 가이드](docs/meta/integrated_work_guide_ko.md) — 요청 분류, 정본·스킬·검증 선택과 문서 관리
- [AIOS 성숙도 우선 작업흐름](docs/meta/minimal_io_and_maturity_workflow_ko.md) — K/M/C/W/H축과 전역 우선순위 정본
- [H1 OS-neutral binding trace/replay 작업 준비서](docs/os/h1_binding_trace_replay_workplan_ko.md) — H1-a/b/c와 12개 fixture/artifact/parity 계약 `CURRENT`; §13.2에 exact-SHA 원격 cross-OS acceptance 증거
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
