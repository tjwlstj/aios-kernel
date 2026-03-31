# 커널-유저 경계 최적화 우선순위 정리

이 문서는 현재 AIOS 코드베이스를 기준으로, 멀티 AI 에이전트 병렬 실행을 위해 무엇을 커널에 남기고 무엇을 유저 공간 런타임으로 올려야 하는지 정리한다.

## 결론

외부 분석에서 제안된 방향 중 상당수는 유효하다. 다만 지금 AIOS의 가장 큰 병목은 `알고리즘 부재`보다 `커널-유저 경계 비용`, `메모리 공유 방식`, `비동기 제출/완료 경로 미정립`에 있다.

즉 현재 우선순위는 다음과 같다.

1. `ring3 + shared memory UAPI + submit/completion ring`
2. `page allocator backend 정리 (buddy)`
3. `descriptor / metadata slab`
4. `futex 또는 wait-queue 기반 completion`
5. `실시간 추론 lane에 한정한 EDF`
6. `유저 공간 모델 런타임 최적화`

## 현재 코드 기준 판단

### `mm/tensor_mm.c`

- 현재 구현은 텐서 풀 위의 정적 블록 테이블과 best-fit 탐색 성격이 강하다.
- 향후에는 페이지 단위 backend에 buddy를 두고, 텐서 allocator는 그 위에서 shape / alignment / lifetime 정책만 담당하는 계층으로 분리하는 편이 맞다.
- slab은 텐서 데이터 자체보다 `KV slot descriptor`, `CQE`, `submission entry metadata`, `shared window descriptor` 같은 반복 객체에 먼저 적용하는 것이 효과적이다.

### `sched/ai_sched.c`

- 현재 CFS/MLFQ 성격은 이미 충분한 기반이 있다.
- 다음 단계는 전체 스케줄러 교체보다 `실시간 추론 lane`에만 EDF heap을 붙여 deadline-sensitive task를 분리하는 것이다.
- DRF/WFQ는 장기적으로 유효하지만, GPU 시간, DMA, 메모리 대역폭 accounting이 먼저 있어야 의미가 있다.

### `hal/accel_hal.c`

- 지금 구조에서 가장 큰 개선 지점은 syscall 중심 동기 제출을 줄이는 것이다.
- 커널과 유저 공간이 공유 메모리 기반 submit/completion ring으로 연결되면 모델 제출 오버헤드와 완료 polling 비용을 동시에 줄일 수 있다.
- 이 부분은 장치 드라이버보다 먼저 들어가도 전체 구조에 이득이 크다.

### `runtime/ai_syscall.c`

- 현재 dispatcher는 기능적으로는 충분하지만, 고빈도 추론 요청을 처리하기에는 trap 중심 구조다.
- 이후에는 `control syscall`과 `data plane ring`을 분리해야 한다.
- 즉 시스콜은 등록/설정/문 진입에만 쓰고, 실제 infer submit / completion은 공유 링으로 넘기는 방향이 적합하다.

### `os/runtime`

- Flash Attention, INT8/INT4 matmul, quantized KV-cache, Winograd 같은 최적화는 커널보다 유저 공간 런타임에 두는 것이 맞다.
- 커널은 메모리, 제출, 완료, health, isolation, DMA, wait primitive를 제공하고, 실제 모델 실행기는 `aios-modeld`, `aios-kvcached`, `aios-agentd`가 담당해야 한다.

## 커널에 남길 것

- page allocator backend
- DMA-safe memory
- shared memory region registration
- submit/completion ring
- wait queue / futex-like sleep-wake
- health gate
- agent-aware scheduling hook
- zero-copy memory fabric

## 유저 공간으로 올릴 것

- 모델 format loader
- MatMul / Attention runtime
- batching policy
- Flash Attention / quantized kernel selection
- KV-cache tiering policy
- TurboQuant / kvtc orchestration
- agent supervisor / node orchestration

## 권장 실행 순서

### 1단계: 경계 비용 절감

- ring3 진입 기반
- shared memory registration
- submit/completion ring
- inference wait object

### 2단계: 메모리 backend 정리

- buddy page allocator
- huge page 분기
- slab cache for descriptors
- DMA / shared / hotset tier 구분

### 3단계: 스케줄링 정밀화

- EDF lane for realtime inference
- completion-driven wakeup
- accelerator-aware queue depth 제어

### 4단계: 유저 공간 runtime 확장

- `aios-init`
- `aios-agentd`
- `aios-modeld`
- `aios-kvcached`
- native ELF lane
- WASI component lane

## 왜 이 순서가 맞는가

지금 AIOS 목표는 `멀티 AI 에이전트를 병렬로 띄우되, 오버헤드를 커널에서부터 줄이는 것`이다. 이 목표에서 가장 먼저 줄여야 하는 비용은 모델 연산 알고리즘의 FLOP보다 `주소 공간 경계`, `복사`, `동기화`, `제출/완료` 경로다.

따라서 지금 시점의 핵심은 다음 한 줄로 요약된다.

`더 빠른 연산기`보다 먼저 `더 싼 경계`를 만든다.

이후에야 Flash Attention, quantized matmul, KV compression 같은 최적화가 구조 위에 자연스럽게 올라간다.
