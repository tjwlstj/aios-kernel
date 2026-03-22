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
│         (Model / Tensor / Infer / Train)            │
├──────────┬──────────┬──────────┬────────────────────┤
│  Tensor  │    AI    │  Model   │                    │
│  Memory  │ Workload │ Registry │  Kernel Core       │
│  Manager │Scheduler │          │                    │
├──────────┴──────────┴──────────┤                    │
│     Accelerator HAL            │  VGA Console       │
│  (GPU / TPU / NPU / CPU SIMD) │  Serial Driver     │
├────────────────────────────────┴────────────────────┤
│                   Hardware                          │
│   x86_64 CPU  │  NVIDIA GPU  │  AMD GPU  │  TPU    │
└─────────────────────────────────────────────────────┘
```

## Key Features

### Tensor Memory Manager
- 텐서 중심 메모리 할당 (64바이트 AVX-512 정렬)
- 용도별 메모리 풀 분리 (Model / Inference / DMA / KV-Cache)
- 2MB 거대 페이지(Huge Page) 지원으로 TLB 미스 최소화
- 참조 카운팅 기반 자동 메모리 해제

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

## Project Structure

```
aios-kernel/
├── boot/               # Multiboot2 부트 어셈블리
│   └── boot.asm        # x86_64 엔트리포인트, GDT, 페이징, SSE/AVX 활성화
├── kernel/             # 커널 코어
│   ├── main.c          # kernel_main 엔트리포인트
│   └── linker.ld       # 링커 스크립트
├── mm/                 # 텐서 메모리 관리자
│   └── tensor_mm.c     # Best-fit 할당, 풀 관리, KV-Cache
├── sched/              # AI 워크로드 스케줄러
│   └── ai_sched.c      # MLFQ, CFS, 데드라인 스케줄링
├── hal/                # 가속기 HAL
│   └── accel_hal.c     # PCI 스캔, 디바이스 추상화
├── runtime/            # AI 시스템 콜 인터페이스
│   └── ai_syscall.c    # 시스콜 디스패처, 모델 레지스트리
├── drivers/            # 디바이스 드라이버
│   └── vga.c           # VGA 텍스트 모드 콘솔
├── include/            # 헤더 파일
├── docs/               # 설계 문서
├── build/              # 빌드 출력 (자동 생성)
└── Makefile            # 빌드 시스템
```

## Build & Run

### Prerequisites
```bash
sudo apt install nasm gcc binutils qemu-system-x86 grub-pc-bin xorriso mtools
```

### Build
```bash
make all        # 커널 바이너리 빌드
make iso        # 부팅 가능한 ISO 이미지 생성
```

> 참고: 기본 컴파일러는 `gcc`이며, 다른 툴체인을 사용할 경우 `make CC=clang LD=ld.lld` 또는 `make TOOLCHAIN_PREFIX=x86_64-elf-` 형태로 지정할 수 있습니다.

### Run in QEMU
```bash
make run        # QEMU에서 커널 실행
make debug      # GDB 디버깅 모드로 실행
```

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
| 커널 크기 | ~39KB |

## Planning Documents

- [SLM 자율 운영/최적화 구조 계획](docs/slm_autonomous_kernel_plan.md)

## License

MIT License

## Acknowledgments

이 프로젝트는 AI 워크로드에 최적화된 운영체제의 가능성을 탐구하기 위한 실험적 프로젝트입니다.
