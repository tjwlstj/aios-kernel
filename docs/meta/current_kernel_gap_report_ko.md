# AIOS 현재 커널 부족점과 외부 기준 정리 (2026-03-29)

## 목적

이 문서는 현재 AIOS 커널이 어디까지 와 있는지, 그리고 다음 단계 구현에서 어떤 외부 표준/기술을 기준으로 삼아야 하는지를 함께 정리한다.

기준은 다음 두 축이다.

- 현재 저장소 코드가 실제로 제공하는 것
- 해당 영역에서 따라야 할 공식 표준 또는 사실상 표준

## 현재 상태 한 줄 요약

AIOS 커널은 `부팅`, `기본 드라이버 bootstrap`, `AI syscall 인터페이스`, `SLM snapshot`, `health gate`, `OS 계층 설계 분리`까지는 진전했지만, 아직은 `실행 가능한 AI 전용 커널 프로토타입`에 가깝다. 범용 OS나 완전한 AI appliance OS로 보기 위해서는 아래 부족점을 우선적으로 메워야 한다.

## 1. 가장 큰 부족점

### 1) 유저 모드 / 프로세스 / 실행 ABI 부재

현재 커널은 초기화 후 idle loop에 들어가며, 진짜 유저 공간 프로그램을 실행하지 않는다. `runtime/ai_syscall.c`에는 AI syscall 디스패처가 있지만, 실제 `ring3 -> kernel` 진입 경로, TSS, syscall entry, ELF loader, process address space는 아직 없다.

관련 코드:

- `kernel/main.c`
- `runtime/ai_syscall.c`
- `interrupt/idt.c`

왜 중요한가:

- 메인 AI와 상위 OS 계층은 커널보다 유저 공간에서 돌아야 안정성과 업데이트성이 높다.
- native user-space ABI가 없으면 `aios-init`, `aios-agentd`, `aios-modeld`, `aios-kvcached` 같은 유저 공간 데몬을 실제로 띄울 수 없다.

외부 기준:

- Intel SDM은 운영체제 지원 환경에 `memory management`, `protection`, `task management`, `interrupt and exception handling`을 포함한다고 설명한다. 즉 현재 AIOS가 갖춘 예외 처리만으로는 유저 공간 실행 기반이 완성되지 않는다.
  - [Intel SDM landing page](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html)
- x86_64 user-space ABI의 기준점은 `x86-64 psABI`다.
  - [x86-64 psABI 공식 저장소](https://gitlab.com/x86-psABIs/x86-64-ABI)

판단:

- 다음 커널 1순위는 `ring3 + TSS + syscall entry + static ELF loader + aios-init`이다.

### 2) 실제 모델 런타임 부재

현재 `SYS_MODEL_LOAD`, `SYS_INFER_SUBMIT`, `SYS_INFER_WAIT`는 존재하지만, 실제 모델 파일 import, graph 실행, attention kernel, completion/result 전달이 완성되어 있지 않다.

관련 코드:

- `runtime/ai_syscall.c`
- `hal/accel_hal.c`
- `mm/tensor_mm.c`

현재 상태:

- 모델 로드는 메타데이터 등록과 메모리 할당에 가깝다.
- inference submit은 scheduler task 생성 수준이다.
- wait는 실제 blocking/완료 대기가 아니다.
- `accel_attention()`은 아직 stub이다.

왜 중요한가:

- AIOS의 정체성은 결국 `모델 실행과 메모리/장치 최적화`에서 드러난다.
- syscall surface만 있고 실행기가 없으면 상위 OS 설계가 실제 워크로드를 처리하지 못한다.

외부 기준:

- ONNX IR는 computation graph, standard data types, built-in operators를 정의하는 open specification이다.
  - [ONNX IR Specification](https://onnx.ai/onnx/repo-docs/IR.html)
- ONNX 문서는 IR이 inference뿐 아니라 training도 지원한다고 명시한다. 즉 AIOS는 장기적으로 inference-first로 시작하더라도, import 포맷은 ONNX를 기준으로 잡는 편이 호환성에 유리하다.

판단:

- 다음 런타임 1순위는 `ONNX import -> internal graph lowering -> attention / KV path -> completion`이다.

### 3) 메모리 관리가 정적 풀 + identity map 중심

현재 Tensor MM은 AI 지향 설계라는 장점이 있지만, 기본 구조는 고정 크기 메모리 풀과 identity mapping에 머물러 있다.

관련 코드:

- `include/mm/tensor_mm.h`
- `mm/tensor_mm.c`
- `kernel/linker.ld`

현재 상태:

- tensor/model/inference/DMA pool 크기가 컴파일 타임 고정이다.
- 텐서 `virt_addr`가 `phys_addr`와 동일하다.
- 가상메모리 분리, 유저 공간 보호, page fault 기반 복구가 없다.
- `kv_cache_resize()`도 아직 실제 구현이 아니다.

왜 중요한가:

- user-space OS로 가려면 최소한 kernel/user 분리 주소공간이 필요하다.
- KV-cache 계층화, TurboQuant/kvtc, zero-copy object map도 결국 메모리 객체와 주소공간 정책 위에서 안정적으로 돌아야 한다.

외부 기준:

- Intel SDM은 OS support environment 안에 protection과 memory management를 포함한다.
  - [Intel SDM landing page](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html)
- ONNX IR는 모델을 portable serialized graph로 정의하고, 구현체가 내부 메모리 표현을 다르게 가져갈 수 있다고 설명한다. 즉 import 포맷과 실행 메모리 표현은 분리해야 한다.
  - [ONNX IR Specification](https://onnx.ai/onnx/repo-docs/IR.html)

판단:

- `identity mapped kernel`에서 `object-backed VMM`으로 넘어가야 한다.

### 4) 스케줄러 타이머 IRQ는 연결됐지만 실제 선점/문맥전환은 미완성

2026-04-25 기준으로 스케줄러 자체는 구조가 잘 잡혀 있고, `ai_sched_tick()`은 QEMU에서 관찰 가능한 PIT IRQ0 경로에 연결됐다. 다만 이 연결은 아직 tick/accounting 단계이며, 실제 실행 컨텍스트를 저장/복구하는 선점형 context switch는 없다.

관련 코드:

- `sched/ai_sched.c`
- `interrupt/idt.c`
- `interrupt/isr_stub.asm`
- `kernel/time.c`

현재 상태:

- monotonic time source는 생겼다.
- scheduler tick 함수도 있다.
- `kernel_timer_irq_init()`이 PIT를 100Hz 주기 모드로 설정하고 legacy PIC IRQ0을 IDT vector 32로 remap한다.
- `interrupt/isr_stub.asm`과 `interrupt/idt.c`가 legacy PIC IRQ 32~47 스텁을 등록하며, IRQ0은 `kernel_timer_irq_handler()`를 통해 `ai_sched_tick()`을 호출한다.
- QEMU smoke는 `[TIMER] PIT IRQ ready` 로그를 필수 checkpoint로 확인한다.
- 실제 task stack/context 저장, userspace/ring3 전환, 선점형 dispatch는 아직 없다.

왜 중요한가:

- 스케줄러가 자료구조에서 실제 실행기 단계로 넘어가려면 preemption source가 필요하다.
- inference latency, deadline miss, fairness 수치가 진짜 의미를 가지려면 하드웨어 시간과 연결되어야 한다.

외부 기준:

- Intel SDM은 interrupt/exception handling, multi-processor support를 OS 핵심 영역으로 둔다.
  - [Intel SDM landing page](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html)

판단:

- `timer IRQ -> ai_sched_tick`은 bootstrap으로 연결됐다.
- 다음 core milestone은 `ai_sched_tick -> runnable task 선택 -> context switch`다.

### 5) 실기기 호환성이 아직 4GiB 이하 물리주소 전제

현재 부트, ACPI, PCI 코어는 실용적으로 잘 동작하지만, 물리주소 4GiB 이하 전제가 남아 있다.

관련 코드:

- `boot/boot.asm`
- `kernel/acpi.c`
- `drivers/pci_core.c`

현재 상태:

- 초기 identity map은 첫 4GiB 중심이다.
- ACPI 테이블 접근은 `< 4GiB`일 때만 안전하다고 본다.
- ECAM도 MCFG base가 4GiB 미만일 때만 활성화한다.

왜 중요한가:

- 최신 UEFI 시스템에서는 XSDT/MCFG가 4GiB فوق에 있을 수 있다.
- 지금 구조는 그런 시스템에서 조용히 legacy path로 후퇴하거나 정보를 놓칠 수 있다.

외부 기준:

- UEFI Forum은 최신 ACPI 규격을 6.6까지 제공하고 있으며, ACPI는 OS-directed configuration and power management의 핵심 표준이다.
  - [UEFI Specifications page](https://uefi.org/specifications)
- ACPI FAQ는 ACPI가 standardized device discovery, OS configuration, power management를 제공하며 OSPM이 플랫폼 제어를 담당한다고 설명한다.
  - [UEFI ACPI FAQ](https://uefi.org/faq)

판단:

- `4GiB barrier 제거 + dynamic mapping window`는 실기기 호환성의 핵심 과제다.

### 6) 드라이버는 아직 bootstrap 수준

현재 e1000, USB, storage는 모두 bootstrap/init/smoke에 강하고, 실제 data path는 제한적이다.

관련 코드:

- `drivers/e1000.c`
- `drivers/usb_host.c`
- `drivers/storage_host.c`

현재 상태:

- e1000은 지원되는 Intel NIC 후보 중 부트스트랩 호환성 점수가 높은 장치를 고른 뒤, RX descriptor ring bootstrap + bounded RX poll/rearm + TX smoke까지 간다.
- USB는 여러 host controller 중 부트스트랩 호환성 점수가 높은 후보를 고른 뒤, xHCI capability probe 수준까지 간다.
- storage는 여러 controller 중 부트스트랩 호환성 점수가 높은 후보를 고른 뒤, IDE channel live/status 또는 AHCI/NVMe 분류 수준까지 간다.
- 다만 일반 패킷 수신 스택, interrupt 기반 completion, storage read/write, USB transfer ring은 아직 부족하다.

왜 중요한가:

- 상위 OS 계층이 메모리, 모델, KV-cache를 실사용하려면 결국 I/O가 살아야 한다.
- 장기기억, checkpoint, model import, cache offload가 모두 storage/network 의존이다.

판단:

- `e1000 일반 RX 경로 -> storage read -> xHCI transfer`가 현재 드라이버 1순위다.

## 2. 유저 공간 OS 계층 기준에서 추가로 필요한 것

### 1) libc / POSIX-lite 전략

처음부터 Linux ABI 전체를 복제하는 대신, `musl 친화 POSIX-lite`를 목표로 하는 편이 현실적이다.

근거:

- musl은 lightweight, fast, simple, standards-conformance와 safety를 강조한다.
  - [musl libc](https://musl.libc.org/)

권장:

- `read/write/clock/poll/mmap-lite/thread/TLS` 우선
- `fork/signal/full execve semantics`는 후순위

### 2) Component sandbox lane

모든 agent node를 native로 돌리기보다, 일부는 WASI component로 격리하는 것이 맞다.

근거:

- Component Model은 interoperable WebAssembly libraries, applications, and environments를 위한 구조다.
  - [Component Model introduction](https://component-model.bytecodealliance.org/)
- WIT world는 컴포넌트가 제공/요구하는 경계를 명시하며, 이 경계가 강한 sandboxing을 제공한다.
  - [Worlds](https://component-model.bytecodealliance.org/design/worlds.html)
  - [WIT reference](https://component-model.bytecodealliance.org/design/wit.html)
- WASI 0.2.0은 stable set of WIT definitions다.
  - [Component Model introduction](https://component-model.bytecodealliance.org/)
  - [WASI interfaces](https://wasi.dev/interfaces)

권장:

- 메인 AI와 고성능 모델 서비스는 native
- verifier/summarizer/tool adapters는 WASI component

## 3. 권장 구현 순서

### Phase 1

- ring3
- TSS
- syscall entry
- static ELF loader
- `aios-init`

### Phase 2

- kernel/user address space 분리
- page fault 경로
- object-backed mapping
- async completion primitive

### Phase 3

- ONNX import lane
- internal graph lowering
- real inference wait / completion
- attention / KV-cache runtime

### Phase 4

- timer IRQ 기반 preemption
- e1000 일반 RX 경로
- storage read
- xHCI transfer ring

### Phase 5

- WASI host
- component plugins
- OCI-like bundle import

## 4. 최종 판단

현재 AIOS의 가장 큰 부족점은 "아이디어 부족"이 아니라 `커널 기반을 유저 공간 OS로 연결하는 마지막 고리`가 아직 없다는 점이다.

정리하면:

- 커널은 이미 부팅/계측/초기 드라이버/SLM 기반을 갖췄다.
- 다음 단계는 `유저 공간 실행 ABI`, `진짜 모델 런타임`, `VMM`, `타이머 기반 스케줄링`, `실사용 I/O`다.
- 이 순서를 지키면 메인 AI + 하위 노드 트리 구조를 실제로 올릴 수 있다.
