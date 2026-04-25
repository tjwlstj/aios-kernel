# AIOS - Kernel-first AI-Native Operating System

<p align="center">
  <strong>Embodied LLM/SLM runtime을 위한 x86_64 베어메탈 커널 실험</strong>
</p>

---

## Overview

AIOS(AI-Native Operating System)는 AI 워크로드를 **1급 시민(First-class citizen)**으로 취급하는 커널 우선형 x86_64 베어메탈 OS 실험입니다. 현재 베타는 부팅 가능한 커널 기반, 텐서 지향 메모리 메타데이터, 메모리 패브릭, 헬스 스냅샷, SLM 하드웨어 스냅샷, NodeBit 정책 게이트, 제한된 AI 시스콜 표면을 중심으로 발전하고 있습니다.

장기 방향은 embodied AI OS입니다. LLM/SLM 에이전트는 유저스페이스에서 단기 기억과 장기 기억을 분리해 유지하고, 세션을 넘어 연속성을 보존하며, 하드웨어에는 커널이 중재하는 정책 경계를 통해 접근합니다.

이 저장소는 아직 범용 상용 OS가 아닙니다. 유저스페이스 handoff, ELF 로더, 영속 기억 런타임, 실시간 학습 승격 루프는 설계/구현 예정 영역이며, 현재 트리는 커널 기반과 호스트 도구, QEMU 스모크 테스트 가능한 스캐폴딩을 제공합니다.

## GitHub Description

Suggested repository description:

> Kernel-first AI-native OS experiment for embodied LLM/SLM runtime: memory fabric, continuity roadmap, NodeBit policy gates, and mediated hardware access.

## Current Status (2026-04-25)

- **Current beta:** `v0.2.0-beta.6` (`0.2.0-beta.6 "Genesis"` boot banner).
- **Boot path:** x86_64 Multiboot2 커널, GDT/IDT/TSS, 페이징, PIT IRQ0 scheduler tick bootstrap, QEMU 스모크 테스트 기반.
- **Memory:** 물리/가상 할당 기반, 텐서 메모리 메타데이터, 수명 프로파일링, 메모리 패브릭 노드, 공유 영역 스캐폴딩.
- **Autonomy and policy:** 헬스 스냅샷, 제한된 자율 제안/롤백 경로, SLM 하드웨어 스냅샷, NodeBit 정책 조회.
- **Userspace:** ring3 경계와 user access helper는 존재하지만, 전체 유저스페이스 handoff와 ELF 로더는 아직 없음.
- **Hardware AI access:** 가속기 인터페이스는 추상화/탐색 스캐폴딩 단계. 실제 GPU/NPU/TPU 드라이버와 직접 클럭 제어 백엔드는 계획 상태.
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
│  - user access guards and QEMU smoke-test hooks                │
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
- 유저 포인터 copy/validate 경로와 부트스트랩 snapshot ABI
- 전체 유저스페이스 handoff, ELF loader, long-running userspace AI runtime은 후속 구현 대상

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

```
aios-kernel/
├── os/                 # 상위 OS 계층 (메인 AI, 학습 도구, 노드 트리)
├── boot/               # Multiboot2 부트 어셈블리
│   └── boot.asm        # x86_64 엔트리포인트, GDT, 페이징, SSE/AVX, BSS 초기화
├── kernel/             # 커널 코어
│   ├── main.c          # kernel_main 엔트리포인트
│   ├── kernel_room.c   # Kernel Room snapshot / gate foundation
│   ├── user_access.c   # 유저 포인터 검증/copy helper
│   └── linker.ld       # 링커 스크립트
├── interrupt/          # 인터럽트 처리
│   ├── idt.c           # IDT 설정, 예외 핸들러, kernel_panic
│   └── isr_stub.asm    # ISR 어셈블리 스텁 (32개 예외 + legacy PIC IRQ)
├── lib/                # 커널 라이브러리
│   └── string.c        # memset, memcpy, strlen 등 기본 유틸리티
├── mm/                 # 텐서 메모리 관리자
│   ├── tensor_mm.c     # Best-fit 할당, 풀 관리, 수명 프로파일링
│   └── memory_fabric.c # 멀티 에이전트 공유/zero-copy 메모리 기반
├── sched/              # AI 워크로드 스케줄러
│   └── ai_sched.c      # MLFQ, CFS, 데드라인 스케줄링
├── hal/                # 가속기 HAL
│   └── accel_hal.c     # PCI 스캔, 디바이스 추상화
├── runtime/            # AI 런타임
│   ├── ai_syscall.c    # 시스콜 디스패처, 모델 레지스트리
│   ├── autonomy.c      # 자율 제어 평면, 정책 엔진
│   └── slm_orchestrator.c # SLM snapshot, plan gate, NodeBit policy
├── drivers/            # 디바이스 드라이버
│   ├── vga.c           # VGA 텍스트 모드 콘솔
│   ├── serial.c        # COM1 시리얼 콘솔 (115200 8N1)
│   ├── e1000.c         # e1000 bring-up / smoke path
│   ├── storage_host.c  # storage probe scaffold
│   └── usb_host.c      # USB/xHCI probe scaffold
├── include/            # 헤더 파일
├── docs/               # 설계 문서
├── scripts/            # 정적 점검/검증 스크립트
├── testkit/            # 호스트/부트/QEMU 검증 도구
├── .github/workflows/  # CI/CD 파이프라인
├── build/              # 빌드 출력 (자동 생성)
└── Makefile            # 빌드 시스템
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
pwsh -File .\testkit\kernel\build-windows.ps1 -Target all
pwsh -File .\testkit\kernel\build-windows.ps1 -Target test
python .\testkit\aios-testkit.py all --strict
```

Windows용 자세한 설치 및 경로 설정 방법은 [docs/windows_build.md](docs/windows_build.md)를 참고하세요.

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
| 유저스페이스 | ring3/user access scaffold, full handoff 예정 |
| AI 가속기 | PCI/capability abstraction scaffold, 실제 벤더 드라이버 예정 |
| CI | GitHub Actions (빌드 + QEMU 스모크 테스트) |

## Planning Documents

- [자율 OS 실행 로드맵 (2026-04-11)](docs/autonomous_os_execution_roadmap_ko.md)
- [종합 점검 보고서 (2026-04-15)](docs/inspection_report_2026_04_15.md)
- [AI 네이티브 OS GitHub/공개 사례 정리 (2026-04-21)](docs/ai_native_os_github_landscape_ko.md)
- [AI 에이전트 자율 OS 요건 정리](docs/ai_agent_autonomous_os_requirements_ko.md)
- [상용 OS 안정성 기준선](docs/commercial_stability_baseline_ko.md)
- [SLM 자율 운영/최적화 구조 계획](docs/slm_autonomous_kernel_plan.md)
- [SLM 하드웨어 온보딩 방향](docs/slm_hardware_onboarding_ko.md)
- [SLM 학습/최적화 방향](docs/slm_learning_optimization_ko.md)
- [Driver Model / Stack Foundation (2026-04-10)](docs/driver_model_foundation_ko.md)
- [Enum 무결성 + 저레벨 SLM 정렬 기록 (2026-04-10)](docs/enum_and_lowlevel_slm_alignment_ko.md)
- [점검 및 부족한 점 리포트 (2026-03-22)](docs/inspection_and_gaps_ko.md)
- [현재 커널 부족점과 외부 기준 정리 (2026-03-29)](docs/current_kernel_gap_report_ko.md)
- [AI 에이전트 OS용 모델 스택 추천 (2026-03-28)](docs/agent_model_stack_recommendations_ko.md)
- [정적-혼돈 연산자 기반 메인 AI/트리 아키텍처 (2026-03-28)](docs/static_chaos_agent_architecture_ko.md)
- [유저 공간 OS 구조 및 호환성 설계 (2026-03-29)](docs/user_space_compat_architecture_ko.md)
- [유저공간 OS 구현 방향 정리 (2026-04-19)](docs/user_space_os_direction_ko.md)
- [유저공간 OS 세분화 빌드 계획 (2026-04-19)](docs/user_space_os_build_slices_ko.md)
- [테스트 툴링 구조와 올인원 도구 (2026-03-29)](docs/test_tooling_ko.md)
- [Testkit 분리/세분화 가이드 (2026-04-10)](docs/testkit_guide_ko.md)
- [Gemini CLI 활용 방향 정리 (2026-03-30)](docs/gemini_cli_usage_strategy_ko.md)
- [Gemini CLI 1차 검토 기록 (2026-03-30)](docs/gemini_cli_first_review_ko.md)
- [멀티 AI 에이전트용 Memory Fabric 기초안 (2026-03-30)](docs/multi_agent_memory_fabric_foundation_ko.md)
- [커널 엔트로피용 노이즈 소스 정리 (2026-03-30)](docs/kernel_entropy_noise_sources_ko.md)
- [커널-유저 경계 최적화 우선순위 정리 (2026-03-31)](docs/kernel_user_boundary_optimization_ko.md)
- [코드 경계 가이드와 변경 가능한 구조 트리 (2026-04-12)](docs/code_boundary_and_structure_tree_ko.md)
- [유기적 커널 구조 정리 (2026-04-12)](docs/organic_kernel_structure_ko.md)
- [메모리 병렬처리 최적화 정리 (2026-04-12)](docs/memory_parallel_optimization_ko.md)
- [Kernel Room Topology 문서 모음](docs/kernel-room/README.md)
- [Kernel Room Topology 정리 (2026-04-18)](docs/kernel-room/kernel_room_topology_ko.md)
- [Kernel Room Topology 개발 가이드 (2026-04-18)](docs/kernel-room/development_guide_ko.md)
- [Orbit-Cell Node Model 구현 가능성 검토 (2026-04-12)](docs/kernel-room/orbit_cell_node_feasibility_ko.md)
- [OS 계층 소개](os/README.md)

## License

MIT License

## Acknowledgments

이 프로젝트는 AI 워크로드에 최적화된 운영체제의 가능성을 탐구하기 위한 실험적 프로젝트입니다.
