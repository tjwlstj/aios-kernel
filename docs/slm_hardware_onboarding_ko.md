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
- `SLM_ACTION_E1000_RX_POLL`
- `SLM_ACTION_E1000_TX_SMOKE`
- `SLM_ACTION_E1000_DUMP`
- `SLM_ACTION_BOOTSTRAP_USB`
- `SLM_ACTION_USB_DUMP`
- `SLM_ACTION_BOOTSTRAP_STORAGE`
- `SLM_ACTION_STORAGE_DUMP`
- `SLM_ACTION_IO_AUDIT`
- `SLM_ACTION_CORE_AUDIT`

현재 구현은 "bootstrap/dump + 기초 capability smoke" 중심이다.
즉, 장치 레지스터 접근과 준비 상태 확인은 가능하고,
- `e1000`는 지원되는 Intel NIC 후보 중 부트스트랩 호환성 점수가 높은 장치를 고른 뒤, QEMU 기준 `link up + RX ring bootstrap + bounded RX poll/rearm + TX smoke PASS`
- USB는 발견된 host controller를 호환성 점수로 고른 뒤 `xHCI`면 capability probe까지 수행
- storage는 발견된 controller를 호환성 점수로 고른 뒤 IDE면 channel status probe까지 수행
까지는 올라왔다.

다만 `e1000`의 RX descriptor를 한 번씩 consume/rearm 하는 bounded poll만 있고,
일반 패킷 수신 경로나 상위 네트워크 스택은 아직 미완이며,
실제 USB transfer, storage read/write 같은 일반 데이터 경로도 아직 없다.

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
5. `e1000` 준비가 끝난 경우 bounded RX poll plan
6. USB bootstrap 또는 dump plan
7. storage bootstrap 또는 dump plan

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
- kernel health 기반 risky I/O plan 차단
- `allow_apply`가 켜진 플랜만 실행
- `e1000 RX poll`에서 completed frame이 없어도 no-op success로 처리
- 실행 결과를 `APPLIED` 또는 `FAILED`로 기록

### 현재 한계

- user-space SLM 런타임은 아직 없음
- `e1000`는 지원되는 Intel NIC 후보 중 부트스트랩에 더 유리한 장치를 고른 뒤, QEMU 기준 RX descriptor ring bootstrap과 bounded RX poll/rearm, TX smoke까지 성공하고 SLM action으로도 호출 가능하지만 일반 RX path는 아직 없음
- USB는 controller별 data path 없이, 후보군 중 부트스트랩에 더 유리한 host를 골라 `xHCI` capability probe까지만 수행
- storage는 후보군 중 부트스트랩에 더 유리한 controller를 고르고, IDE channel probe와 AHCI/NVMe 분류까지만 있음
- wireless, bluetooth는 아직 분류와 plan 템플릿만 있음
- queue/poll/DMA hint는 아직 실제 드라이버 큐 크기나 DMA allocator까지 연결되진 않음
- health gate는 들어갔지만 subsystem restart나 watchdog 기반 복구는 아직 없음

### 다음 추천 단계

1. plan list/read-next API 추가
2. user-space SLM daemon 또는 agent runtime 추가
3. queue/poll/DMA hint를 실제 NIC/USB/storage 드라이버 설정값에 연결
4. USB transfer ring 또는 AHCI port reset 같은 실제 데이터 경로 추가
5. plan apply 결과를 autonomy verifier와 연결
