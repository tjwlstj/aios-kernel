# 커널 엔트로피용 노이즈 소스 정리

## 목적

이 문서는 `AIOS`에서 커널 난수생성기(`kernel RNG`)를 설계할 때 고려해야 할 노이즈의 종류, 생성 방식, 수집 가능한 소스, 그리고 신뢰하면 안 되는 소스를 구조적으로 정리하기 위한 메모이다.

이 문서의 목적은 다음 두 가지다.

- `노이즈`를 `타이머`가 아니라 `엔트로피 소스`로 분리해서 보기
- 현재 AIOS 코드베이스에서 당장 수집 가능한 소스와, 나중에 붙일 소스를 구분하기

이번 정리는 `Gemini CLI`로 1차 조사한 결과를 바탕으로, 현재 저장소 구조에 맞게 다시 구조화한 내용이다.

## 핵심 원칙

먼저 전제를 분명히 해야 한다.

- 노이즈는 좋은 `시간 기준(clock)`이 아니다
- 노이즈는 좋은 `엔트로피 후보(source of entropy)`가 될 수 있다
- raw noise를 바로 난수로 쓰면 안 된다
- 반드시 `수집 -> 혼합 -> 상태 판정 -> DRBG seed/reseed` 구조로 가야 한다

즉 AIOS에는 장기적으로 아래 세 층이 분리되어야 한다.

- `time source`
- `entropy source`
- `deterministic random generator (DRBG)`

## 노이즈의 종류

커널 난수생성기 관점에서 볼 수 있는 노이즈는 크게 5가지다.

### 1. 시간 지터 계열

이건 어떤 이벤트가 **정확히 언제 발생하는지의 미세한 흔들림**이다.

예:

- TSC 측정 잔차
- 인터럽트 도착 간격의 흔들림
- I/O 완료 시점의 미세한 변동
- polling loop 탈출 시점의 미세 차이

이 종류는 커널이 가장 쉽게 수집할 수 있다.

### 2. 장치/버스 지연 계열

이건 CPU 밖에서 일어나는 버스/장치 수준의 변동이다.

예:

- PCIe 트랜잭션 지연
- DMA 완료 시점 변동
- 저장장치 상태 레지스터 변화 타이밍
- USB 컨트롤러 응답 지연
- NIC TX/RX 완료 지터

하드웨어, 펌웨어, 버스 상태, 전력 상태에 따라 흔들린다.

### 3. 메모리/캐시 경쟁 계열

이건 동일한 메모리 계층을 여러 주체가 공유하면서 생기는 변동이다.

예:

- cache line 충돌
- DRAM 접근 지연 변화
- memcpy/memset/memmove의 사이클 흔들림
- shared hotset 경합
- future NUMA/remote memory latency 차이

AIOS처럼 멀티 에이전트와 대형 텐서를 다루는 OS에서는 특히 중요하다.

### 4. 사용자/외부 입력 계열

외부 세계가 만든 비결정성이다.

예:

- 시리얼 입력 타이밍
- 네트워크 패킷 도착 시점
- USB 입력 이벤트
- 사람이 입력한 시점

입력 자체보다, **입력 시점과 간격**이 더 중요한 엔트로피 후보가 된다.

### 5. 하드웨어 RNG / 내부 확률원 계열

x86_64 계열에서는 표준적인 하드웨어 엔트로피 소스가 있을 수 있다.

예:

- `RDRAND`
- `RDSEED`

이건 일반적인 jitter보다 품질이 높을 가능성이 크지만, 여전히 단독 맹신보다는 다른 소스와 함께 섞는 편이 안전하다.

## 노이즈는 어떻게 생기나

엔트로피 소스는 "랜덤처럼 보인다"가 아니라, 어떤 계층에서 변동이 생기는지를 이해해야 한다.

### CPU 내부

- pipeline 상태 변화
- speculative execution 영향
- cache hit/miss 차이
- SMT sibling의 간섭
- 전력/클럭 상태 변화

이런 요소는 `TSC`로 측정한 짧은 루프 시간에 흔들림을 만든다.

### 메모리 계층

- L1/L2/L3 경합
- prefetcher 동작
- TLB 상태
- DRAM row/bank 충돌

이건 대규모 텐서 이동, shared hotset 접근, KV-cache 접근에서 지터를 만든다.

### 버스/디바이스

- PCIe arbitration
- DMA completion 순서
- 장치 내부 queue 상태
- firmware/option ROM 영향

이건 NIC, USB, storage, accel HAL 쪽에서 entropy 후보가 된다.

### 외부 환경

- 사람 입력 타이밍
- 네트워크 외부 세계
- 열/전력 변화
- VM host scheduling

이건 일정하지 않지만, 때로는 편향이 심하다.  
따라서 "항상 좋은 엔트로피"라고 보면 안 된다.

## 현재 AIOS에서 수집 가능한 소스

현재 저장소 기준으로 당장 접근 가능한 소스를 `현실적` 기준으로 정리하면 이렇다.

### 1. TSC 기반 지터

관련 코드:

- [include/kernel/time.h](Z:\aios\aios-kernel\include\kernel\time.h)
- [kernel/time.c](Z:\aios\aios-kernel\kernel\time.c)

현재 AIOS는 이미 `TSC`와 `PIT`를 이용해 보정된 시간원을 사용하고 있다.  
따라서 다음을 엔트로피 후보로 쓸 수 있다.

- 짧은 반복 측정의 잔차
- calibrate 과정의 편차
- 특정 이벤트 전후 cycle delta

장점:

- 항상 존재
- 구현이 간단

단점:

- VM/QEMU에서는 품질이 낮을 수 있음
- 단독으로는 예측 가능성 높음

### 2. 부팅 selftest 메모리 미세 지터

관련 코드:

- [include/kernel/selftest.h](Z:\aios\aios-kernel\include\kernel\selftest.h)
- [kernel/selftest.c](Z:\aios\aios-kernel\kernel\selftest.c)

이미 boot-time memory microbench가 있기 때문에 다음을 엔트로피 후보로 쓸 수 있다.

- memcpy/memmove 반복 측정 편차
- cache 단계별 access latency 흔들림
- boot tier 측정 과정의 잔차

장점:

- AIOS 구조와 잘 맞음
- 메모리 경합이 생길수록 지터가 커질 수 있음

단점:

- 부팅 시점에만 집중됨
- 고정 환경에서는 반복성이 높을 수 있음

### 3. 시리얼 입력 타이밍

관련 코드:

- [drivers/serial.c](Z:\aios\aios-kernel\drivers\serial.c)

현재 시리얼 콘솔은 polling 기반이라, 입력 시점 자체가 엔트로피 후보가 될 수 있다.

장점:

- 사람이 개입하면 예측이 어려움

단점:

- 입력이 없으면 소스가 없음
- 자동화 환경에선 기대 불가

### 4. 드라이버 초기화 및 I/O 상태 변동

관련 코드:

- [drivers/e1000.c](Z:\aios\aios-kernel\drivers\e1000.c)
- [drivers/usb_host.c](Z:\aios\aios-kernel\drivers\usb_host.c)
- [drivers/storage_host.c](Z:\aios\aios-kernel\drivers\storage_host.c)

현재는 smoke/bootstrap 단계지만, 다음을 후보로 쓸 수 있다.

- 장치 ready 도달 시점
- link up/down 변화 타이밍
- controller status poll 횟수
- timeout 직전 지터

장점:

- CPU 밖에서 생기는 변동을 가져올 수 있음

단점:

- 현재 AIOS는 아직 IRQ/data path가 약함
- polling만으로는 수집 품질이 제한적

### 5. 스케줄러/런타임 이벤트 지터

관련 코드:

- [sched/ai_sched.c](Z:\aios\aios-kernel\sched\ai_sched.c)
- [runtime/slm_orchestrator.c](Z:\aios\aios-kernel\runtime\slm_orchestrator.c)
- [runtime/autonomy.c](Z:\aios\aios-kernel\runtime\autonomy.c)

현재는 완전한 선점 스케줄러는 아니지만, task submit/plan apply/telemetry 이벤트 간의 간격은 후보가 될 수 있다.

장점:

- AIOS다운 소스
- 멀티 에이전트 구조와 잘 연결됨

단점:

- 아직 진짜 병렬 실행이 약함
- 동일 입력에선 편향이 클 수 있음

## 지금은 제안 단계인 소스

현재 저장소에는 아직 구현되지 않았지만, 이후 유효한 소스는 다음이다.

### 1. CPUID 기반 `RDRAND` / `RDSEED`

이건 가장 먼저 붙여볼 가치가 있다.

필요한 것:

- CPUID feature detection
- 성공/실패 횟수 추적
- 다른 소스와 혼합

장점:

- x86_64에서 표준적인 하드웨어 RNG 경로

단점:

- 구형 CPU나 일부 VM에선 없음
- 단독 신뢰는 피하는 편이 좋음

### 2. 외부 IRQ 지터

현재 AIOS는 예외 처리 중심이라, 앞으로 다음이 붙으면 강력한 소스가 된다.

- timer IRQ
- NIC IRQ
- USB IRQ
- storage IRQ

이벤트 도착 간격은 classic entropy source에 가깝다.

### 3. NUMA / HMAT / SLIT 기반 locality variance

향후:

- SRAT
- SLIT
- HMAT

를 읽게 되면, local/remote memory 접근 차이와 node 간 지연 차이를 entropy 보조 입력으로 활용할 여지가 있다.

다만 이것은 난수 품질보다 **시스템 특성 힌트**에 가깝기 때문에, 직접 entropy보다는 entropy mixing weight 조절에 더 적합하다.

### 4. 가속기 실행 변동

향후 accel HAL이 실체화되면 다음이 가능하다.

- GPU/NPU queue completion 지연
- DMA burst variance
- thermal/power state 영향

이건 AIOS의 장기 강점이 될 수 있다.

## 약하거나 위험한 소스

다음은 절대로 raw entropy처럼 믿으면 안 된다.

### 1. 단순 시스템 시간

예:

- `kernel_time_monotonic_ns()` 값 자체
- `TSC` 값 자체

이건 시간일 뿐, 엔트로피가 아니다.

쓸 수 있는 건:

- 이벤트 간 지터
- 반복 측정의 편차

뿐이다.

### 2. 메모리 주소 자체

예:

- 할당된 `phys_addr`
- `virt_addr`
- block base 주소

현재 AIOS는 정적 풀과 identity map 비중이 커서 주소 패턴이 꽤 예측 가능하다.  
즉 주소 자체를 entropy로 쓰는 건 위험하다.

### 3. health/telemetry 값 자체

예:

- compatibility score
- health level
- queue depth recommendation

이 값들은 정책 결과이지, 엔트로피가 아니다.  
그 값이 바뀌는 이벤트의 시점은 참고가 될 수 있지만, 값 그 자체는 RNG seed로 부적절하다.

### 4. VM/QEMU에서의 미세 지터 맹신

QEMU나 가상환경에서는 다음이 많이 왜곡된다.

- TSC 지터
- 장치 응답 지연
- 인터럽트 분포
- 링크 상태 변화

즉 VM에서 엔트로피를 수집하더라도, 상태를 `degraded`로 두는 편이 맞다.

### 5. raw 장치 상태 레지스터

장치 레지스터 값은 대부분 결정적이다.  
변화 시점은 참고할 수 있지만 값 자체를 곧바로 seed에 넣는 방식은 품질이 낮다.

## AIOS에 맞는 분리 구조

이 프로젝트에는 엔트로피를 두 갈래로 나누는 것이 좋다.

### 1. kernel secure RNG

용도:

- 세션 ID
- future `getrandom`
- capability token
- 안전한 무작위값

요구:

- raw noise를 직접 노출하지 않음
- seed 상태 관리
- reseed 가능
- VM/저엔트로피 환경에서 degraded 상태 표시

### 2. AI exploration RNG

용도:

- worker diversification
- planner tie-break
- sampling bias
- chaos input
- randomized backoff

이 경로는 secure RNG보다 느슨해도 되지만, 여전히 raw noise 직결은 피해야 한다.

## 권장 수집 구조

실전적으로는 이 순서가 좋다.

1. `entropy_event(type, value, timing)` 같은 공통 입력 API
2. TSC 지터 / selftest 잔차 / I/O 완료 간격을 지속적으로 수집
3. 가능하면 `RDRAND/RDSEED`를 보조 입력으로 혼합
4. 엔트로피 풀 상태를 `UNSEEDED / DEGRADED / READY / CRYPTO_READY`로 나눔
5. `kernel RNG`와 `AI exploration RNG`를 논리적으로 분리
6. `SLM snapshot`에 엔트로피 상태를 노출

## 지금 AIOS 기준 추천 첫 구현 순서

가장 안전한 첫 단계는 이렇다.

1. `include/kernel/entropy.h`, `kernel/entropy.c`
2. health subsystem에 `ENTROPY` 추가
3. 초기 소스는 다음 3개만 수집
   - TSC jitter
   - boot selftest residual
   - serial input timing
4. 상태가 안정되면
   - PCI / driver poll jitter
   - timer IRQ jitter
   - `RDRAND/RDSEED`
   를 차례로 추가

## 한 줄 결론

AIOS에서 노이즈는 `타이머`가 아니라 `엔트로피 입력`으로 다뤄야 한다.  
그리고 지금 가장 현실적인 방향은 **TSC 지터 + selftest 잔차 + I/O timing + future hardware RNG**를 섞는 별도 `entropy plane`을 만드는 것이다.
