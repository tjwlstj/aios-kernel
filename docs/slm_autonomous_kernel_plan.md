# AIOS SLM 자율 운영/최적화 구조 계획

## 1. 목표

커널 OS 설치 후, SLM(Small Language Model) 기반 에이전트가 다음을 **스스로 반복 수행**하도록 한다.

1. 시스템 상태 수집/해석
2. 스케줄링/메모리/가속기 정책 제안
3. 안전 제약 내 자동 적용
4. 결과 평가 후 정책 업데이트

즉, "사람이 매번 튜닝하지 않아도 커널이 운영 환경에 맞춰 점진적으로 최적화"되는 구조를 만든다.

---

## 2. 계층 구조 (Hierarchical SLM Architecture)

### L0: Safety/Policy Guardian (필수)
- 역할: 위험 작업 차단, 권한 통제, 롤백 승인
- 책임:
  - 화이트리스트 기반 syscall/명령 제어
  - 리소스 상한(CPU/메모리/가속기) 강제
  - 실패/이상 징후 시 즉시 Safe Mode 전환

### L1: Orchestrator Planner
- 역할: 전체 정책 계획 수립
- 입력:
  - 메모리 통계
  - 스케줄러 통계
  - 모델/가속기 상태
- 출력:
  - "이번 주기 실행할 최적화 액션 묶음(batch plan)"

### L2: Worker SLM Pool (병렬)
- 역할: 분산 실행기
- 예시 워커:
  - Memory Worker: pool 압력 완화 정책
  - Scheduler Worker: 우선순위/배치 튜닝
  - Accelerator Worker: 친화도/동기화 정책 조정
  - Inference Worker: latency/throughput 균형 튜닝

### L3: Verifier/Critic
- 역할: 적용 결과 검증
- 기준:
  - p95 latency
  - 처리량(tokens/s 또는 tasks/s)
  - 실패율/재시도율
  - 메모리 단편화/피크 사용량

---

## 3. 학습 계층 (Learning Layers)

### 3.1 단기 학습 (Online Adaptation)
- 주기: 수 초~수 분
- 방식:
  - 최근 window metric 기준 미세 파라미터 조정
  - 실패 시 즉시 이전 정책 복귀

### 3.2 중기 학습 (Policy Distillation)
- 주기: 수 시간~일
- 방식:
  - 성공한 의사결정 로그를 규칙/경량 정책으로 증류
  - 불안정 정책은 감쇠 또는 폐기

### 3.3 장기 학습 (Profile Specialization)
- 주기: 주 단위
- 방식:
  - 워크로드 프로파일(실시간, 배치, 혼합형)별 정책 템플릿 구축
  - 부팅 시 환경 감지 후 템플릿 우선 적용

---

## 4. 설치 후 자율 최적화 루프 (Bootstrapping)

## Phase A: 초기 안정화 (Install + 0~24h)
- Read-only 관측 모드
- 정책 적용 없이 추천안만 생성
- 기준선(Baseline) 수집

## Phase B: 제한적 자동 적용 (Day 2~7)
- 저위험 항목만 자동 적용
- 예: 배치 크기 미세 조정, 우선순위 재정렬
- 모든 변경점에 체크포인트/롤백 기록

## Phase C: 확장 자동 최적화 (Week 2+)
- 워크로드별 템플릿 적용
- 실패율/성능개선 이득이 검증된 항목 범위 확대

---

## 5. 커널 연동 포인트 (AIOS 기준)

- Memory 계층:
  - Tensor MM stats 기반 메모리 클래스 압력 분석
- Scheduler 계층:
  - task 큐 길이, preemption, active/completed 통계 활용
- Syscall 계층:
  - 정보 조회 syscall을 관측 입력으로 사용
  - 액션 적용은 허용된 syscall 범위에서만 수행

---

## 6. 안전 설계 원칙

1. **Never unsafe by default**
   - 기본은 관측 전용, 자동 변경은 opt-in
2. **Two-phase apply**
   - 제안(plan) → 승인(guardian) → 적용(commit)
3. **Rollback first**
   - 정책 적용 전 스냅샷 저장
4. **Bounded autonomy**
   - 자동 조정 범위(파라미터/빈도/대상) 하드 제한

---

## 7. 데이터 구조(초안)

```c
typedef struct {
    uint64_t ts_ns;
    mem_stats_t mem;
    sched_stats_t sched;
    uint32_t active_models;
    uint32_t active_accels;
} telemetry_frame_t;

typedef struct {
    uint32_t action_id;
    uint32_t risk_level;      // 0~3
    uint32_t target_subsys;   // mem/sched/accel/infer
    int64_t  delta_value;
} policy_action_t;

typedef struct {
    uint64_t before_score;
    uint64_t after_score;
    bool rollback_triggered;
} policy_eval_t;
```

---

## 8. 구현 로드맵

### Milestone 1 (MVP)
- Telemetry ring buffer
- Policy action queue
- Guardian allowlist + rollback manager

### Milestone 2
- Worker SLM 병렬 실행 프레임
- Verifier 기반 자동 채점
- 자동/수동 모드 전환

### Milestone 3
- 프로파일별 정책 템플릿
- 장기 학습/증류 파이프라인
- 자율 최적화 안정성 리포트

---

## 9. 성공 지표 (KPI)

- p95 latency 20% 개선 (기준 대비)
- OOM/실패율 30% 감소
- 수동 튜닝 개입 횟수 50% 감소
- 롤백 없는 연속 안정 실행 기간 증가

