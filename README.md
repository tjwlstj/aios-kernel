# AIOS - AI-Native Operating System Kernel

<p align="center">
  <strong>커널 단위부터 AI를 위해 설계된 운영체제</strong>
</p>

---

## Overview

AIOS(AI-Native Operating System)는 인공지능 워크로드를 **1급 시민(First-class citizen)**으로 취급하는 실험적인 x86_64 베어메탈 운영체제 커널입니다. 기존 범용 OS가 파일 시스템과 프로세스 관리에 초점을 맞추는 반면, AIOS는 텐서 메모리 관리, AI 가속기 추상화, 모델 추론/학습 스케줄링에 집중합니다.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  User Space                         │
│   ┌───────────┐  ┌───────────┐  ┌──────────────┐   │
│   │ AI Agent  │  │ AI Agent  │  │  AIOS SDK    │   │
│   └─────┬─────┘  └─────┬─────┘  └──────┬───────┘   │
│         └───────────────┴───────────────┘           │
├─────────────────────────────────────────────────────┤
│              AI System Call Interface                │
│    (Model / Tensor / Infer / Train / Autonomy)      │
├──────────┬──────────┬──────────┬────────────────────┤
│  Tensor  │    AI    │ Autonomy │                    │
│  Memory  │ Workload │ Control  │  Kernel Core       │
│  Manager │Scheduler │  Plane   │                    │
├──────────┴──────────┴──────────┤                    │
│     Accelerator HAL            │  IDT / Exceptions  │
│  (GPU / TPU / NPU / CPU SIMD) │  VGA + Serial      │
├────────────────────────────────┴────────────────────┤
│                   Hardware                          │
│   x86_64 CPU  │  NVIDIA GPU  │  AMD GPU  │  TPU    │
└─────────────────────────────────────────────────────┘
```

## Key Features

### Tensor Memory Manager
- 텐서 중심 메모리 할당 (64바이트 AVX-512 정렬)
- 용도별 메모리 풀 분리 (Model / Inference / DMA / KV-Cache)
- 수명 기반 프로파일링 (SHORT_TERM / LONG_TERM / REALTIME / RANDOM)
- 2MB 거대 페이지(Huge Page) 지원으로 TLB 미스 최소화
- 참조 카운팅 기반 자동 메모리 해제

### Memory Fabric Foundation
- 멀티 AI 에이전트용 메모리 도메인(seed)과 공유 텐서 window 추적
- 복사보다 zero-copy / shared window 우선 정책
- ACPI / PCIe / selftest를 바탕으로 hotset / staging / worker 수 추천
- 미래의 NUMA / CXL 확장을 막지 않는 fallback-first 설계

### AI Workload Scheduler
- 다단계 피드백 큐 (Multi-level Feedback Queue)
- CFS 기반 공정 스케줄링 (Virtual Runtime 추적)
- 데드라인 인식 실시간 추론 스케줄링
- GPU/가속기 친화도(Affinity) 기반 작업 배치
- 선점형(Preemptive) 스케줄링 지원

### Accelerator HAL
- PCI 버스 자동 스캔으로 AI 가속기 탐색
- GPU/TPU/NPU 통합 추상화 인터페이스
- MatMul, Attention 등 AI 핵심 연산 API
- CPU SIMD(SSE/AVX) Fallback 지원

### Autonomy Control Plane
- L0~L3 자율 제어 레벨 (관찰 → 안전 적용 → 자율 최적화)
- 정책 제안/승인/적용/롤백 파이프라인
- 이벤트 로깅 및 텔레메트리 프레임 수집
- 스케줄러 액추에이터 통합 (우선순위 자동 조정)

### Interrupt & Exception Handling
- x86_64 IDT (Interrupt Descriptor Table) 완전 구현
- 32개 CPU 예외 핸들러 (Divide Error, Page Fault, GPF 등)
- kernel_panic() 안전 정지 메커니즘
- 시리얼 + VGA 이중 출력 디버깅

### AI System Call Interface
| 범위 | 카테고리 | 주요 시스콜 |
|------|----------|------------|
| `0x100-0x1FF` | 모델 관리 | `SYS_MODEL_LOAD`, `SYS_MODEL_UNLOAD` |
| `0x200-0x2FF` | 텐서 조작 | `SYS_TENSOR_CREATE`, `SYS_TENSOR_DESTROY` |
| `0x300-0x3FF` | 추론 | `SYS_INFER_SUBMIT`, `SYS_INFER_STREAM` |
| `0x400-0x4FF` | 학습 | `SYS_TRAIN_FORWARD`, `SYS_TRAIN_BACKWARD` |
| `0x500-0x5FF` | 가속기 | `SYS_ACCEL_LIST`, `SYS_ACCEL_SELECT` |
| `0x600-0x6FF` | 파이프라인 | `SYS_PIPE_CREATE`, `SYS_PIPE_EXECUTE` |
| `0x700-0x7FF` | 시스템 정보 | `SYS_INFO_MEMORY`, `SYS_INFO_SYSTEM` |
| `0x710-0x715` | 자율 제어 | `SYS_AUTONOMY_PROPOSE`, `SYS_AUTONOMY_ROLLBACK` |

## Project Structure

```
aios-kernel/
├── os/                 # 상위 OS 계층 (메인 AI, 학습 도구, 노드 트리)
├── boot/               # Multiboot2 부트 어셈블리
│   └── boot.asm        # x86_64 엔트리포인트, GDT, 페이징, SSE/AVX, BSS 초기화
├── kernel/             # 커널 코어
│   ├── main.c          # kernel_main 엔트리포인트
│   └── linker.ld       # 링커 스크립트
├── interrupt/          # 인터럽트 처리
│   ├── idt.c           # IDT 설정, 예외 핸들러, kernel_panic
│   └── isr_stub.asm    # ISR 어셈블리 스텁 (32개 예외)
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
│   └── autonomy.c      # 자율 제어 평면, 정책 엔진
├── drivers/            # 디바이스 드라이버
│   ├── vga.c           # VGA 텍스트 모드 콘솔
│   └── serial.c        # COM1 시리얼 콘솔 (115200 8N1)
├── include/            # 헤더 파일
├── docs/               # 설계 문서
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
pwsh -File .\scripts\build-windows.ps1 -Target all
pwsh -File .\scripts\build-windows.ps1 -Target test
python .\scripts\aios-allinone.py all --strict
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
| 텐서 정렬 | 64바이트 (AVX-512) |
| 최대 AI 작업 | 256개 동시 실행 |
| 최대 가속기 | 16개 디바이스 |
| 최대 모델 | 64개 동시 로드 |
| 커널 크기 | ~51KB |
| 소스 코드 | ~5,000줄 (C + ASM) |
| CI | GitHub Actions (빌드 + QEMU 스모크 테스트) |

## Planning Documents

- [SLM 자율 운영/최적화 구조 계획](docs/slm_autonomous_kernel_plan.md)
- [점검 및 부족한 점 리포트 (2026-03-22)](docs/inspection_and_gaps_ko.md)
- [현재 커널 부족점과 외부 기준 정리 (2026-03-29)](docs/current_kernel_gap_report_ko.md)
- [AI 에이전트 OS용 모델 스택 추천 (2026-03-28)](docs/agent_model_stack_recommendations_ko.md)
- [정적-혼돈 연산자 기반 메인 AI/트리 아키텍처 (2026-03-28)](docs/static_chaos_agent_architecture_ko.md)
- [유저 공간 OS 구조 및 호환성 설계 (2026-03-29)](docs/user_space_compat_architecture_ko.md)
- [테스트 툴링 구조와 올인원 도구 (2026-03-29)](docs/test_tooling_ko.md)
- [Gemini CLI 활용 방향 정리 (2026-03-30)](docs/gemini_cli_usage_strategy_ko.md)
- [Gemini CLI 1차 검토 기록 (2026-03-30)](docs/gemini_cli_first_review_ko.md)
- [멀티 AI 에이전트용 Memory Fabric 기초안 (2026-03-30)](docs/multi_agent_memory_fabric_foundation_ko.md)
- [OS 계층 소개](os/README.md)

## License

MIT License

## Acknowledgments

이 프로젝트는 AI 워크로드에 최적화된 운영체제의 가능성을 탐구하기 위한 실험적 프로젝트입니다.
