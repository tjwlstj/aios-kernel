# AIOS Driver Model / Stack Foundation (2026-04-10)

## 목적

드라이버 수를 무작정 늘리는 대신, AIOS 커널이 공통된 기준으로
현재 드라이버 상태를 표현하고 SLM/규칙 엔진이 같은 구조를 참조하도록
기반 레이어를 정리한다.

이번 단계의 핵심은 다음과 같다.

1. `net`, `usb`, `storage`를 공통 `driver class`로 표현
2. 각 드라이버의 성숙도를 `stage`로 표현
3. 장치별 기본 policy hint를 공통 구조로 수집
4. SLM I/O profile 계산이 개별 드라이버 하드코딩 대신 공통 stack snapshot을 사용

## 새 구조

관련 파일:

- [include/drivers/driver_model.h](/Z:/aios/aios-kernel/include/drivers/driver_model.h)
- [drivers/driver_model.c](/Z:/aios/aios-kernel/drivers/driver_model.c)

핵심 타입:

- `driver_class_t`
  - `NET`
  - `USB`
  - `STORAGE`
- `driver_stage_t`
  - `DISCOVERED`
  - `BOOTSTRAP`
  - `CONTROL_READY`
  - `PARTIAL_IO`
  - `ACTIVE_IO`
  - `DEGRADED`
- `driver_profile_id_t`
  - `INTEL_E1000`
  - `XHCI`
  - `IDE_COMPAT`
  - `AHCI`
  - `NVME`
  - generic profiles
- `driver_policy_hint_t`
  - `queue_depth_hint`
  - `poll_budget_hint`
  - `dma_window_kib_hint`
  - `confidence`
  - `observe_only`
- `driver_stack_snapshot_t`
  - 클래스별 엔트리 수집
  - ready/degraded/PCIe count
  - merged policy

## 현재 stage 기준

이번 기준은 "구현 사실"을 과장하지 않도록 설계했다.

- `DISCOVERED`
  - PCI probe로 장치는 보이지만 해당 드라이버가 아직 올라오지 않음
- `BOOTSTRAP`
  - 드라이버가 init 중이거나 BAR/MMIO/IO 정도만 확보한 상태
- `CONTROL_READY`
  - 제어 레지스터/기본 capability까지는 읽을 수 있으나 일반 data path는 아직 없음
- `PARTIAL_IO`
  - 제한된 data path checkpoint가 있음
  - 현재는 `e1000`의 `RX ring bootstrap + bounded RX poll/rearm + TX smoke`가 여기에 해당
- `ACTIVE_IO`
  - 일반 I/O 경로가 실제로 동작하는 단계
  - 현재 저장소에는 아직 없음
- `DEGRADED`
  - 장치는 있으나 링크/채널/기본 capability가 기대 수준에 못 미침

## 현재 드라이버 매핑

- `e1000`
  - `ready + link up + rx ring/bootstrap or tx smoke`면 `PARTIAL_IO`
  - bounded RX poll/rearm은 있으나 아직 일반 RX path와 상위 네트워크 스택은 없으므로 `ACTIVE_IO`로 올리지 않음
- `usb_host`
  - `xHCI capability probe` 수준이면 `CONTROL_READY`
- `storage_host`
  - IDE live/status 또는 AHCI/NVMe MMIO bootstrap이면 `CONTROL_READY`

## SLM과의 연결

기존에는 `slm_orchestrator.c`가 다음을 직접 하드코딩했다.

- `e1000_driver_ready()`
- `usb_host_ready()`
- `storage_host_ready()`

이번 변경 후에는 `driver_model_snapshot_read()` 결과를 사용해:

- ready/degraded controller 수 계산
- PCIe I/O device 수 계산
- merged queue/poll/DMA hint 계산
- action confidence seed 계산

즉, 드라이버 개별 구현은 그대로 두되, 정책 계층이 바라보는 관측면을
공통 구조로 묶었다.

## 의도적으로 남겨둔 것

- SLM action 자체는 아직 `e1000`, `usb`, `storage` 개별 액션을 사용한다.
- `e1000`에는 bounded `RX poll` 액션이 추가됐지만, 이는 descriptor consume/rearm 관측용이며 일반 RX 스택을 의미하지는 않는다.
- `driver model`은 아직 별도 syscall ABI로 노출하지 않았다.
- `ACTIVE_IO` 단계에 해당하는 일반 RX/read/transfer 경로는 아직 없다.
- bounded RX poll은 들어갔지만 interrupt/NAPI류 일반 RX 경로는 아직 없다.
- `wireless`, `bluetooth`는 분류만 있고 driver model 엔트리로는 아직 승격하지 않았다.

## 다음 추천 단계

1. `driver model`을 SLM snapshot의 별도 서브블록으로 노출
2. `net/storage/usb` class ops 테이블 도입
3. class 공통 `dump/reset/health/policy apply` 인터페이스 추가
4. `e1000 일반 RX path`, `storage identify/read`, `xHCI transfer ring`을 추가해 `ACTIVE_IO`로 승격
