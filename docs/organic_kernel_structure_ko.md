# AIOS 유기적 커널 구조 정리

작성일: 2026-04-12

## 목적

이 문서는 AIOS 커널이 "기능이 많은 커널"이 아니라
"각 구성요소가 상태와 이벤트를 통해 서로 유기적으로 연결되는 커널"로 가기 위해
필요한 구조를 정리한다.

핵심 질문은 이거다.

- 부팅 후 각 서브시스템이 따로 초기화되고 끝나는가
- 아니면 관측, 판단, 적용, 검증, 복구의 순환 구조로 이어지는가

AIOS가 메인 AI + 하위 노드 + SLM/LLM orchestration을 목표로 하는 만큼,
후자의 구조가 필요하다.

## 현재 구현 기준 현실 체크

2026-04-12 기준 실제 저장소에서 확인되는 현실은 다음과 같다.

- `kernel/main.c`는 부팅, selftest, PCI/device bootstrap, health, SLM orchestrator까지는
  순차적으로 초기화한다
- `runtime/autonomy.c`와 `runtime/slm_orchestrator.c`는 정책/추천/계획의 틀을 제공하지만,
  아직 userspace supervisor가 붙은 폐쇄 루프는 아니다
- `drivers/e1000.c`, `drivers/usb_host.c`, `drivers/storage_host.c`는
  bootstrap/dump/기초 smoke 중심이며, 장치 수명주기가 통합된 상태 머신으로 묶여 있지는 않다
- `sched/ai_sched.c`는 구조는 괜찮지만 아직 timer IRQ와 직접 연결된 실행 루프가 아니다
- `runtime/ai_syscall.c`는 syscall surface를 제공하지만,
  실제 ring3 caller와 object/wait 중심 운영 구조는 아직 없다

즉, 지금 AIOS는 "좋은 부품을 여러 개 가진 커널"에 가깝고,
아직 "부품들이 하나의 순환 구조로 묶인 커널"은 아니다.

## 유기적 작동의 최소 정의

AIOS에서 "유기적 작동"은 아래 순환이 성립하는 상태를 뜻한다.

```text
관측 -> 상태화 -> 판단 -> 적용 -> 검증 -> 복구
```

각 단계의 의미:

1. 관측
   - 하드웨어, 메모리, 스케줄, 작업 상태를 읽는다
2. 상태화
   - 읽은 값을 subsystem/object 상태로 정리한다
3. 판단
   - 커널 또는 userspace policy가 다음 행동을 결정한다
4. 적용
   - 허용된 액션만 실제로 실행한다
5. 검증
   - 결과가 기대와 맞는지 확인한다
6. 복구
   - 실패하면 degraded/fallback/retry로 이어간다

이 구조가 없으면,
각 서브시스템은 "초기화 성공/실패"에서 멈추고
멀티 AI 에이전트 운영체제로 발전하기 어렵다.

## AIOS에 필요한 핵심 구조

### 1. 통합 상태 모델

가장 먼저 필요한 것은 "모든 중요한 대상을 상태가 있는 객체로 보는 관점"이다.

대상:

- device
- memory object
- scheduler entity
- model object
- inference request
- autonomy plan
- SLM orchestration target

최소 상태 예시:

```text
DISCOVERED -> BOOTSTRAP -> READY -> DEGRADED -> FAILED -> RETRY_WAIT
```

중요한 점:

- 단순 `bool ready`로는 부족하다
- 상태 전이 이유(reason)와 마지막 검증 결과가 함께 남아야 한다
- health gate와 SLM plan도 이 상태 모델을 읽을 수 있어야 한다

### 2. 이벤트 / 완료 기반 연결

현재 AIOS는 초기화 함수 호출 중심 구조가 강하다.
유기적으로 작동하려면 함수 호출만으로는 부족하고,
이벤트와 completion을 기준으로 연결돼야 한다.

필요한 이벤트 예시:

- device discovered
- driver bootstrap done
- health degraded
- scheduler pressure high
- model load complete
- inference request done
- storage offload complete

필요한 완료 primitive 예시:

- wait handle
- completion queue
- doorbell/eventfd 유사 wakeup
- shared ring submit/completion

이 구조가 생기면 커널 내부와 userspace가 느슨하게 연결될 수 있다.

### 3. 핸들 / 객체 기반 UAPI

커널이 더 유기적으로 작동하려면,
외부와의 경계도 pointer 중심보다 handle/object 중심으로 가는 편이 좋다.

필요한 이유:

- ABI 안정성
- userspace supervisor와의 연결 용이성
- language/runtime 확장 대비
- wait/completion 모델과의 결합

최소 객체 종류:

- device handle
- memory object handle
- model handle
- queue handle
- wait handle
- plan handle

즉, 커널 내부는 복잡할 수 있어도
경계는 작고 안정적인 객체 모델로 보여야 한다.

### 4. 드라이버 라이프사이클 레지스트리

지금 드라이버들은 각각 `*_init`, `*_ready`, `*_dump` 패턴을 갖고 있다.
이건 좋은 시작점이지만,
다음 단계로는 공통 lifecycle registry가 필요하다.

필요한 공통 항목:

- device identity
- bootstrap state
- controller kind
- selected candidate 정보
- last init status
- degraded reason
- retry 가능 여부
- dump callback
- health 반영 규칙

이 구조가 있으면:

- 네트워크/USB/storage가 같은 health 규칙을 탄다
- SLM plan이 장치별 예외 처리가 아니라 공통 모델을 읽을 수 있다
- boot inventory와 실기기 비교가 쉬워진다

### 5. 타이머 기반 실행 루프

스케줄러와 커널 제어가 유기적으로 작동하려면,
결국 시간원이 실제 실행 루프와 이어져야 한다.

최소 연결:

```text
timer irq -> tick/accounting -> scheduler decision -> deferred work/completion
```

여기서 중요한 건:

- 단순 tick 존재보다 "누가 언제 재평가되는가"가 중요하다
- inference latency, timeout, retry budget, poll budget이 시간과 연결돼야 한다
- autonomy/SLM도 static snapshot만이 아니라 time-based reevaluation이 가능해야 한다

### 6. health / rollback / repair 루프

AIOS 목적상 panic-only 구조는 맞지 않는다.
유기적 커널은 실패를 다음처럼 다뤄야 한다.

```text
READY -> DEGRADED -> FALLBACK -> RECHECK -> RECOVERED
```

필요한 요소:

- subsystem health score
- required/optional 구분
- fallback path
- retry policy
- recovery evidence
- risky apply 차단

즉, health는 단순 보고가 아니라
"복구 루프를 여는 제어면"이어야 한다.

### 7. 커널 메커니즘 / userspace 정책 분리

AIOS는 AI orchestration OS를 목표로 하므로,
커널이 모든 판단을 직접 하면 금방 엉킨다.

권장 분리:

- 커널
  - 시간원
  - 드라이버
  - memory object
  - scheduler mechanism
  - wait/completion
  - health gate
  - safe actuator
- userspace
  - 메인 AI supervisor
  - agent tree orchestration
  - 모델 정책
  - KV/cache tier policy
  - recovery policy
  - tool/plugin integration

즉, 커널은 "살아있는 기반"이 되고,
상위 정책은 userspace가 담당하는 편이 전체 시스템이 더 유기적으로 움직인다.

## AIOS 기준 최소 구조도

```text
hardware
  -> probe/bootstrap
  -> lifecycle registry
  -> health/state model
  -> scheduler/time loop
  -> uapi object/handle layer
  -> userspace supervisor
  -> model/agent/kv services
  -> policy feedback
  -> safe action apply
  -> hardware
```

이 구조의 핵심은
"위에서 결정하고 아래에서 실행한 뒤, 다시 상태가 위로 올라오는 것"이다.

## 지금 AIOS에 가장 먼저 필요한 조각

우선순위를 너무 넓히지 않으면,
현재 저장소 기준 최소 순서는 이렇다.

### 1. ring3 handoff + `aios-init`

이게 없으면 policy와 supervisor가 커널 밖으로 올라갈 수 없다.

### 2. object / handle / wait 중심 UAPI

지금의 syscall surface를
실행 가능한 userspace 경계로 끌어올리는 데 필요하다.

### 3. driver lifecycle registry

이미 존재하는 `e1000` / `usb_host` / `storage_host`를
공통 상태 모델로 묶는 첫 단계다.

### 4. timer IRQ -> scheduler 실연결

정적인 부팅 기반을 실제 실행 기반으로 바꾸는 핵심이다.

### 5. 실제 데이터 경로 1개

다음 중 하나만 먼저 살아도 구조가 훨씬 유기적으로 된다.

- e1000 일반 RX
- storage read
- xHCI transfer

### 6. userspace policy supervisor

SLM/autonomy recommendation을
실제 폐쇄 루프로 만드는 핵심이다.

## 지금 당장 하지 않는 편이 좋은 것

다음은 아직 이르다.

- 커널 전체 언어 전환
- 너무 이른 대규모 디렉토리 이동
- full POSIX 호환 욕심
- 모든 드라이버를 한 번에 실사용 단계로 확장
- 메인 AI 정책을 커널 내부에 과도하게 넣는 것

이런 선택은 구조를 유기적으로 만들기보다
오히려 결합도를 높일 가능성이 크다.

## 한 줄 판단

AIOS 커널이 더 유기적으로 작동하려면
"더 많은 기능"보다 먼저
`상태 모델 + 이벤트/completion + 핸들 UAPI + 복구 루프 + userspace 정책 분리`
가 필요하다.
