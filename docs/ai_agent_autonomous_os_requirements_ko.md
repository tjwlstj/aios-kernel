# AI 에이전트 자율 OS를 위한 커널 요구사항 정리

## 1. 목적 재정의

AIOS의 장기 목표가 단순한 "AI 워크로드 최적화 커널"을 넘어서
"AI 에이전트가 시스템 상태를 이해하고, 자원과 정책을 자율적으로 조정하며,
안전하게 운영을 지속하는 OS"라면, 커널의 역할도 다음처럼 재정의할 필요가 있다.

- 단순 실행 환경 제공
- AI 작업을 위한 자원 모델 제공
- 에이전트가 신뢰할 수 있는 관측 데이터 제공
- 정책 적용/롤백을 위한 안전한 실행 경로 제공
- 장애, 불안정, 비정상 상태에서 자동 복구 가능한 기반 제공

즉, 이 프로젝트의 핵심은 "AI를 돌릴 수 있는 OS"가 아니라,
"AI 에이전트가 OS 수준에서 운영 결정을 내릴 수 있게 만드는 커널"이다.

---

## 2. 현재 AIOS의 강점

현재 저장소 기준으로 이미 방향성이 잘 잡혀 있는 부분은 다음과 같다.

- 텐서 중심 메모리 관리자: `mm/tensor_mm.c`
- AI 워크로드 전용 스케줄러: `sched/ai_sched.c`
- 가속기 추상화 계층: `hal/accel_hal.c`
- 자율 제어 골격: `runtime/autonomy.c`
- AI syscall 인터페이스: `runtime/ai_syscall.c`
- 부팅 단계 성능 프로파일링과 PCI 탐지: `kernel/selftest.c`, `drivers/platform_probe.c`

즉, AIOS는 이미 "AI 실행 특화 커널"의 기본 골격은 갖고 있다.
하지만 "에이전트 자율 OS"가 되려면 아래 계층이 더 필요하다.

---

## 3. 자율 OS 관점에서 커널에 꼭 필요한 기능

### A. 신뢰 가능한 시간/성능 기준층

자율 최적화는 결국 "이 정책이 더 좋아졌는가"를 측정할 수 있어야 한다.
그래서 커널은 성능과 시간의 기준점을 제공해야 한다.

필수 기능:

- 단조 증가 시간원
- TSC/PIT/APIC/HPET 기반 보정된 시간원
- 부팅 시점 baseline 성능 프로파일
- CPU cache/L1~L3/DRAM 접근 지연 추정
- memcpy/memmove/DMA/PCIe 복사 대역폭 측정
- 스케줄링 latency, wait time, deadline miss 계측

현재 상태:

- 일부 구현됨: `kernel/selftest.c`
- 부족함:
  - 실제 나노초 단위 시간원 통합
  - 스케줄러와 동일 시간축 사용
  - DMA/PCIe 대역폭 계측 경로

왜 중요한가:

- 자율 정책은 추정이 아니라 계측 기반이어야 한다.
- 성능 티어를 알아야 probe depth, timeout, queue depth를 자동 조절할 수 있다.

---

### B. 관측성(Observability) 커널

에이전트가 똑똑해지려면 먼저 커널이 충분히 말해줘야 한다.

필수 기능:

- 메모리/스케줄러/가속기/네트워크/스토리지 텔레메트리
- 이벤트 로그와 causal trace
- 정책 적용 전후 snapshot
- 최근 N초/N분 rolling metrics
- subsystem별 health score
- anomaly signal (OOM 직전, queue saturation, irq storm, thermal, link flap)

현재 상태:

- 일부 구현됨: `runtime/autonomy.c` telemetry ring, event log
- 부족함:
  - 네트워크/USB/스토리지/전력/온도 텔레메트리 없음
  - 정책 전후 KPI 비교기 부족
  - subsystem health score 없음

왜 중요한가:

- 자율 시스템은 "상태를 안다"보다 "상태를 신뢰도 있게 설명할 수 있다"가 중요하다.

---

### C. 정책 적용/롤백 실행층

자율 OS는 단순히 추천을 만드는 것이 아니라, 정책을 안전하게 적용하고 필요 시 되돌릴 수 있어야 한다.

필수 기능:

- 정책 제안 queue
- 정책 target schema 분리
  - scheduler
  - memory
  - accelerator
  - network
  - storage
- bounded action 범위
- two-phase apply
- rollback snapshot
- cooldown / retry budget
- safe mode / observation only mode

현재 상태:

- 일부 구현됨: `runtime/autonomy.c`
- 부족함:
  - 현재 actuator는 사실상 scheduler priority만 있음
  - memory/accel/network target action 부재
  - before/after verifier 미완

왜 중요한가:

- 자율 제어는 "바꾸는 능력"이 아니라 "위험하게 안 바꾸는 능력"이 핵심이다.

---

### D. 작업 모델(Task Model)의 고도화

에이전트 OS에서는 프로세스보다 "작업 단위"가 중요하다.
LLM inference, planner, tool call, retriever, background finetune은 각각 다른 성격을 가진다.

필수 기능:

- 작업 타입 분류
  - realtime inference
  - interactive planning
  - background learning
  - tool execution
  - data ingest
- deadline, cost, risk, affinity를 포함한 task metadata
- priority inheritance / class-based scheduling
- batchable / non-batchable 구분
- cancel / suspend / checkpoint / resume
- task graph / dependency execution

현재 상태:

- 일부 구현됨: `sched/ai_sched.c`
- 부족함:
  - task graph 없음
  - agent session/task lineage 없음
  - token latency / inference SLA 지표 없음

왜 중요한가:

- AI 에이전트는 단일 태스크 실행기가 아니라 복수의 상호 의존 작업 그래프를 운영한다.

---

### E. 메모리 계층의 고도화

AI 에이전트 OS에서 메모리는 단순 할당기가 아니라 정책 대상이다.

필수 기능:

- tensor/model/inference/dma/kv-cache 분리
- pinned memory / zero-copy / DMA-friendly region
- memory pressure signal
- hot/cold tensor 분류
- kv-cache compaction / eviction 정책
- model residency 관리
- NUMA 또는 device-local memory 추상화
- memory QoS

현재 상태:

- 강점 있음: `mm/tensor_mm.c`
- 부족함:
  - page fault / virtual memory / mapping 계층 부재
  - NUMA/device-local memory 정책 없음
  - pressure-driven reclaim 정책 없음

왜 중요한가:

- AI agent는 context window, model residency, KV cache가 핵심 자원이다.
- 결국 메모리 정책이 성능과 비용을 좌우한다.

---

### F. 장치/가속기 관리층

에이전트 OS는 하드웨어를 "있는지 여부"가 아니라 "지금 쓸 수 있는지"로 판단해야 한다.

필수 기능:

- PCI/PCIe probe
- link speed / width / BAR / MSI-X capability 파악
- GPU/NPU/FPGA 추상화
- network/usb/storage 기본 드라이버
- DMA 경로와 host<->device copy 정책
- IOMMU / isolation 준비
- reset / recovery / watchdog
- thermal/power utilization telemetry

현재 상태:

- 일부 구현됨:
  - `hal/accel_hal.c`
  - `drivers/platform_probe.c`
  - `drivers/e1000.c` 최소 부트스트랩
- 부족함:
  - 실가속기 submit/sync/interrupt 처리 없음
  - USB/스토리지 실사용 드라이버 없음
  - reset/recovery/watchdog 부족

왜 중요한가:

- 자율 에이전트는 결국 NIC, SSD, GPU, USB 센서/입력 장치까지 다뤄야 한다.

---

### G. 통신/분산 운영 기반

AI 에이전트가 진짜 운영 주체가 되려면 외부 세계와 안정적으로 통신해야 한다.

필수 기능:

- 최소 네트워크 스택
  - NIC 드라이버
  - ARP / IPv4 / UDP / TCP 최소 구현
- node identity
- secure control channel
- distributed inference / distributed worker messaging
- remote telemetry export
- heartbeat / liveness

현재 상태:

- 거의 미구현
- 현재는 NIC 탐지와 e1000 최소 초기화 정도만 존재

왜 중요한가:

- 단일 머신 자율화만으로는 실제 에이전트 운영체제의 가치가 제한된다.

---

### H. 보안/격리/권한 모델

AI 에이전트 자율화는 잘못 설계하면 가장 위험한 형태의 자동화가 된다.

필수 기능:

- capability-based permission
- syscall allowlist
- model/tool/network/storage 권한 분리
- 실행 컨텍스트 격리
- audit log
- signed policy / trusted update path
- secret handling boundary
- unsafe action guardrail

현재 상태:

- 일부 개념만 존재: observation-only, safe mode
- 부족함:
  - 실제 권한 모델 없음
  - untrusted agent/tool 분리 없음
  - audit/security 경로 없음

왜 중요한가:

- 자율 OS는 성능보다 사고 방지가 먼저다.

---

### I. 복구/내고장성(Fault Tolerance)

자율 시스템은 실패를 못 막는 대신, 실패 후 빨리 복구해야 한다.

필수 기능:

- subsystem watchdog
- driver reset/rebind
- degraded mode
- checkpoint / rollback
- crash-safe logs
- boot reason / failure reason persistence
- recovery policy state machine

현재 상태:

- 일부 구현됨: autonomy rollback 뼈대
- 부족함:
  - persistent recovery context 없음
  - driver/node recovery 계층 없음

왜 중요한가:

- 자율 운영에서는 "한 번 잘 도는 것"보다 "실패 후 계속 살아나는 것"이 더 중요하다.

---

### J. 에이전트 런타임과의 경계 설계

모든 걸 커널 안에 넣는 것은 오히려 나쁜 설계다.
커널과 user-space agent runtime의 경계를 분명히 해야 한다.

커널이 가져야 하는 것:

- 시간원, 자원 모델, 스케줄링, 메모리, 장치, 보안, 정책 apply/rollback primitive

커널 밖으로 빼야 하는 것:

- LLM/SLM 추론 자체
- planner logic
- tool orchestration
- 장기 학습/정책 증류
- 복잡한 semantic reasoning

권장 경계:

- Kernel:
  - telemetry source
  - policy actuator
  - safety guard
- User space agent runtime:
  - planner
  - critic
  - learner
  - tool router

---

## 4. AI 에이전트 자율 OS를 위한 우선순위 기능 매트릭스

### Tier 0: 반드시 먼저 필요한 것

- 안정 부팅/시간원
- 검증 가능한 selftest/benchmark
- telemetry ring + health/event log
- scheduler/memory/accel 정책 apply + rollback
- 최소 NIC 드라이버
- 안전모드/관측모드
- 회귀 테스트/CI

### Tier 1: 자율 운영에 필요한 것

- policy verifier
- task graph / session model
- network stack 최소 구현
- storage/log persistence
- driver reset/recovery
- capability/allowlist 보안 모델

### Tier 2: 진짜 AIOS답게 만드는 것

- GPU/NPU 실 submit path
- NUMA/device-local memory policy
- remote telemetry / distributed agent fabric
- profile specialization
- long-term policy distillation

---

## 5. 현재 AIOS 기준 권장 로드맵

### Phase 1. Kernel-as-Measurement Platform

목표:
- "무슨 일이 일어나는지"를 정확히 안다

해야 할 일:
- time source 통합
- scheduler KPI 확장
- memory pressure / accel utilization / NIC status telemetry 추가
- boot profile을 runtime telemetry와 연결

### Phase 2. Kernel-as-Safe-Actuator

목표:
- "무엇을 바꿀 수 있는지"를 안전하게 정의한다

해야 할 일:
- autonomy action schema 확장
- scheduler/memory/accel/network target 분리
- rollback snapshot 구조화
- verifier 추가

### Phase 3. Kernel-as-Agent Substrate

목표:
- "에이전트가 정책을 제안하고 커널이 안전하게 실행"하는 구조 완성

해야 할 일:
- user-space agent runtime ABI
- policy daemon / guardian split
- capability model
- event-driven control loop

### Phase 4. Autonomous Distributed AIOS

목표:
- 단일 머신을 넘어 노드 단위 자율 운영으로 확장

해야 할 일:
- network stack
- node identity
- remote telemetry
- distributed scheduling / model placement

---

## 6. 구현 관점에서 가장 시급한 실제 과제

지금 코드베이스 기준으로 가장 먼저 필요한 과제는 다음이다.

1. 시간원 통일
- `sched/ai_sched.c`의 tick 기반 가정 제거
- `kernel/selftest.c`의 성능 측정 결과를 공통 monotonic time과 연결

2. autonomy verifier 구현
- `runtime/autonomy.c`에 before/after score 도입
- rollback trigger를 KPI와 직접 연결

3. 장치 계층 분리
- `drivers/platform_probe.c`는 discovery 전용
- `drivers/e1000.c`, 이후 USB/storage는 driver lifecycle 전용으로 분리

4. network/storage 최소 경로 확보
- 에이전트 OS라면 최소한 NIC + persistent log 경로는 빨리 필요

5. 커널/유저 공간 경계 명확화
- 자율 planner는 커널 밖
- 커널은 telemetry, actuator, guard 역할에 집중

---

## 7. 결론

AI 에이전트 자율 OS를 만들기 위해 필요한 커널은
"AI 연산을 잘 돌리는 커널"보다 한 단계 더 나아가야 한다.

핵심은 아래 4가지다.

- 잘 측정하는 커널
- 잘 설명하는 커널
- 잘 되돌리는 커널
- 잘 제한하는 커널

현재 AIOS는 텐서 메모리, AI 스케줄러, autonomy 골격, 장치 탐지라는 좋은 출발점을 이미 갖고 있다.
다음 단계는 기능을 무작정 늘리기보다,
"관측 -> 검증 -> 안전 적용 -> 복구"의 루프를 커널 내부에 단단히 만드는 것이다.

그 위에 user-space agent runtime을 얹으면, 비로소 "AI 에이전트의 자율 OS"에 가까워질 수 있다.
