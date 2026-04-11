# AIOS 유저 공간 OS 아키텍처 설계

작성일: 2026-03-29

## 목적

AIOS는 부팅 가능한 AI 전용 커널로 빠르게 진전했지만, 메인 AI와 하위 노드 트리, 모델 서비스, KV-cache 관리, 온라인 학습은 커널 내부보다 유저 공간 OS 계층에서 동작하는 편이 안정성과 호환성 면에서 훨씬 유리하다.

이 문서는 `커널 부팅 이후 유저 공간에서 동작하는 AIOS`를 목표로, 기반기술 조사와 호환성 중심 설계를 정리한다.

## 현재 구현 기준 현실 체크

이 문서는 목표 아키텍처를 설명하는 문서다. 현재 저장소 구현은 아직 그 단계에 도달하지 않았다.

2026-04-12 기준으로 실제 코드에서 확인되는 현실은 다음과 같다.

- `kernel/main.c`는 모든 초기화 후 `hlt` idle loop로 들어간다
- boot path에는 user code/data selector와 TSS를 준비하는 최소 scaffold가 들어갔고,
  boot log에는 `[USER] Ring3 scaffold ready=1` marker가 남는다
- `runtime/ai_syscall.c`는 syscall surface와 ring 상태 통계를 제공하지만,
  아직 ring3 caller가 실제로 진입하는 경로는 없다
- `runtime/autonomy.c`와 `runtime/slm_orchestrator.c`는 커널 내부 control plane으로는 의미가 있지만,
  아직 `aios-init`나 userspace supervisor가 이를 호출하는 단계는 아니다
- 따라서 이 문서의 `aios-init`, `aios-osd`, `aios-agentd` 등은 현재 구현이 아니라
  "ring3 handoff 이후에 올릴 대상"이다

구현 기준 체크포인트와 병행 추진안은
[gemini_driver_userspace_checkpoint_ko.md](./gemini_driver_userspace_checkpoint_ko.md)
를 참고한다.

## 왜 유저 공간인가

커널은 다음에 집중해야 한다.

- 시간원, 스케줄링, 메모리, DMA, 인터럽트, 드라이버, 안전한 액추에이터
- 버전드 UAPI
- zero-copy, shared memory, completion/event primitive

반대로 다음은 유저 공간에 두는 편이 낫다.

- 메인 AI 상태모델
- 하위 에이전트 트리
- 장기기억 / KV-cache tier policy
- 모델 로더 / 형식 변환 / 양자화 / 압축
- 온라인 학습 / adapter 관리 / 관측 파이프라인
- 정책 데몬, 플러그인, 외부 툴 통합

이 분리는 다음 이점을 준다.

- 커널 크기와 crash surface 축소
- 메인 AI/학습 로직의 빠른 업데이트
- 포맷/런타임/언어 호환성 확보
- 실험 기능을 샌드박스에서 격리

## 기반기술 조사 요약

### 1. x86_64 ELF + System V psABI

AIOS는 현재 x86_64 베어메탈 커널이므로, 첫 번째 native 유저 공간 ABI는 ELF64 + x86_64 System V ABI가 가장 자연스럽다. 이 조합은 C/C++/Rust/Zig 툴체인 호환성이 높고, 장기적으로 `musl` 계열 libc와도 잘 맞는다.

권장 판단:

- 최초 native 유저 공간 ABI는 `x86_64 ELF + SysV ABI`
- 사용자 프로그램은 PIE를 기본값으로 가정
- 초기에는 동적 링커보다 정적 링크와 단순 ELF loader를 우선

참고:

- [x86-64 psABI 공식 저장소](https://gitlab.com/x86-psABIs/x86-64-ABI)

### 2. musl libc

`musl`은 Linux syscall API 위의 C 표준 라이브러리 구현이지만, 경량/단순/표준 준수 지향이라는 특성 때문에 AIOS의 첫 libc 호환 목표로 적합하다. 중요한 점은 AIOS가 처음부터 Linux ABI를 전부 복제할 필요는 없고, `musl 친화적인 POSIX-lite`를 목표로 삼는 편이 현실적이라는 점이다.

권장 판단:

- `glibc 호환`보다 `musl 친화 POSIX-lite`를 먼저 목표로 한다
- `fork`, signals, `mmap` 전체 semantics를 초기에 다 흉내내지 않는다
- `read/write/poll/clock/getrandom/mmap-lite/thread-local` 계열부터 우선 지원한다

참고:

- [musl libc](https://musl.libc.org/)

### 3. WASI 0.2 + WebAssembly Component Model

WASI는 언어 중립적이고 안전한 컴포넌트 실행 계층을 제공한다. 특히 Bytecode Alliance의 Component Model은 WIT 기반 인터페이스를 통해 언어 간 호환성과 플러그인 구성을 단순화한다.

AIOS에서 WASI는 다음에 적합하다.

- 플러그인형 agent node
- 검증기 / 요약기 / 메모리 정리기 / 라우터 같은 소형 워커
- 외부 툴 연결용 sandbox

권장 판단:

- native ELF lane와 별개로 `portable component lane`을 둔다
- 메인 AI는 native 런타임 우선
- 하위 노드와 플러그인은 WASI component 우선

참고:

- [WASI.dev 소개](https://wasi.dev/)
- [WebAssembly Component Model](https://component-model.bytecodealliance.org/)

### 4. OCI Runtime / Image Spec

OCI는 운영체제 프로세스와 애플리케이션 컨테이너 표준을 정의한다. AIOS는 처음부터 Linux 컨테이너를 완전 구현할 필요는 없지만, `OCI bundle`을 배포 단위로 수용해두면 toolchain, registry, 배포 자동화와의 호환성이 크게 좋아진다.

권장 판단:

- 초기는 `OCI-like application bundle`
- 나중에 `OCI image import + unpack + manifest 실행`
- Linux namespace/cgroup 호환을 바로 목표로 하지 않는다

참고:

- [OCI Runtime Specification](https://github.com/opencontainers/runtime-spec)
- [OCI Image Specification](https://github.com/opencontainers/image-spec)

### 5. ONNX IR

모델 형식은 장기적으로 하나만 고집하기보다 표준 interchange format을 먼저 잡는 것이 호환성에 유리하다. ONNX IR는 런타임 독립적인 computation graph, 타입, 연산자 세트를 정의하며, inference와 training 모두를 다룬다.

권장 판단:

- 외부 모델 유입 포맷의 1순위는 `ONNX`
- AIOS 내부 실행 포맷은 별도 최적화 representation을 사용해도 된다
- 변환기 계층에서 ONNX -> AIOS internal graph로 내린다

참고:

- [ONNX IR Specification](https://onnx.ai/onnx/repo-docs/IR.html)

## 설계 원칙

1. 커널 UAPI는 작고 안정적으로 유지한다.
2. 유저 공간 기능 확장은 native와 component 두 경로를 동시에 허용한다.
3. POSIX 전체를 흉내내지 않고, AIOS 목적에 맞는 POSIX-lite를 만든다.
4. 모델/에이전트/기억 서비스는 데몬화해서 장애를 격리한다.
5. 메인 AI는 native fast path, 하위 노드는 sandbox-first로 배치한다.

## 목표 아키텍처

```text
boot -> kernel -> ring3 handoff -> aios-init (PID1)
                                 -> aios-osd
                                 -> aios-modeld
                                 -> aios-memd
                                 -> aios-kvcached
                                 -> aios-agentd
                                 -> aios-compatd
                                 -> main-ai supervisor
                                 -> worker SLM/LLM nodes
```

### 커널 영역

- 부팅, 인터럽트, 타이머, ACPI/PCI, DMA, 드라이버
- Tensor MM, KV allocation primitives
- AI syscall / SLM snapshot / health / autonomy gate
- zero-copy shared memory
- event/completion primitive

### 유저 공간 코어 데몬

#### `aios-init`

- PID1 역할
- early user-space 초기화
- 기본 namespace/handle table/session root 준비
- 다른 코어 데몬 시작

#### `aios-osd`

- 유저 공간 control plane
- 정책, 설정, 서비스 생명주기
- 메인 AI 부팅 순서 관리

#### `aios-modeld`

- 모델 import / validate / convert / load
- ONNX import
- 내부 최적화 graph 생성
- accelerator별 materialization 관리

#### `aios-memd`

- 장기기억 journal
- adapter/LoRA artifact
- memory index
- cold object store

#### `aios-kvcached`

- KV-cache hot/warm/cold tier manager
- TurboQuant / kvtc 적용 policy
- prefix reuse, offload, hydrate 관리

#### `aios-agentd`

- 메인 AI supervisor
- 하위 노드 트리 관리
- static-chaos operator 계산
- planner / verifier / summarizer / tool worker orchestration

#### `aios-compatd`

- native ELF loader
- WASI component host
- OCI bundle launcher
- capability broker

## 호환성 중심 실행 레인

### A. Native Lane

대상:

- 메인 AI supervisor
- 모델 서비스
- KV manager
- 성능 민감한 SLM/LLM worker

형식:

- ELF64
- x86_64 SysV ABI
- 정적 링크 우선
- 향후 PIE + shared object 지원

이유:

- 가장 낮은 런타임 오버헤드
- DMA, shared memory, zero-copy와 결합이 쉬움

### B. Portable Component Lane

대상:

- verifier
- summarizer
- memory distiller
- plugin tool adapters
- untrusted helper nodes

형식:

- WASI 0.2 component
- WIT 인터페이스 기반

이유:

- 언어 중립성
- 배포 단순화
- sandbox와 capability 제어 용이

### C. Bundle Lane

대상:

- 외부 서비스 패키징
- reproducible deployment
- agent runtime distribution

형식:

- OCI bundle 우선
- image import는 후속 단계

이유:

- 기존 registry/CI/CD 생태계 활용
- 운영 배포 표준과의 접점 확보

### D. Model Lane

대상:

- 모델 import / interchange

형식:

- ONNX 우선
- 내부 graph/weight layout은 별도 최적화

이유:

- 특정 런타임 종속성 감소
- 변환기와 검증기 분리가 쉬움

## AIOS용 POSIX-lite 범위

초기 목표는 `Linux clone`이 아니라 `AI runtime-friendly POSIX-lite`다.

초기 포함:

- 파일 디스크립터 유사 handle
- `read`, `write`, `close`
- `clock_gettime` 유사 시간 API
- `poll` 또는 completion wait
- `mmap-lite`
- thread creation / join
- TLS 기본 지원
- shared memory map
- random / entropy

후순위:

- `fork`
- `execve` 전체 semantics
- signals 전체 모델
- tty/job control
- 완전한 `/proc`, `/sys`

## 커널 UAPI 설계 방향

현재 AI syscall 범위는 이미 잘 잡혀 있다. 이를 `versioned UAPI`로 정리해 유저 공간 ABI로 고정해야 한다.

필수 원칙:

- 구조체 크기와 필드 버전 명시
- 포인터 대신 handle/offset 사용 확대
- shared memory ring + completion doorbell 제공
- long-running request는 async submit/wait 기본

권장 신규 UAPI:

- `SYS_EXEC_ELF`
- `SYS_MAP_OBJECT`
- `SYS_WAIT_HANDLE`
- `SYS_CHANNEL_CREATE`
- `SYS_KV_OBJECT_CREATE`
- `SYS_KV_OBJECT_TRANSITION`
- `SYS_CAP_GRANT`

## 메모리 / KV-cache 계층

커널은 allocator와 pin/DMA/map만 담당하고, 정책은 유저 공간으로 올린다.

계층:

- `HOT`: live attention 직결, 무압축 또는 저손실 active quant
- `WARM`: 재사용 가능, TurboQuant 계열
- `COLD`: 저장/전송 중심, kvtc 계열
- `REMOTE`: 원격 prefix cache 또는 distributed store

운영 규칙:

- 메인 AI의 현재 thought window와 최근 토큰은 HOT 우선
- 반복 prefix와 긴 대화 문맥은 WARM/COLD로 이동
- hydrate/dehydrate는 `aios-kvcached`가 결정
- 커널은 zero-copy map, DMA move, storage offload만 제공

## 보안 / 안정성

메인 AI는 권한이 크지만 무제한이어선 안 된다.

원칙:

- 메인 AI는 supervisor 권한을 가지되, 커널 health gate를 우회하지 못한다
- 하위 노드는 capability-scoped sandbox에서 실행한다
- device plan apply는 `guardian/verifier + kernel health gate`를 통과해야 한다
- 온라인 학습은 adapter/journal 우선, base weights 직접 수정은 후순위

## 부팅 이후 시퀀스

1. 커널이 driver/health/SLM snapshot까지 초기화
2. ring3 전환 후 `aios-init` 시작
3. `aios-osd`와 `aios-compatd`가 기본 실행 환경 구성
4. `aios-modeld`, `aios-memd`, `aios-kvcached` 시작
5. `aios-agentd`가 메인 AI profile과 agent tree 로드
6. 메인 AI supervisor 실행
7. 하위 노드들을 native 또는 WASI component로 순차 기동

## 구현 우선순위

### Phase 1. 진짜 유저 공간 만들기

- ring3 전환
- GDT/TSS/user stack
- 최소 page permission 분리
- `aios-init` ELF loader

### Phase 2. Native ABI와 libc 호환층

- x86_64 SysV ABI 준수
- static ELF loader
- POSIX-lite syscall shim
- musl-friendly CRT start

### Phase 3. Component Lane

- WASI 0.2 host
- WIT 기반 AIOS host functions
- verifier/summarizer/tool worker부터 component화

### Phase 4. Core Daemons

- `aios-modeld`
- `aios-memd`
- `aios-kvcached`
- `aios-agentd`

### Phase 5. Bundle / Model Compatibility

- OCI bundle import
- ONNX import/validate/convert
- internal optimized graph lowering

## 최종 판단

AIOS는 커널 안에서 모든 AI 기능을 끝내는 구조보다, `커널은 최소 고성능 기반`, `OS 유저 공간은 메인 AI와 서비스 계층`으로 가는 편이 맞다.

호환성 우선순위는 아래가 가장 현실적이다.

1. `x86_64 ELF + SysV ABI`
2. `musl 친화 POSIX-lite`
3. `WASI 0.2 component`
4. `OCI bundle`
5. `ONNX import lane`

즉, `부팅되는 AI 커널` 다음 단계는 `유저 공간에서 살아 움직이는 AIOS`여야 한다.
