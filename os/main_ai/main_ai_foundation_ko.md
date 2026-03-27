# 메인 AI 기초안

## 목표

메인 AI는 단순한 큰 모델이 아니라,
자기 연속성과 목표 연속성을 유지하는 상위 운영 코어다.

핵심 책임:

- 자기 상태 유지
- 목표 스택 관리
- 장기기억 통합
- 하위 노드 활성화/중지
- 학습 경로 승인/거절

## 상태 모델

메인 AI는 최소한 다음 상태를 유지해야 한다.

- `identity_state`
  현재 자기 서술, 역할, 금지 규칙, 운영 모드
- `goal_stack`
  장기/중기/단기 목표와 우선순위
- `memory_state`
  episodic / semantic / summary memory 상태
- `operator_state`
  `S_static`, `S_chaos`, `SCO`, 현재 모드
- `tree_state`
  어떤 하위 노드가 살아 있고 무엇을 맡고 있는지
- `learning_gate`
  memory-only / adapter-allowed / offline-deferred 여부

## 정적-혼돈 연산자

메인 AI는 각 주기마다 다음을 계산한다.

```text
S_static = f(self_consistency, goal_continuity, memory_coherence,
             policy_stability, resource_reserve, safety_margin)

S_chaos = f(novelty_pressure, prediction_error, unresolved_uncertainty,
            opportunity_gain, external_surprise, hypothesis_diversity_demand)

SCO = S_chaos - S_static
```

운용 모드:

- `stabilize`
  기억 통합과 자기 일관성 우선
- `balance`
  탐색과 안정화 균형
- `explore`
  더 많은 worker를 깨워 가설을 넓게 생성

## 메인 AI 루프

1. 커널 텔레메트리와 최근 기억을 읽는다
2. 정적-혼돈 점수를 계산한다
3. 운용 모드를 선택한다
4. 하위 노드 예산과 활성화 계획을 만든다
5. 하위 노드 결과를 검증 후 병합한다
6. 기억과 목표 상태를 갱신한다
7. 학습 경로를 결정한다

## 학습 원칙

- base model은 즉시 바꾸지 않는다
- 실시간 변화는 memory-first
- adapter는 승인된 경우만 업데이트
- 메인 AI 정체성에 영향 주는 변경은 offline merge로 보낸다

## 하위 노드와의 관계

메인 AI는 raw 출력을 모두 직접 읽지 않는다.
항상 아래 단계 뒤에 최종 입력을 받는다.

- planner 생성
- critic 비판
- verifier 확인
- summarizer 압축

즉 메인 AI는 "검증되고 압축된 결과"만 소비하는 상위 의사결정기여야 한다.
