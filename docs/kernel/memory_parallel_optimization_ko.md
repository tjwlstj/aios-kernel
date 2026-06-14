# AIOS 메모리 병렬처리 최적화 정리

작성일: 2026-04-12

## 목적

이 문서는 AIOS에서 "메모리 병렬처리 최적화"를 무엇으로 봐야 하는지,
그리고 현재 구현 기준에서 어떤 순서로 개선하는 것이 맞는지 정리한다.

여기서 말하는 병렬처리는 단순히 여러 스레드가 동시에 malloc을 호출하는 뜻이 아니다.
AIOS 기준으로는 다음을 함께 포함한다.

- 멀티 AI 에이전트가 같은 메모리 객체를 낮은 오버헤드로 공유하는 것
- short-term / realtime / DMA / KV-cache 경로가 서로 덜 간섭하는 것
- 복사보다 zero-copy와 shared window를 우선하는 것
- 제출/완료/대기 비용 때문에 메모리 경합이 증폭되지 않게 하는 것

즉, 이 문서의 초점은 `더 빠른 allocator` 하나보다
`멀티 에이전트 + 장치 + KV-cache` 전체 흐름에서 메모리를 병렬 친화적으로 만드는 구조다.

## 현재 구현 기준 현실 체크

2026-04-12 기준 실제 코드에서 확인되는 상태는 다음과 같다.

- `mm/tensor_mm.c`는 정적 블록 테이블과 region별 free list 위에서 동작한다
- `find_best_fit()`는 region free list를 선형 탐색한다
- tensor/model/inference/DMA pool은 컴파일 타임 고정 크기다
- `tensor->virt_addr = phys_addr`로, 아직 identity-mapped kernel 성격이 강하다
- `memory_fabric.c`는 domain, shared window, zero-copy 추천 프로필을 제공하지만,
  아직 진짜 userspace export나 completion 기반 병렬 실행 루프는 없다
- `tensor_mm`와 `memory_fabric` 모두 전역 배열/카운터 중심이라,
  장기적으로는 contention과 ownership 규칙이 더 필요하다

즉, 지금 AIOS 메모리 구조는
"AI 친화적인 분리와 힌트는 이미 있음"
단계까지는 왔지만,
"병렬 실행에 강한 메모리 backend"
단계까지는 아직 아니다.

## 지금 병목으로 보이는 지점

### 1. 할당 경로가 중앙화돼 있다

현재 `tensor_mm`는 다음 특성을 가진다.

- 정적 `block_pool`
- 정적 `tensor_table`
- region별 단일 free list head
- best-fit 선형 탐색

이 구조는 부팅과 단일 실행 흐름에는 단순하고 좋지만,
병렬 에이전트/worker가 늘어나면 중앙 metadata 경합 지점이 되기 쉽다.

### 2. 메타데이터와 데이터 경로가 충분히 분리되지 않았다

실제 AI 워크로드에서는 데이터 자체보다
descriptor / window / queue entry / completion entry가 더 자주 흔들릴 수 있다.

그런데 지금은 다음이 아직 명확히 분리돼 있지 않다.

- 큰 tensor backing memory
- 작은 metadata object
- 공유 윈도우 descriptor
- submit/completion descriptor

이 상태에서는 작은 객체 churn이 큰 데이터 경로까지 같이 방해할 수 있다.

### 3. 공유는 시작됐지만 병렬 실행 루프와 아직 덜 연결돼 있다

`memory_fabric`의 domain/window 구조는 방향이 좋다.
하지만 아직은 다음이 미구현이다.

- ring3 shared window export
- memory op completion
- wait handle 기반 wakeup
- memory pressure에 따른 재배치 루프

즉, 공유 구조는 있는데
"공유가 실제 병렬 처리 최적화로 이어지는 폐쇄 루프"는 아직 약하다.

### 4. 읽기 위주와 쓰기 위주 메모리의 분리가 약하다

AIOS에는 성격이 매우 다른 메모리가 공존한다.

- model weights: read-mostly
- KV-cache: read/write but locality 민감
- inference scratch: 짧고 churn 큼
- DMA staging: 장치 왕복 중심

이 성격 차이가 충분히 구조화되지 않으면,
메모리 병렬처리는 allocator 경쟁보다 먼저
캐시 오염과 불필요한 복사에서 손해를 본다.

## 최적화 원칙

### 1. 먼저 correctness, 그 다음 병렬성

진짜 병렬 최적화 전에 먼저 필요한 건
"누가 어떤 metadata를 소유하고 언제 바꾸는가"를 분명히 하는 것이다.

즉 다음이 선행돼야 한다.

- 명확한 ownership
- bounded critical section
- 추후 spinlock / wait queue 도입이 가능한 구조
- overflow와 accounting 안전성

### 2. 복사 제거가 락 미세 최적화보다 먼저다

AIOS 목적에서는 allocator 몇 cycle보다
복사와 staging, submit/wait 비용이 더 크다.

그래서 우선순위는 이렇다.

1. shared window
2. read-mostly 공유
3. DMA staging 분리
4. completion 기반 비동기화
5. 그 다음 allocator 미세 튜닝

### 3. 데이터와 metadata를 분리한다

큰 tensor backing과 작은 descriptor는 같은 성격이 아니다.

권장 분리:

- 큰 데이터: page / hugepage / region backend
- 작은 metadata: slab 또는 cacheline-aware descriptor cache

이렇게 해야 병렬 요청에서 작은 객체 churn이 큰 데이터 풀을 흔들지 않는다.

### 4. shared hotset과 private scratch를 분리한다

멀티 에이전트 병렬 실행에서 가장 중요한 구분은 이거다.

- shared hotset
- worker private scratch

공유해야 하는 건 적극적으로 공유하고,
짧게 쓰고 버리는 건 domain-local로 묶는 편이 좋다.

## 권장 구조

아래 구조는 지금 AIOS를 크게 부수지 않으면서,
병렬 메모리 처리에 맞게 키우기 위한 목표 구조다.

```text
page backend
  -> tensor frontend
  -> descriptor slabs
  -> memory domains
  -> shared windows
  -> submit/completion memory ops
  -> pressure telemetry
```

### 1. page backend

역할:

- page / hugepage 공급
- region별 기본 reserve
- DMA-safe page 구분

권장 방향:

- `best-fit only`에서 벗어나 page backend를 분리
- buddy 또는 page-run 기반 backend 위에 tensor allocator를 올림

즉, `Tensor MM`는 정책 계층으로 가고
밑에는 더 단순한 page backend를 두는 편이 맞다.

### 2. tensor frontend

역할:

- shape / dtype / alignment
- lifetime class
- region class
- tensor object metadata

이 계층은 "AI 친화 정책"을 유지하되,
실제 page sourcing은 backend에 위임하는 구조가 적절하다.

### 3. descriptor slab

먼저 slab을 붙일 가치가 큰 곳:

- shared window descriptor
- submit entry
- completion entry
- KV slot metadata
- wait object

즉, slab은 tensor 데이터보다
"작고 자주 생성/해제되는 제어 객체"부터 적용하는 것이 맞다.

### 4. domain-local scratch arena

worker/inference 경로에는
짧게 쓰고 버리는 버퍼가 많다.

그래서 다음 구조가 유효하다.

- `main` hotset
- `worker` scratch arena
- `memory` journal/cold path
- `device` staging path

이렇게 하면 short-term churn이 전역 풀 전체를 매번 흔들지 않는다.

### 5. read-mostly shared window

가장 먼저 분리해야 할 메모리는 다음이다.

- model weights
- prefix cache
- 일부 KV read-mostly 구간

이건 적극적으로 read-only shared window화하는 편이 좋다.
반대로 write-heavy scratch는 공유보다 분리하는 편이 낫다.

### 6. memory completion path

나중에는 메모리 관련 작업도 단순 함수 호출보다
completion 기반이 더 맞다.

예시:

- window export complete
- DMA staging complete
- offload / hydrate complete
- compact / evict complete

이 경로가 있어야 userspace SLM/policy가
메모리 병렬 최적화를 폐쇄 루프로 다룰 수 있다.

## 현재 코드 기준 추천 우선순위

### 1단계. 메모리 correctness와 ownership 정리

가장 먼저 필요한 것:

- `tensor_mm` / `memory_fabric` ownership 규칙 명시
- region/table/window accounting 점검
- 추후 lock 도입을 막지 않는 함수 경계 정리

이 단계는 성능보다 안정성 선행 작업이다.

### 2단계. page backend와 tensor frontend 분리

권장:

- page 공급 계층 분리
- huge page / DMA page 분기
- tensor allocator는 shape/lifetime/front metadata 중심으로 유지

이 단계가 되어야 병렬 처리 최적화가 allocator 미세 튜닝을 넘어서게 된다.

### 3단계. descriptor slab 도입

먼저 대상:

- window descriptor
- submission/completion descriptor
- wait object
- KV metadata

이건 상대적으로 리스크가 낮고,
병렬 churn을 줄이는 효과가 크다.

### 4단계. domain-local scratch + shared hotset

이 단계부터 실제 병렬 친화성이 눈에 띄게 좋아진다.

- worker scratch는 local
- model/prefix는 shared
- device staging은 분리

즉, "다 같이 하나의 풀을 두드리는 구조"에서 벗어난다.

### 5단계. ring3 export + completion 연결

이후에는 userspace runtime과 연결해야 한다.

- shared tensor window export
- submit/completion ring
- wait handle
- memory pressure feedback

이 구조가 생기면 메모리 병렬 최적화가
커널 내부 최적화에서 끝나지 않고
메인 AI / SLM policy까지 연결된다.

## 지금 당장 하지 않는 편이 좋은 것

다음은 아직 이르다.

- allocator 전체를 한 번에 새 구조로 갈아엎기
- NUMA/CXL 전제를 현재 기본 경로로 두기
- 커널 안에 Flash Attention류 런타임 최적화를 넣기
- 모든 메모리 경로를 lock-free로 만들려고 욕심내기

지금 단계에선
`올바른 계층화 + 복사 감소 + metadata 분리`
가 더 중요하다.

## 한 줄 판단

AIOS의 메모리 병렬처리 최적화는
`더 똑똑한 malloc` 하나의 문제가 아니라,
`page backend 분리 + shared hotset + domain-local scratch + completion 기반 메모리 경로`
를 만드는 구조 문제에 가깝다.
