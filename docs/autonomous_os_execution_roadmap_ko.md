# AI 에이전트 자율 OS 실행 로드맵

## 1. 목표

AIOS를 다음 단계로 끌어올리는 목표는 단순하다.

- AI 워크로드를 잘 실행하는 커널
- 에이전트가 시스템 상태를 이해할 수 있는 커널
- 정책을 안전하게 적용/롤백할 수 있는 커널
- 장치, 메모리, 스케줄러를 자율적으로 운영할 수 있는 기반

즉, 최종 목표는 "AI 친화 커널"이 아니라
"AI 에이전트가 운영 주체가 될 수 있는 OS substrate"를 만드는 것이다.

---

## 2. 현재 기준선

현재 저장소에는 이미 다음 기반이 있다.

- 안정 부팅과 QEMU 스모크 테스트
- boot-time selftest / 성능 티어 추정
- PCI probe와 기본 파이프라인 우선순위
- 최소 e1000 부트스트랩
- 텐서 메모리 관리자
- AI 전용 스케줄러
- 가속기 HAL 골격
- autonomy control plane 골격
- AI syscall 인터페이스

즉, "측정", "탐지", "골격"은 있다.
이제 필요한 것은 이 골격을 "검증 가능하고, 자율 제어 가능한 시스템"으로 연결하는 일이다.

---

## 3. 핵심 우선순위

### Priority 1. 공통 시간원과 KPI 통합

목표:
- 모든 subsystem이 같은 시간 기준으로 움직이게 한다.

필수 작업:
- monotonic time source 도입
- scheduler tick/time 모델 통합
- autonomy telemetry timestamp 정합성 확보
- latency/throughput/fail-rate KPI를 공통 구조로 정의

완료 기준:
- scheduler/autonomy/selftest가 같은 시간 단위를 사용
- deadline/wait/latency 계산이 하드웨어 타이머 기준으로 설명 가능

---

### Priority 2. autonomy verifier와 rollback 고도화

목표:
- 정책을 적용했다면 "좋아졌는지/나빠졌는지"를 커널이 판단하게 만든다.

필수 작업:
- before/after score 계산
- risk budget / cooldown / retry budget
- rollback trigger를 KPI와 직접 연결
- subsystem별 action schema 분리

완료 기준:
- scheduler action commit 후 verifier 결과가 나온다
- 성능 악화 시 자동 rollback이 가능하다

---

### Priority 3. 장치 계층 분리와 최소 실사용 드라이버

목표:
- discovery와 actual driver lifecycle을 분리한다.

필수 작업:
- platform_probe는 discovery 전용으로 유지
- e1000을 send/receive 가능한 최소 NIC driver로 확장
- USB/xHCI 초기화 skeleton 추가
- storage/log persistence용 최소 장치 경로 확보

완료 기준:
- 최소 1개 NIC에서 패킷 송신 확인
- persistent log 또는 crash reason 저장 가능

---

### Priority 4. 메모리 정책 계층 강화

목표:
- tensor memory manager를 자율 정책 대상 자원으로 승격한다.

필수 작업:
- memory pressure signal
- hot/cold tensor 분류
- KV-cache residency/eviction 정책
- pinned/DMA/reclaim 지표 추가

완료 기준:
- autonomy가 메모리 관련 action을 제안/검증/롤백 가능

---

### Priority 5. 에이전트 런타임 경계 확립

목표:
- planner/critic/learner를 커널 바깥 user space로 분리한다.

필수 작업:
- telemetry ABI 정의
- policy actuator syscall/API 정리
- capability/allowlist 정리
- guardian 역할 명확화

완료 기준:
- user-space agent runtime이 telemetry를 받고 policy를 제안할 수 있음
- 커널은 안전한 executor 역할에 집중

---

## 4. 단계별 실행 계획

### Phase A. Measured Kernel

기간:
- 즉시 시작

목표:
- 커널이 자신의 상태를 정확히 측정하고 설명할 수 있어야 한다.

작업:
- 공통 monotonic time source
- scheduler/autonomy KPI 구조 통합
- boot profile을 runtime telemetry와 연결

---

### Phase B. Safe Autonomous Kernel

목표:
- 정책 적용이 단순한 데모가 아니라 실제 운영 루프가 되도록 만든다.

작업:
- verifier
- rollback policy
- safe mode / degraded mode
- subsystem action schema

---

### Phase C. Device-Useful Kernel

목표:
- 실제 외부 세계와 상호작용 가능한 OS가 되도록 만든다.

작업:
- e1000 최소 송수신
- USB/xHCI skeleton
- storage/persistent log

---

### Phase D. Agent Substrate

목표:
- user-space agent runtime이 올라와도 안정적으로 작동하는 기반을 만든다.

작업:
- telemetry ABI
- policy daemon 경계
- capability / audit / secret boundary

---

## 5. 당장 실행할 Next 3

1. 공통 time source 도입
- scheduler/autonomy를 같은 monotonic time으로 맞춤

2. autonomy verifier MVP
- scheduler priority 조정에 대해 before/after score 계산

3. e1000 최소 송신 경로
- descriptor ring 없이도 우선 드라이버 상태와 link/MAC 신뢰도를 높이고, 이후 TX ring 초기화로 확장

---

## 6. 하지 말아야 할 것

- 커널 안에 LLM planner 자체를 넣기
- telemetry 없이 정책부터 자동 적용하기
- USB/Bluetooth/Storage/GPU를 동시에 깊게 파기
- 보안 모델 없이 "자율"을 먼저 키우기

---

## 7. Definition of Done

이 프로젝트에서 어떤 기능이 "완료"로 간주되려면 최소한 다음이 필요하다.

- 부팅 테스트 통과
- serial/QEMU 로그로 확인 가능
- 회귀 시그니처 존재
- telemetry에 반영됨
- rollback 또는 failure handling 경로 존재

---

## 8. 결론

지금 AIOS에 필요한 것은 거대한 기능 목록이 아니라,
"측정 -> 판단 -> 적용 -> 검증 -> 복구" 루프를 커널 수준에서 닫는 것이다.

이 로드맵은 그 순서를 강제하기 위한 문서다.
즉, 다음 개발의 기준은 "무엇을 더 넣을까"가 아니라,
"자율 운영 루프를 얼마나 더 닫았는가"가 되어야 한다.
