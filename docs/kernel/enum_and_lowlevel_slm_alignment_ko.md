# Enum 무결성 + 저레벨 SLM 정렬 기록 (2026-04-10)

## 점검 기준
- 로컬/원격 동기 상태 확인: `main == origin/main` (기준 커밋: `9fb161c`)
- 외부 가이드 반영:
  - `aios_enum_integrity_guide.md`
  - `aios_kernel_lowlevel_slm_plan.md`

## 이번 반영 내용

### 1) Enum 무결성 강화
- `include/kernel/health.h`
  - `kernel_subsystem_id_t`를 explicit numbering으로 고정
  - `KERNEL_SUBSYSTEM_COUNT = 16` 명시
  - `kernel_subsystem_id_valid()` 헬퍼 추가
- `include/drivers/platform_probe.h`
  - `platform_device_kind_t`를 explicit numbering으로 고정
  - `PLATFORM_DEVICE_KIND_COUNT = 6` 추가
  - `platform_device_kind_valid()` 헬퍼 추가
- `include/runtime/slm_orchestrator.h`
  - `SLM_ACTION_COUNT = 11` 명시
  - `slm_action_valid()` 헬퍼 추가
- `include/mm/tensor_mm.h`
  - `DTYPE_INVALID = 255` sentinel 추가
  - `tensor_dtype_valid()` 헬퍼 추가
- `include/runtime/autonomy.h`
  - `AUTONOMY_REASON_COUNT = 7`, `ACTION_STATE_COUNT = 5` 추가
  - `autonomy_target_support_t` 도입
  - 타깃/사유/state 검증용 헬퍼 추가

### 2) 컴파일타임 안전장치 추가
- `include/kernel/types.h`
  - `AIOS_STATIC_ASSERT` 매크로 추가
- 아래 enum/테이블에 정합성 assert 적용
  - `KERNEL_SUBSYSTEM_COUNT == 16`
  - `PLATFORM_DEVICE_KIND_COUNT == 6`
  - `SLM_ACTION_COUNT == 11`
  - `AUTONOMY_REASON_COUNT == 7`
  - `ACTION_STATE_COUNT == 5`

### 3) Autonomy semantic integrity 개선
- `runtime/autonomy.c`
  - 타깃 지원도 매트릭스(`none/observe-only/apply`) 추가
  - 기존 “scheduler만 실제 적용” 상태를 코드로 명시
  - invalid target은 `BAD_TARGET`, 미구현 target은 `UNSUPPORTED_TARGET`으로 분리
  - dump 로그에 target 이름/지원도 출력 추가

## 현재 상태 평가
- 즉시 충돌/중복 같은 enum 번호 문제는 없음
- 확장 시 발생 가능한 ABI drift 위험을 낮추는 방향으로 구조 보강 완료
- 저레벨 SLM 정책 제안 모델의 action-space 고정/검증 기반 강화 완료

## 다음 단계 (우선순위)
1. `enum -> name` 테이블을 telemetry/serial 경로에 일괄 적용
2. `runtime/slm_orchestrator.c`의 policy action별 verifier score 분리
3. OS 계층(`os/tools`) JSONL 생성기에 action whitelist 검증 규칙 연동
4. QEMU 반복 실험 루프에서 rollback 발생 샘플 자동 태깅
