# AIOS - Kernel-first AI-Native Operating System

<p align="center">
  <strong>Embodied LLM/SLM runtime을 위한 x86_64 베어메탈 커널 실험</strong>
</p>

---

## Overview

AIOS(AI-Native Operating System)는 AI 워크로드를 **1급 시민(First-class citizen)**으로 취급하는 커널 우선형 x86_64 베어메탈 OS 실험입니다. 현재 베타는 부팅 가능한 커널 기반, 텐서 지향 메모리 메타데이터, 메모리 패브릭, 헬스 스냅샷, SLM 하드웨어 스냅샷, NodeBit 정책 게이트, 관측 전용 AI pressure tracker와 resource ledger, 제한된 AI 시스콜 표면을 중심으로 발전하고 있습니다.

장기 방향은 embodied AI OS입니다. LLM/SLM 에이전트는 유저스페이스에서 단기 기억과 장기 기억을 분리해 유지하고, 세션을 넘어 연속성을 보존하며, 하드웨어에는 커널이 중재하는 정책 경계를 통해 접근합니다.

이 저장소는 아직 범용 상용 OS가 아닙니다. 다만 2026-07 기준으로 bounded ring3 실행 조각은 동작합니다. 두 정적 bootstrap process descriptor가 각자 private CR3와 16KiB ring0 entry stack을 소유하고, PID 1/slot 0 다음 PID 2/slot 1이 커널 내장 static ELF64 데모를 각자의 주소공간에서 순차 실행해 `int 0x80` 관측 시스콜과 `exit(42)`를 왕복합니다. 장기 실행 유저스페이스 서비스, 두 process의 타이머 선점, 동적 주소공간/PMM, 디스크 프로그램, 영속 기억 런타임, 실시간 학습 승격 루프는 후속 영역입니다.

AI 작업자는 루트 [`AGENTS.md`](AGENTS.md)의 저장소 규칙과
[`.agents/README.md`](.agents/README.md)의 프로젝트 스킬 색인을 먼저
확인합니다. 체크포인트는 `beta`에서 검증하고 승인된 동일 SHA만 `main`으로
fast-forward합니다.

## GitHub Description

Suggested repository description:

> Kernel-first AI-native OS experiment for embodied LLM/SLM runtime: ring3 ELF demo, memory fabric, NodeBit policy gates, and mediated hardware access.

## Current Status (2026-08-02)

- **Current beta:** `v0.2.0-beta.6` (`0.2.0-beta.6 "Genesis"` boot banner).
- **Boot path:** x86_64 Multiboot2 커널, GDT/IDT/TSS, 페이징, PIT IRQ0 scheduler tick bootstrap, QEMU 스모크 테스트 기반.
- **Hardening:** stack protector, NX/W^X 2MB identity-map marking, SMEP/UMIP/SMAP 감지/활성화 경로, #PF CR2 dump, #DF IST1, cppcheck CI.
- **Memory:** 물리/가상 할당 기반, 텐서 메모리 메타데이터, 수명 프로파일링, 메모리 패브릭 노드, 공유 영역 스캐폴딩.
- **Pressure observation:** schema 1의 `state pressure`가 workload queue, Memory Fabric reader/writer 중첩, 누적 NodeBit 거부율을 0..1024 정수 벡터로 읽는다. 현재는 system→plane 2단계 관측만 `CURRENT`이며 task migration이나 budget apply는 하지 않는다.
- **Resource observation:** schema 1의 커널 내부 `ai_resource_snapshot_t`가 heap bytes, tensor bytes, active Memory Fabric windows, inference ring registrations, runnable scheduler tasks를 고정 5개 aggregate row로 읽는다. 모든 owner는 아직 `NONE/UNATTRIBUTED`이며 read-only `SYS_INFO_RESOURCE`(0x706) syscall과 `state resource` 셸 토픽은 `CURRENT`, owner attribution과 quota/reserve/apply는 `PLANNED`다.
- **Autonomy and policy:** 헬스 스냅샷, 제한된 자율 제안/롤백 경로, SLM 하드웨어 스냅샷, NodeBit 정책 조회, Kernel Room syscall range classification.
- **Userspace:** bounded bootstrap process pair slice 완료. 정적 descriptor 2개가 각자 private 2MiB user leaf/CR3와 16KiB ring0 entry stack을 소유하며, PID 1/slot 0과 PID 2/slot 1이 순차적으로 static ELF64 데모의 `int 0x80` 왕복, uaccess 거부, CR3·BSP `rsp0` 복원을 검증한다. `aios-init`, 디스크 기반 ELF 적재, 두 process의 타이머 선점, full trapframe, 동적 주소공간 수명주기, 장기 실행 유저스페이스 런타임은 아직 없다.
- **Hardware AI access:** 가속기 인터페이스는 추상화/탐색 스캐폴딩 단계. 실제 GPU/NPU/TPU 드라이버와 직접 클럭 제어 백엔드는 계획 상태.
- **I/O:** e1000은 RX ring bootstrap + bounded RX poll/rearm + TX smoke 수준. USB/storage는 bootstrap/probe 중심이며, 다음 storage 목표는 virtio-blk 최소 read path.
- **Continuity runtime:** 단기/장기 기억 분리, 저널링, distillation, self-learning promotion flow는 유저스페이스 AI 런타임 로드맵.

## Project Direction

AIOS의 방향은 커널을 AI 시스템의 결정론적 body로 두고, 학습 행동은 정책으로 제한하는 것입니다.

- **Kernel:** 클럭, 메모리 보호, 디바이스 중재, 헬스 스냅샷, 롤백 표면, 작은 정책 비트맵을 담당합니다.
- **Userspace AI runtime:** LLM/SLM context, 단기 작업 기억, 장기 기억 저장소, semantic index, agent continuity를 담당합니다.
- **Learning loop:** 경험을 기록하고 검증한 뒤, 메모리나 작은 정책 artifact로 distill하고, 감사 가능한 gate를 통과한 결과만 승격합니다.
- **NodeBit policy:** API, tool, 하드웨어 액션을 실행 전에 빠르게 확인할 수 있도록 작은 노드형 정책 테이블을 메모리에 둡니다.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Planned userspace embodied AI runtime                         │
│  - short memory / long memory / continuity journal             │
│  - tools, APIs, agents, semantic stores                        │
├──────────────────────────────────────────────────────────────┤
│ AI syscall and policy boundary                                 │
│  - health snapshot, SLM snapshot, NodeBit lookup               │
│  - bounded proposal, verifier, rollback paths                  │
├──────────────────────────────────────────────────────────────┤
│ Current kernel foundation                                      │
│  - boot, paging, interrupts, scheduler foundation              │
│  - tensor memory, memory fabric, HAL scaffolds                  │
│  - ring3 static ELF demo, uaccess guards, QEMU testkit          │
├──────────────────────────────────────────────────────────────┤
│ Hardware                                                       │
│  CPU, memory, timers, PCI devices, storage/network bring-up     │
└──────────────────────────────────────────────────────────────┘
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

### SLM Snapshot and NodeBit Policy Gate
- `slm_hw_snapshot_t`로 커널 health, 메모리 패브릭, agent tree, device readiness를 한 번에 노출
- SLM 런타임이 없거나 준비되지 않은 경우에도 안정적인 snapshot/fallback 값 제공
- 작은 노드형 정책 비트맵(NodeBit)으로 API, tool, device action의 allow/observe/risky 상태를 빠르게 조회
- `SYS_SLM_NODEBIT_LOOKUP`로 유저스페이스 AI가 정책 상태를 직접 확인할 수 있는 읽기 경로 제공

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
- 정적 2개 bootstrap descriptor의 private CR3/run state/16KiB ring0 entry-stack ownership, PID 1→PID 2 순차 실행과 각 실행 사이/최종 BSP TSS `rsp0`·CR3·current owner 복원 증명
- `aios-init`, 디스크에서 적재되는 ELF, 두 process 선점/full trapframe, 동적 주소공간/PMM, long-running userspace AI runtime은 후속 구현 대상

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
| `0x720-0x725` | SLM/NodeBit | `SYS_SLM_HW_SNAPSHOT`, `SYS_SLM_PLAN_SUBMIT`, `SYS_SLM_PLAN_APPLY`, `SYS_SLM_NODEBIT_LOOKUP` |

## Project Structure

저장소는 6개 도메인으로 구성된 모노레포다. 도메인 경계·의존 규칙·"어디에 두나" 결정 가이드는
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
├── os/                 # ② 유저스페이스 런타임 + 전용 프로그램
│   ├── runtime/  main_ai/  compat/  examples/  tools/
│   └── apps/           #    전용 프로그램 (스캐폴드)
│
├── models/             # ③ AI/SLM 모델 매니페스트 (가중치는 비추적)
│   └── manifests/
├── store/              # ④ 부팅 후 온라인 드라이버/프로그램 다운로드 카탈로그
│   └── catalog/
├── tools/              # ⑤ 테스트툴 + 빌드 오케스트레이션
│   └── testkit/
├── docs/               # ⑥ 설계 문서 (kernel/ autonomy/ os/ models/ tools/ meta/ kernel-room/)
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
python .\tools\testkit\aios-testkit.py all --strict
```

Windows용 자세한 설치 및 경로 설정 방법은 [docs/tools/windows_build.md](docs/tools/windows_build.md)를 참고하세요.

### Python Testkit

Claude/Codex 작업과 CI에서 쓰는 주요 진입점은 `tools/testkit/aios-testkit.py`입니다.

```powershell
python .\tools\testkit\aios-testkit.py all --strict
python .\tools\testkit\aios-testkit.py kernel --target test --strict
python .\tools\testkit\aios-testkit.py boot-matrix --profiles full minimal storage-only --strict
python .\tools\testkit\aios-testkit.py boot-inventory --profiles full minimal storage-only --strict
python .\tools\testkit\aios-testkit.py boot-perf --profiles full minimal storage-only --strict
python .\tools\testkit\aios-testkit.py shell --strict
python .\tools\testkit\aios-testkit.py shell --strict --skip-build
python .\tools\testkit\aios-testkit.py os
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
| AI 가속기 | PCI/capability abstraction scaffold, 실제 벤더 드라이버 예정 |
| CI | GitHub Actions (cppcheck + OS tool smoke + QEMU smoke + shell lane) |

## Planning Documents

전체 설계 문서 색인은 **[docs/README.md](docs/README.md)**, 저장소 도메인 구조는
**[PROJECT.md](PROJECT.md)** 를 참고하세요. 주요 문서:

- [자율 OS 실행 로드맵](docs/autonomy/autonomous_os_execution_roadmap_ko.md)
- [AI 친화 리소스 관리 개발 계획 (2026-04-27)](docs/autonomy/ai_resource_management_development_plan_ko.md)
- [최소 I/O 점검과 성숙도 우선 작업흐름](docs/meta/minimal_io_and_maturity_workflow_ko.md)
- [OLD 문서 체크리스트](docs/meta/old_docs_check_2026_07_03_ko.md)
- [유저공간 OS 구현 방향](docs/os/user_space_os_direction_ko.md)
- [브라우저 콘솔과 자체 런타임 엔진 로드맵](docs/os/browser_console_and_runtime_engine_roadmap_ko.md)
- [AI 에이전트 OS용 모델 스택 추천](docs/models/agent_model_stack_recommendations_ko.md)
- [종합 점검 보고서 (2026-04-15)](docs/meta/inspection_report_2026_04_15.md)
- [Kernel Room Topology 문서 모음](docs/kernel-room/README.md)
- [OS 계층 소개](os/README.md)

## License

MIT License

## Acknowledgments

이 프로젝트는 AI 워크로드에 최적화된 운영체제의 가능성을 탐구하기 위한 실험적 프로젝트입니다.
