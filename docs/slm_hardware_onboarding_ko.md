## SLM Hardware Onboarding

### 목적

AIOS의 SLM 계층은 커널 안에서 직접 "모든 드라이버를 생성"하는 것이 아니라,
하드웨어 상태를 표준화된 스냅샷으로 노출하고 안전한 드라이버 액션을
플랜 단위로 제어할 수 있게 하는 오케스트레이션 계층이다.

현재 목표는 다음 세 가지다.

1. 부팅 직후 하드웨어 인벤토리와 성능 티어를 읽을 수 있게 한다.
2. SLM 또는 상위 에이전트가 안전한 드라이버 액션만 제안/적용하게 한다.
3. 실패한 장치 초기화를 telemetry와 plan state로 추적 가능하게 만든다.

### 현재 커널 통합 상태

- 초기화 훅: `slm_orchestrator_init()`
- 스냅샷 ABI: `slm_snapshot_read()`
- 플랜 ABI:
- `slm_plan_submit()`
- `slm_plan_apply()`
- `slm_plan_get()`
- `slm_plan_list()`
- syscall 번호:
- `SYS_SLM_HW_SNAPSHOT`
- `SYS_SLM_PLAN_SUBMIT`
- `SYS_SLM_PLAN_APPLY`
- `SYS_SLM_PLAN_STATUS`
- `SYS_SLM_PLAN_LIST`

관련 파일:

- [runtime/slm_orchestrator.c](/Z:/aios/aios-kernel/runtime/slm_orchestrator.c)
- [include/runtime/slm_orchestrator.h](/Z:/aios/aios-kernel/include/runtime/slm_orchestrator.h)
- [runtime/ai_syscall.c](/Z:/aios/aios-kernel/runtime/ai_syscall.c)
- [include/runtime/ai_syscall.h](/Z:/aios/aios-kernel/include/runtime/ai_syscall.h)

### 스냅샷에 포함되는 정보

- monotonic timestamp
- calibrated TSC 정보
- boot performance tier
- memory microbench throughput
- PCI probe 결과 요약
- 장치 목록
- `e1000` 준비 상태와 링크 상태
- USB host 준비 상태와 컨트롤러 타입
- storage host 준비 상태와 컨트롤러 타입
- I/O profile
  - ready/degraded controller 수
  - PCIe I/O device 수
  - recommended queue depth
  - recommended poll budget
  - recommended DMA window

이 스냅샷은 SLM이 "무슨 하드웨어가 있고 지금 어느 정도 준비됐는가"를
짧은 컨텍스트로 이해하도록 돕는다.

### 현재 액션 템플릿

지원 템플릿:

- `SLM_TEMPLATE_DISCOVERY`
- `SLM_TEMPLATE_PCI_ETHERNET`
- `SLM_TEMPLATE_PCI_USB`
- `SLM_TEMPLATE_PCI_STORAGE`

현재 구현된 액션:

- `SLM_ACTION_REPROBE_PCI`
- `SLM_ACTION_BOOTSTRAP_E1000`
- `SLM_ACTION_E1000_TX_SMOKE`
- `SLM_ACTION_E1000_DUMP`
- `SLM_ACTION_BOOTSTRAP_USB`
- `SLM_ACTION_USB_DUMP`
- `SLM_ACTION_BOOTSTRAP_STORAGE`
- `SLM_ACTION_STORAGE_DUMP`
- `SLM_ACTION_IO_AUDIT`

현재 구현은 모두 "bootstrap/dump" 중심이다.
즉, 장치 레지스터 접근과 준비 상태 확인까지는 가능하지만, 실제 USB transfer,
storage read/write 같은 데이터 경로는 아직 없다.

I/O 강화 포인트:

- 부팅 시 I/O profile을 계산한다.
- 모든 seeded plan에 queue/poll/DMA hint를 붙인다.
- `IO_AUDIT` plan으로 네트워크/USB/storage 상태를 한 번에 검토할 수 있다.

### 부팅 시 자동 시드되는 플랜

SLM 오케스트레이터는 부팅 시 다음과 같은 저위험 plan을 자동으로 만든다.

1. PCI inventory refresh plan
2. I/O audit plan
3. Ethernet bootstrap 또는 dump plan
4. `e1000` 준비가 끝난 경우 TX smoke plan
5. USB bootstrap 또는 dump plan
6. storage bootstrap 또는 dump plan

이 플랜들은 기본적으로 `allow_apply=false`로 생성된다.
즉, 현재는 자동 실행보다 "추천 큐 생성"이 목적이다.

### 안전 모델

SLM은 raw MMIO write를 직접 수행하지 않는다.
커널이 허용한 액션만 플랜으로 제출하고, apply 시에도 허용된 드라이버
엔트리포인트만 호출한다.

현재 안전 원칙:

- template/action 조합 검증
- risk level 보존
- queue/poll/DMA hint 상한 검증
- `allow_apply`가 켜진 플랜만 실행
- 실행 결과를 `APPLIED` 또는 `FAILED`로 기록

### 현재 한계

- user-space SLM 런타임은 아직 없음
- `e1000`는 bootstrap 수준이며 TX smoke도 아직 실패 가능
- USB는 `xHCI` 기준 bootstrap/dump만 있음
- storage는 IDE/AHCI/NVMe 분류와 bootstrap/dump만 있음
- wireless, bluetooth는 아직 분류와 plan 템플릿만 있음
- queue/poll/DMA hint는 아직 실제 드라이버 큐 크기나 DMA allocator까지 연결되진 않음

### 다음 추천 단계

1. plan list/read-next API 추가
2. user-space SLM daemon 또는 agent runtime 추가
3. queue/poll/DMA hint를 실제 NIC/USB/storage 드라이버 설정값에 연결
4. USB transfer ring 또는 AHCI port reset 같은 실제 데이터 경로 추가
5. plan apply 결과를 autonomy verifier와 연결
