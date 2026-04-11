# AIOS 코드 경계 가이드와 변경 가능한 구조 트리

작성일: 2026-04-12

## 목적

이 문서는 AIOS가 당분간 `C + x86_64 Assembly` 중심 커널을 유지한다는 전제에서,
지금 코드 사용성을 높이고 나중에 구조를 바꾸기 쉽게 만드는 최소 기준을 정리한다.

핵심은 다음 두 가지다.

1. 지금은 언어를 늘리지 않는다.
2. 대신 코드 경계와 디렉토리 책임을 먼저 고정한다.

즉, 이 문서는 "새 언어 도입 계획"이 아니라
"현재 C 커널을 덜 아프게 유지하면서 이후 유저 공간/서비스 계층으로 분리하기 위한
경계 문서"다.

## 현재 구현 기준 현실 체크

2026-04-12 기준 실제 저장소에서 확인되는 상태는 다음과 같다.

- 커널 핵심 구현 언어는 `C + x86_64 Assembly`다.
- `boot/`, `interrupt/`, `kernel/`, `mm/`, `sched/`, `hal/`, `drivers/`, `runtime/`가
  실제 부팅 경로와 연결된다.
- `os/`는 상위 OS 계층 설계와 도구를 위한 분리 공간이지만,
  아직 실제 ring3 서비스가 올라가는 단계는 아니다.
- 따라서 지금 당장 좋은 방향은 "언어 교체"보다
  "경계 정리 + 파일 책임 고정 + 나중에 옮기기 쉬운 구조화"다.

## 지금 바로 적용할 코드 사용성 규칙

### 1. 부팅/아키텍처 종속 코드는 아래에만 둔다

- `boot/`
- `interrupt/`
- `kernel/`의 초기화 진입점

이 영역은 다음 특성을 가진다.

- x86_64 레지스터, GDT/TSS/IDT, paging, trap frame과 직접 연결
- 부트 순서와 강하게 결합
- 다른 언어로 나누기 가장 어려운 영역

즉, 이 영역은 당분간 `C + asm` 고정 영역으로 보는 편이 맞다.

### 2. 하드웨어 접근 책임은 driver/HAL에만 둔다

- MMIO/PIO/config-space 접근
- BAR 탐색
- controller bootstrap
- capability dump

이 로직은 다음 파일군 밖으로 퍼지지 않는 것이 좋다.

- `drivers/`
- `hal/`
- 일부 `kernel/` 초기화 glue 코드

`runtime/`나 미래 `os/` 서비스에서 장치를 만질 때는
직접 레지스터를 건드리지 않고,
반드시 syscall/UAPI/plan apply를 통하도록 유지한다.

### 3. 공용 경계는 헤더에서 먼저 고정한다

지금 AIOS는 구현보다 "경계가 먼저 흔들리는" 위험이 더 크다.
그래서 다음 순서가 좋다.

1. `include/`에 경계 타입과 함수 계약을 먼저 둔다
2. `.c` 구현은 그 계약을 만족하는 최소 범위만 넣는다
3. 로그/summary/testkit이 보는 필드는 가급적 안정적으로 유지한다

특히 다음은 쉽게 바꾸지 않는 편이 좋다.

- syscall 번호
- enum 값
- boot log marker
- testkit이 읽는 요약 필드

### 4. driver 인터페이스는 패턴을 통일한다

현재 AIOS의 최소 드라이버들은 대체로 다음 패턴을 갖고 있다.

- `*_init()`
- `*_ready()`
- `*_info()`
- `*_dump()`

이 패턴은 계속 유지하는 편이 좋다.
이유는 단순하다.

- 부팅 경로에서 쓰기 쉽다
- SLM action으로 묶기 쉽다
- 나중에 유저 공간 서비스에서 상태를 읽기 쉽다
- testkit이 로그와 summary를 만들기 쉽다

즉, "기능을 더 넣는 것"보다 먼저 "형태를 통일하는 것"이 사용성에 더 이롭다.

### 5. 정책과 메커니즘을 분리한다

현재 AIOS 목적은 멀티 AI 에이전트/SLM/LLM orchestration이다.
이 구조에서는 특히 다음 분리가 중요하다.

- 커널: 메커니즘
- OS 계층: 정책

예시:

- 커널은 `queue depth`, `poll budget`, `DMA window` 같은 힌트를 계산
- OS 계층은 그 힌트를 보고 어떤 agent/node/model을 어디에 배치할지 결정

즉, 커널은 "할 수 있는 수단"을 제공하고,
OS 계층은 "어떻게 쓸지"를 결정한다.

### 6. 실험 코드는 `os/`와 `tools/`에 먼저 둔다

지금 당장 커널에 넣지 않아도 되는 것들은 먼저 아래에 두는 편이 안전하다.

- 학습용 도구
- 데이터셋 정리기
- 메인 AI 정책 초안
- node tree 매니페스트
- SDK/compat 설계 문서

그 다음 실제 ring3가 생기면
그 중 필요한 것만 user-space service로 끌어올린다.

## 추천 디렉토리 책임

아래는 현재 구조를 기준으로 한 "책임 지도"다.
지금 당장 파일을 모두 옮기자는 뜻은 아니다.
우선은 이 지도를 기준으로 파일을 배치하고,
나중에 이동이 필요할 때도 이 기준을 유지하자는 뜻이다.

### 현재 유지 영역

```text
boot/        = 엔트리, GDT/TSS, paging, Multiboot2
interrupt/   = 예외/인터럽트 진입, IDT, trap glue
kernel/      = 초기화 순서, health, time, user-mode scaffold
mm/          = 텐서 메모리, memory fabric, 향후 VMM 기초
sched/       = 커널 스케줄링 메커니즘
hal/         = 가속기/장치 추상화
drivers/     = NIC/USB/storage/VGA/serial/PCI bootstrap
runtime/     = syscall surface, autonomy, SLM orchestrator
include/     = 커널/드라이버/UAPI 계약
testkit/     = 빌드/부팅/요약/회귀 검증
os/          = 미래 유저 공간 OS 계층 설계/도구
```

### 변경 가능한 목표 트리

아래 트리는 "미래 목표 구조"다.
일부 디렉토리는 아직 없다.

```text
aios-kernel/
├── boot/                    # asm + 초기 부트 고정 영역
├── interrupt/               # trap/irq 진입 고정 영역
├── kernel/                  # init/health/time/core glue
├── mm/                      # allocator, object map, future VMM
├── sched/                   # scheduler mechanism
├── hal/                     # accel/device abstraction
├── drivers/
│   ├── net/                 # e1000, future nic drivers
│   ├── usb/                 # usb host bootstrap, future transfer path
│   ├── storage/             # ide/ahci/nvme bootstrap, future read path
│   ├── console/             # vga, serial, future framebuffer log
│   └── bus/                 # pci/acpi glue if later split is needed
├── runtime/
│   ├── uapi/                # syscall dispatch and versioned ABI glue
│   ├── autonomy/            # policy gate entry points
│   └── slm/                 # snapshot/orchestrator/plan surface
├── include/
│   ├── aios/uapi/           # 유저 공간에 공개할 ABI 헤더
│   ├── kernel/internal/     # 커널 내부 전용 헤더
│   └── drivers/             # 드라이버 공용 헤더
├── os/
│   ├── runtime/             # native user-space runtime 설계/구현
│   ├── compat/              # WASI/OCI/ELF compat lane
│   ├── services/            # aios-init, aios-osd, modeld 같은 데몬
│   ├── sdk/                 # 상위 앱/에이전트용 헤더와 샘플
│   ├── main_ai/             # 메인 AI profile/node tree
│   ├── tools/               # 학습/정리/변환 도구
│   └── examples/            # 샘플 config/trace
└── testkit/                 # build, smoke, inventory, perf, fault
```

## 지금은 "가상 트리"로 운영하는 편이 좋다

위 목표 트리를 지금 바로 실제 디렉토리 이동으로 강행하면,
현재 저장소에서는 오히려 비용이 커질 수 있다.

이유:

- include 경로가 한 번에 많이 바뀐다
- Makefile과 testkit이 동시에 흔들린다
- 실질 성능보다 리팩터링 비용이 먼저 든다

그래서 현재는 다음 원칙이 적절하다.

1. 실제 파일 이동은 최소화한다
2. 대신 새 파일은 목표 트리의 책임을 의식하고 배치한다
3. 파일 수가 2~3개 이상 모이는 영역만 디렉토리 분리를 시작한다

즉, 지금은 "문서상의 구조 트리"를 먼저 운영 규칙으로 삼고,
실제 이동은 파일 수가 쌓일 때만 하는 편이 좋다.

## 추천 분리 순서

### 1단계. 헤더 경계 먼저 분리

가장 먼저 가치가 큰 것은 `include/` 역할 분리다.

추천 목표:

- `include/aios/uapi/`
- `include/kernel/internal/`
- `include/drivers/`

이렇게 되면 미래에 Rust/Zig/C++을 일부 도입하더라도
공개 ABI와 내부 구현을 나누기 쉬워진다.

### 2단계. driver 하위 분류는 파일 수가 늘 때만

지금은 `drivers/`가 평평해도 괜찮다.
하지만 다음쯤 되면 나눌 가치가 생긴다.

- NIC가 2개 이상
- storage controller가 2개 이상
- USB host/data path 파일이 분리될 때
- framebuffer/console이 추가될 때

그 시점에만 `drivers/net`, `drivers/storage`, `drivers/usb`, `drivers/console`로 나눈다.

### 3단계. `runtime/`는 UAPI와 control plane으로 분리

현재 `runtime/`는 아직 작아서 한 디렉토리로 버틸 수 있다.
하지만 다음이 늘어나면 분리 가치가 생긴다.

- syscall entry
- shared ring/completion ABI
- autonomy verifier
- slm plan/action surface

그때는 아래처럼 역할별로 나누는 편이 좋다.

- `runtime/uapi`
- `runtime/autonomy`
- `runtime/slm`

### 4단계. 실제 user-space가 생기면 `os/services`를 연다

ring3 handoff 전에는 `os/`를 설계와 도구 중심으로 유지한다.
실제 `aios-init`가 뜨기 전까지는
데몬 디렉토리를 무리하게 늘리지 않는 편이 낫다.

즉, `os/services/`는 다음 시점에 여는 게 자연스럽다.

- static ELF loader가 생김
- 첫 번째 user-space service가 실제로 부팅됨

## 언어를 나중에 추가하더라도 흔들리지 않는 기준

나중에 Rust/Zig 같은 언어를 일부 도입하더라도,
다음 기준을 지키면 충격이 적다.

1. 커널 진입부는 계속 `C + asm`
2. 공개 경계는 C ABI 우선
3. 새 언어는 `os/` 또는 user-space lane부터
4. hot path는 언어보다 데이터 배치와 알고리즘 우선
5. testkit marker와 boot summary 필드는 안정적으로 유지

즉, 언어가 늘어나도 구조가 먼저 흔들리면 안 된다.

## 이번 가이드 기준의 최소 후속 작업

가장 작은 다음 조각은 아래 셋 중 하나면 충분하다.

1. `include/aios/uapi`와 `include/kernel/internal` 경계 초안 만들기
2. `drivers/` 하위 분리 없이 파일 주석에 ownership 규칙 추가하기
3. `os/services`는 아직 만들지 않고, `os/README.md`에 planned lane만 명시하기

## 한 줄 판단

지금 AIOS는 "언어를 바꾸는 시기"보다
"코드가 어디까지 커널이고 어디부터 OS/service인지 경계를 고정하는 시기"에 가깝다.

그래서 당장 가장 좋은 선택은
`C 커널 유지 + 경계 문서화 + 목표 트리 합의 + 점진 분리`다.
