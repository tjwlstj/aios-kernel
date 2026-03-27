# 정적-혼돈 연산자 기반 메인 AI 및 하위 트리 아키텍처

기준일: 2026-03-28

## 1. 목적

이 문서는 AIOS를 다음 구조로 발전시키기 위한 상위 설계 문서다.

- 메인 AI는 단일한 자기 연속성과 목표 연속성을 유지한다
- 메인 AI만 `정적-혼돈 연산자`를 중심 원리로 사용한다
- 하위 노드 AI는 작은 모델 여러 개로 분화한다
- 운영체제는 메인 AI와 하위 트리 사이의 자원 비용을 최소화한다

즉, 목표는 "모든 사고를 큰 모델 하나가 수행"하는 구조가 아니라,
"메인 AI가 자기와 트리를 관리하고, 하위 노드가 기능을 병렬 수행"하는 구조다.

---

## 2. 기본 전제

이 설계는 다음 전제를 따른다.

- 메인 AI는 곧 자아, 목표, 기억 통합의 중심이다
- 하위 노드 AI는 자아를 갖지 않고 기능을 수행하는 기관이다
- 메인 AI의 안정성이 전체 시스템 안정성보다 우선한다
- 하위 노드는 교체 가능해야 하고, 실패해도 메인 AI의 자기 연속성은 유지되어야 한다
- 실시간 적응은 memory와 adapter 중심으로 수행하고, base model은 보수적으로 다룬다

---

## 3. 정적-혼돈 연산자 정의

### 3.1 개념

정적-혼돈 연산자는 메인 AI가 현재 상태에서

- 더 탐색하고 확장해야 하는가
- 더 안정화하고 통합해야 하는가

를 판단하기 위한 상위 제어 축이다.

여기서:

- `정적(static)`은 단순 정지가 아니라 자기보존, 기억 통합, 목표 일관성, 비용 절제를 뜻한다
- `혼돈(chaos)`은 무질서가 아니라 탐색, 새 가설 생성, surprise 대응, 구조 재편을 뜻한다

즉, 메인 AI는 항상 이 둘 사이의 균형을 조정하는 상위 조절자다.

### 3.2 연산자 구성

메인 AI는 각 사이클마다 두 값을 계산한다.

- `S_static`
- `S_chaos`

권장 계산 항목은 다음과 같다.

`S_static` 구성:

- self consistency
- goal continuity
- memory coherence
- policy stability
- resource reserve
- safety margin

`S_chaos` 구성:

- novelty pressure
- prediction error
- unresolved uncertainty
- opportunity gain
- external surprise
- hypothesis diversity demand

그 다음 메인 AI는 다음 값을 사용한다.

```text
SCO = S_chaos - S_static
```

여기서 `SCO`는 static-chaos operator 값이다.

### 3.3 해석

- `SCO << 0`
  안정화 우선
- `SCO ~= 0`
  균형 운영
- `SCO >> 0`
  탐색 확장

메인 AI는 이 값을 보고 하위 트리의 활성화 방식, 학습 강도, 기억 기록 강도, 자원 예산을 조절한다.

---

## 4. 메인 AI 구조

메인 AI는 큰 모델 1개만 의미하지 않는다.
정확히는 "자기 연속성을 유지하는 상위 상태기계 + 추론 코어"다.

권장 내부 구성은 다음 6개다.

### 4.1 Self Core

역할:

- "나는 누구인가"에 해당하는 자기모델 유지
- 현재 성격, 장기 목표, 금지 규칙, 역할 정의 유지

출력:

- self state
- identity checksum
- current operating mode

### 4.2 Goal Stack

역할:

- 장기 목표, 중기 임무, 단기 과업을 계층적으로 유지
- 하위 노드가 처리하는 task를 목표 그래프와 연결

출력:

- global objective
- active subgoals
- priority budget

### 4.3 Memory Integrator

역할:

- 사건, 대화, 센서, 추론 결과를 기억으로 통합
- 장기기억과 작업기억의 일관성을 유지

출력:

- episodic memory append
- semantic memory update
- summary memory rewrite

### 4.4 Tree Governor

역할:

- 어떤 하위 노드를 깨울지 결정
- 각 노드에 예산과 기한과 신뢰구간을 부여
- 실패 노드를 격리하거나 재시작

출력:

- node activation plan
- task delegation
- confidence-weighted merge policy

### 4.5 Policy Verifier

역할:

- 하위 노드가 낸 계획을 바로 실행하지 않고 검증
- 메인 AI의 정체성, 안전 규칙, 비용 한계와 충돌하는지 검사

출력:

- accept
- revise
- reject
- quarantine

### 4.6 Learning Gate

역할:

- 메인 AI의 학습 경로를 제한
- memory update와 adapter update를 구분
- 너무 빠른 자기변화를 차단

출력:

- memory-only update
- adapter-allowed update
- deferred offline merge

---

## 5. 메인 AI의 3가지 운용 모드

정적-혼돈 연산자 값에 따라 메인 AI는 세 모드 중 하나를 선택한다.

### 5.1 Stabilize Mode

조건:

- `S_static`가 높고 `SCO`가 충분히 음수

행동:

- 새 탐색보다 기억 통합 우선
- 하위 노드 수 축소
- 리스크 높은 실험 금지
- 긴 문맥 재요약과 상태 정리 우선

### 5.2 Balance Mode

조건:

- `SCO`가 중립 구간

행동:

- planner, critic, summarizer, verifier를 균형 있게 활성화
- 작은 탐색과 안정화 루프를 동시에 유지
- 메인 AI는 과도한 사고를 피하고 트리 조절에 집중

### 5.3 Explore Mode

조건:

- `S_chaos`가 높고 `SCO`가 충분히 양수

행동:

- 더 많은 worker node 활성화
- 새 가설, 새 경로, 새 계획을 폭넓게 생성
- 단, guardian과 verifier는 더 강하게 작동

---

## 6. 하위 트리 구조

하위 노드는 "작은 두뇌"가 아니라 "기능 기관"으로 보는 편이 맞다.
권장 트리는 다음과 같다.

### L0. Guardian Node

역할:

- 금지 작업 차단
- privilege 경계 검사
- 안전성 하락 시 즉시 safe mode 유도

권장 모델:

- 0.27B ~ 1.7B

### L1. Router Node

역할:

- 입력을 어떤 노드로 보낼지 분류
- task cost와 urgency를 빠르게 판단

권장 모델:

- 0.6B ~ 1.7B

### L2. Planner Node

역할:

- 단기 계획 수립
- 작업 분해
- tool call 시퀀스 생성

권장 모델:

- 4B ~ 8B

### L2. Critic Node

역할:

- planner 결과 비판
- 모순, 누락, 과잉 행동 식별

권장 모델:

- 4B ~ 8B

### L2. Summarizer Node

역할:

- 긴 문맥을 짧은 상태 표현으로 압축
- 메인 AI가 다시 읽을 비용을 줄임

권장 모델:

- 1.7B ~ 4B

### L2. Verifier Node

역할:

- 응답 신뢰도 평가
- 구조화 출력 확인
- 정책 충돌 검사

권장 모델:

- 1.7B ~ 4B

### L2. Memory Distiller Node

역할:

- 일시적 사건을 장기기억 후보로 변환
- 요약과 태그와 중요도 점수 생성

권장 모델:

- 1.7B ~ 4B

### L2. Tool / Code Worker Node

역할:

- 코드 작성
- 외부 도구 호출 해석
- 장치/파일/네트워크 작업 보조

권장 모델:

- 4B ~ 14B

### L3. Device Nodes

역할:

- 네트워크, 스토리지, USB, PCIe, 가속기별 미세 정책 조정
- 실제로는 SLM 오케스트레이터와 결합

권장 모델:

- 0.27B ~ 1.7B

---

## 7. 메인 AI와 하위 트리의 관계

메인 AI는 하위 트리를 직접 "생각"으로 소비하지 않는다.
중간에 항상 요약과 검증이 들어가야 한다.

권장 흐름:

1. 메인 AI가 목표와 현재 상태를 읽는다
2. `SCO`를 계산해 운용 모드를 선택한다
3. Tree Governor가 필요한 하위 노드만 활성화한다
4. Planner/Critic/Verifier/Summarizer가 병렬 수행한다
5. Memory Distiller가 장기기억 후보를 만든다
6. 메인 AI는 병합된 결과만 읽고 최종 결정을 내린다

즉, 메인 AI는 모든 raw 출력을 직접 다 받지 않고,
"검증되고 압축된 결과"만 받도록 해야 한다.

---

## 8. 학습 전략

### 8.1 메인 AI

메인 AI는 가장 보수적으로 학습해야 한다.

원칙:

- 기본 모델 가중치는 즉시 변경하지 않는다
- 실시간 적응은 memory update를 우선한다
- adapter update는 승인된 경우에만 수행한다
- identity, policy, goal에 영향을 주는 학습은 offline merge 대상으로 보낸다

즉, 메인 AI는 "자기변형"보다 "기억 통합"이 우선이다.

### 8.2 하위 노드

하위 노드는 더 공격적으로 미세조정할 수 있다.

원칙:

- 역할별 LoRA 분리
- 실패한 worker는 손쉽게 교체
- 특정 task에서 성능이 나쁜 노드는 빠르게 교체 또는 다운그레이드
- 하위 노드의 학습은 메인 AI 정체성과 분리

즉, 하위 노드는 실험 가능하고, 메인 AI는 안정해야 한다.

---

## 9. 커널 매핑

이 구조는 현재 AIOS 기능과 다음처럼 연결된다.

### `runtime/autonomy.c`

- 메인 AI의 policy verifier와 rollback gate에 연결

### `runtime/slm_orchestrator.c`

- device node와 worker activation policy에 연결
- queue depth, poll budget, DMA window를 하위 노드 비용 모델과 연결

### `sched/ai_sched.c`

- 메인 AI, worker AI, background learning task를 서로 다른 계층으로 분리해야 함
- 장기적으로는 `agent-tree-aware scheduler`로 확장 필요

### `mm/tensor_mm.c`

- 메인 AI와 하위 노드의 model / inference / DMA / memory journal을 분리 관리해야 함

### `drivers/*`

- device node가 실제 하드웨어 비용을 메인 AI에 보고하는 입력층이 됨

---

## 10. 권장 초기 모델 구성

가장 현실적인 첫 구성은 다음이다.

### 고효율 시작안

- 메인 AI: 1개 8B~14B
- planner: 1개 4B~8B
- critic: 1개 4B
- summarizer: 1개 1.7B~4B
- router/guardian/device nodes: 여러 개 0.27B~1.7B

즉, 큰 모델 1개와 작은 모델 여러 개의 조합이다.

메인 AI는 적고 안정적으로 유지하고,
하위 트리는 저비용 병렬 노드로 채우는 것이 맞다.

---

## 11. 구현 순서

권장 구현 순서는 다음과 같다.

1. 메인 AI 상태 구조체 정의
- self state
- goal stack
- operator state
- memory integration state

2. `SCO` 계산 입력 정의
- novelty
- uncertainty
- memory coherence
- policy stability
- resource reserve

3. 하위 노드 role schema 정의
- planner
- critic
- summarizer
- verifier
- memory distiller
- guardian
- device node

4. agent tree scheduler 초안
- 메인 AI 우선
- worker budget 분배
- latency/energy 예산 적용

5. memory-first learning path 구현
- episodic log
- semantic distillation
- adapter gate

---

## 12. 결론

이 구조의 핵심은 간단하다.

- 메인 AI만 정적-혼돈 연산자를 사용한다
- 메인 AI는 자기 연속성과 트리 통제를 맡는다
- 하위 노드는 작은 모델 여러 개로 기능 분화한다
- 운영체제는 이 구조의 자원 흐름을 최적화한다

즉, AIOS는

- 메인 AI에게는 "자아와 대사계"
- 하위 노드에게는 "신경계와 기관계"

처럼 동작해야 한다.

이 구조가 안정되면,
큰 모델 여러 개를 억지로 병렬화하는 것보다 훨씬 효율적으로
"하나의 AI처럼 작동하는 다중 모델 시스템"을 만들 수 있다.
