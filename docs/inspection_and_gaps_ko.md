# AIOS 커널 점검 및 부족한 점 (2026-03-22)

## 1) 점검 범위
- 문서: `README.md`, `docs/design.md`, `docs/slm_autonomous_kernel_plan.md`
- 핵심 코드: `kernel/main.c`, `mm/tensor_mm.c`, `sched/ai_sched.c`, `hal/accel_hal.c`, `runtime/ai_syscall.c`, `runtime/autonomy.c`
- 빌드 검증: `make all`

---

## 2) 현재 상태 요약
- 베어메탈 부팅, 서브시스템 초기화, 기본 HAL/스케줄러/텐서 메모리/시스콜 골격은 정상 빌드됩니다.
- 특히 `autonomy`(L0~L3 계획의 MVP 일부) 구조가 코드에 반영되어 있으며, 관측 모드 기본값과 롤백 경로를 갖춘 점은 강점입니다.
- 다만 현재 단계는 "아키텍처 데모/프로토타입" 성격이 강하고, 실제 운영체제 수준으로 가기 위해 필요한 검증/안전/관측/하드웨어 실구현이 크게 부족합니다.

---

## 3) 부족한 점 (핵심)

### A. 검증 체계 부재 (가장 시급)
- 단위 테스트, 통합 테스트, 부트 테스트(QEMU 스모크), 회귀 테스트가 저장소에 없습니다.
- CI 파이프라인(예: GitHub Actions) 정의가 없어, PR/커밋 단위 품질 게이트가 없습니다.

**영향**
- 기능 추가 시 기존 기능 파손 여부를 자동 검출하기 어렵습니다.
- 자율 최적화 기능(autonomy)처럼 상태 전이가 많은 영역에서 회귀 위험이 큽니다.

**우선 조치**
1. 최소 스모크 테스트: `make all` + QEMU 부팅 로그 시그니처 검사.
2. 정책/상태머신 테스트: `autonomy_action_propose/commit/rollback` 시나리오별 검증.
3. 메모리 관리자 테스트: 경계값(정렬, 풀 한계, coalesce) 케이스 추가.

### B. 스케줄러 시간모델 단순화
- `ai_sched_tick()`은 PIT IRQ0 100Hz 경로에서 호출되며, `kernel_time_monotonic_ns()` 기반 elapsed 값을 사용합니다.
- 다만 현재 연결은 tick/accounting bootstrap입니다. 실제 task context switch, deadline miss 집계, 장기 실행 분포 검증은 아직 없습니다.

**영향**
- 데드라인 기반 정책의 신뢰도가 제한됩니다.
- p95 latency 같은 KPI를 현실적으로 검증하기 어렵습니다.

**우선 조치**
1. PIT IRQ0 bootstrap을 APIC/HPET 경로와 선택 가능한 tick source로 확장.
2. tick/accounting 이후 실제 runnable task 선택과 context switch 연결.
3. 스케줄러 통계(대기시간 분포, deadline miss rate) 확장.

### C. HAL 기능이 "탐색+추상화" 중심, 실 디바이스 실행 경로 부족
- 현재 HAL은 PCI 탐색과 CPU SIMD fallback 중심이며, 벤더별 실제 커널/큐 제출/인터럽트 완료 처리 경로가 없습니다.
- `accel_get_count()`는 PCI 탐색 디바이스 수만 반환하여, 문서상 "항상 fallback 존재" 인식과 사용자가 기대하는 디바이스 수 개념이 엇갈릴 수 있습니다.

**영향**
- 실제 GPU/NPU 성능 경로 검증 불가.
- 런타임 정책이 가속기 실상태를 반영하기 어렵습니다.

**우선 조치**
1. 디바이스 수 API를 "물리 디바이스 수"와 "사용 가능 디바이스 수(fallback 포함)"로 분리.
2. 최소 1개 벤더에 대해 submit/sync/오류코드 매핑의 skeleton 구현.
3. DMA/메모리 핀닝 추적 지표 추가.

### D. 자율제어 루프의 평가/학습 계층 미완성
- `autonomy`는 제안/승인/적용/롤백 골격은 있으나, `policy_eval_t`의 before/after 점수 계산 및 실 KPI 기반 검증이 아직 비어 있습니다.
- action_id를 스케줄러 task_id로 재해석하는 MVP 규칙은 있으나, 일반화된 액션 라우팅(메모리/가속기) 확장이 없습니다.

**영향**
- 자동 정책 적용이 있어도 "개선됨"을 정량적으로 증명하기 어렵습니다.
- 안전모드 전환 기준이 운영 KPI와 직접 연결되지 않습니다.

**우선 조치**
1. 평가기(Verifier) 최소 구현: latency/throughput/fail-rate delta 계산.
2. subsys별 action schema 분리(예: sched_action, mem_action, accel_action).
3. 실패 패턴 기반 자동 safe-mode 진입 규칙 정의.

### E. 문서-구현 간 간극
- 문서에는 장기 로드맵(분산/RDMA/SDK/학습증류)이 있으나, 현재 코드와 매핑 표(구현됨/진행중/미구현)가 없습니다.

**영향**
- 외부 기여자 온보딩 시 현재 상태 파악 비용 증가.
- "할 수 있는 것"과 "계획"이 혼동됩니다.

**우선 조치**
1. 기능 매트릭스 문서 추가: feature 상태/담당 모듈/테스트 유무.
2. 릴리즈 기준(Definition of Done) 명시.

---

## 4) 권장 우선순위 (4주 기준)

### Week 1: 품질 게이트 구축
- CI: `make all`, 포맷/정적체크, QEMU 스모크 부팅.
- 실패 시 로그 아티팩트 업로드.

### Week 2: 스케줄러/자율 루프 검증 강화
- 스케줄러 시간원 정리 및 핵심 지표 추가.
- autonomy verifier 최소 점수 모델 도입.

### Week 3: HAL API 정합성 개선
- 디바이스 카운트 API 분리 및 호출자 영향 점검.
- fallback/실디바이스 경로 의미를 문서화.

### Week 4: 메모리/정책 회귀 테스트 확장
- tensor allocator 경계 테스트.
- policy rollback 회귀 세트 고정.

---

## 5) 결론
현재 AIOS는 "AI-네이티브 커널"의 구조적 방향성과 MVP 골격이 잘 잡혀 있습니다. 다만 다음 단계의 핵심은 기능 추가보다 **검증 자동화, 시간/성능 모델 정합성, HAL 실구현 경로, autonomy 평가지표 내재화**입니다. 이 4가지를 먼저 닫으면, 이후 로드맵(학습 증류/프로파일 특화/분산 확장)의 성공 확률이 크게 올라갑니다.
