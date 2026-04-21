# AIOS 유저공간 OS 구현 방향 정리

작성일: 2026-04-19

## 목적

이 문서는 현재 `AIOS` 커널 위에 어떤 형태의 유저공간 운영체제를 올릴 것인지,
"지금 당장 구현 가능한 방향" 기준으로 정리한 메모다.

기존의 `user_space_compat_architecture_ko.md`가 호환성 중심 설계와 기술 선택지를 넓게 다룬다면,
이 문서는 그보다 한 단계 좁혀서 아래 질문에 답한다.

- 현재 커널이 유저공간에 실제로 넘겨줄 수 있는 기반은 무엇인가
- 첫 번째 유저공간 OS는 어떤 서비스부터 올라와야 하는가
- `SLM 모델 선택` 문제를 어떤 운영 구조로 완화할 수 있는가
- 어떤 순서로 구현해야 현재 커널 기준선과 충돌하지 않는가

## 현재 구현 기준 현실 체크

2026-04-21 기준 저장소에서 확인되는 사실은 아래와 같다.

- 커널은 부팅, ACPI/PCI 탐지, health summary, driver bootstrap, SLM seed까지는 수행한다
- `kernel/kernel_room.c`는 read-only `Kernel Room snapshot`과 `Axis Gate` 메타데이터를 노출한다
- `mm/memory_fabric.c`는 멀티 에이전트용 domain/window 기반 공유 메모리 기초를 제공한다
- `runtime/ai_syscall.c`는 AI syscall surface와 일부 ring runtime 통계를 제공한다
- `SYS_INFO_BOOTSTRAP`은 health / room / user scaffold / SLM snapshot을 첫 userspace용 묶음으로 읽게 해준다
- `runtime/slm_orchestrator.c`는 boot-time hardware-aware seed plan을 만든다
- `slm_hw_snapshot_t`는 userspace AI용 하드웨어 접근 힌트, 클럭 분배 힌트, SLM runtime state/status, read-only NodeBit 카탈로그를 포함한다
- `SYS_SLM_NODEBIT_LOOKUP`은 특정 API/tool/device/policy NodeBit를 단건 조회하며, SLM plan submit은 NodeBit runtime overlay를 1차 gate로 사용한다
- `include/kernel/user_access.h`와 `kernel/user_access.c`는 구조적 `access_ok`, `copy_from_user`, `copy_to_user`, 길이 제한 문자열 복사 및 실패 사유 매핑을 제공한다
- `runtime/ai_syscall.c`의 일부 구조체 입력 syscall은 request를 커널 스택에 staging copy한 뒤 내부 API를 호출한다
- `SYS_INFO_BOOTSTRAP`과 `SYS_SLM_HW_SNAPSHOT`은 큰 snapshot을 커널 staging copy로 만든 뒤 userspace로 복사한다
- `kernel/user_mode.c` 기준 ring3 scaffold marker는 있으나, 실제 userspace handoff와 ELF loader는 아직 없다
- `user_access`는 아직 page table 기반 소유권 검사가 아니라 null / zero-size / overflow / flag 검증 중심의 초기 경계다

즉, 현재 AIOS는
`부팅 가능한 AI 커널 + read-only 관측 기반 + 제한된 control plane`
까지는 올라와 있지만,
아직 `유저공간 운영체제가 실제로 실행되는 상태`는 아니다.

## 유저공간 OS의 역할 정의

이 프로젝트에서 유저공간 OS는 단순한 shell이나 앱 실행 계층이 아니다.
커널이 만든 하드웨어/메모리/health 기반 위에서,
메인 AI와 하위 노드 트리, 모델 서비스, 기억 서비스, 정책 브로커를 운영하는 상위 계층에 가깝다.

커널이 맡을 것:

- 시간원, 인터럽트, 메모리, DMA, 드라이버
- health / stability / degraded 판단
- `Kernel Room snapshot` 같은 read-only 상태 노출
- AI syscall, shared ring, shared window 같은 전달 primitive
- 위험한 apply 전의 gate / verifier 연결점

유저공간 OS가 맡을 것:

- `aios-init`와 코어 서비스 부팅 순서
- 메인 AI supervisor와 하위 agent tree 관리
- 모델 import / 변환 / adapter 운영
- 장기기억 journal / index / cold object 관리
- KV-cache tier policy와 hydrate/dehydrate
- SLM 후보군 평가, 승급, 강등, 롤백
- 외부 툴, WASI component, bundle 실행과 샌드박스

## 목표 상태 한 줄 정의

AIOS의 첫 유저공간 OS 목표는 다음처럼 잡는 것이 가장 현실적이다.

`부팅 커널 위에서 read-only snapshot과 bounded UAPI를 사용해, 메인 AI와 다수의 SLM 후보를 안정적으로 운영하는 supervisor 계층`

즉,
처음 목표는 "범용 데스크톱 OS"가 아니라
"AI agent runtime을 안전하게 올리는 유저공간 운영층"이다.

## 첫 번째 유저공간 OS 구성

초기 유저공간은 아래 서비스들로 나누는 편이 좋다.

### `aios-init`

- 첫 userspace 엔트리
- 최소 handle table / shared object map / bootstrap config 준비
- 나머지 코어 데몬 기동

### `aios-osd`

- 유저공간 control plane
- 서비스 생명주기, 설정, 복구 정책 담당
- boot mode와 degraded mode를 설명 가능한 상태로 유지

### `aios-agentd`

- 메인 AI supervisor
- 하위 node tree orchestration
- planner / verifier / summarizer / tool worker 관리

### `aios-modeld`

- 모델 import / validate / convert / load
- ONNX 등 외부 형식을 내부 실행 형식으로 내리는 변환기 역할
- adapter / LoRA artifact 연결

### `aios-memd`

- 장기기억 journal과 인덱스 관리
- 장기 object / adapter artifact / trace 저장
- 향후 persistent store로 가는 브리지

### `aios-kvcached`

- HOT / WARM / COLD KV-cache 정책 담당
- memory pressure와 hardware profile을 같이 보고 계층 전환
- 커널은 map/move만 하고 정책은 여기서 처리

### `aios-compatd`

- ELF loader
- WASI component host
- bundle launcher
- capability broker

## 실행 레인 방향

현재 AIOS 목적에 맞는 레인은 세 개면 충분하다.

### 1. Native lane

- 대상: 메인 AI supervisor, 모델 서비스, 고성능 worker
- 형식: `x86_64 ELF + SysV ABI`
- 특징: 가장 낮은 오버헤드, kernel memory/window와 결합이 쉬움

### 2. Component lane

- 대상: verifier, summarizer, distiller, tool adapter
- 형식: `WASI component`
- 특징: 언어 중립, 샌드박스 친화, 분리 배포가 쉬움

### 3. Bundle lane

- 대상: 외부 서비스 패키징과 배포
- 형식: OCI-like bundle
- 특징: 실행 자체보다 distribution과 재현성에 초점

즉, 첫 번째 유저공간 OS는
`native fast path + component sandbox path`
두 축을 먼저 잡고,
bundle은 배포 보조층으로 뒤따라오는 편이 맞다.

## SLM 선택 문제를 푸는 운영 방향

현재 가장 큰 난점 중 하나는
"처음부터 어떤 SLM이 맞는지 고르기 어렵다"는 점이다.

이 문제를 한 번의 모델 선택으로 풀기보다,
유저공간 OS에서 `후보군 운영 + 점진 승급` 구조로 푸는 것이 더 적합하다.

### 기본 원칙

- 커널은 모델을 선택하지 않는다
- 커널은 관측, 격리, 전달, 롤백 기반만 제공한다
- 실제 후보 평가와 승급/강등은 유저공간 정책 서비스가 맡는다
- 유저공간 AI의 하드웨어 접근은 현재 direct MMIO가 아니라 mediated I/O와 shared ring / zero-copy window 힌트 중심으로 제한한다

### 추천 구조

#### `seed SLM`

- 매우 작은 기본 모델
- 성능보다 안정적인 기동과 낮은 메모리 사용을 우선

#### `candidate registry`

- 여러 후보 SLM의 메모리 사용량, latency, success rate, task fit을 기록
- 단발성 benchmark가 아니라 지속 평가 방식으로 운영

#### `observer service`

- memory window, queue depth, hardware snapshot, health summary를 읽어
  어떤 후보가 실제 workload에 맞는지 관찰
- 커널은 read-only snapshot만 제공

#### `builder service`

- full training보다 adapter, distillation, routing table, prompt policy 보정부터 시작
- 커널 내부가 아니라 userspace service로 두는 편이 안정적

#### `promotion policy`

- `experimental -> bounded -> stable`
- stable 상태의 후보만 더 안쪽 역할이나 더 높은 privilege lane으로 승급

이 방향의 장점은 명확하다.

- 초기에 모델 하나를 정답처럼 고르지 않아도 된다
- 하드웨어 차이에 맞춰 다른 후보를 올릴 수 있다
- 실패한 후보를 버리고 다른 후보를 승급해도 커널 ABI는 유지된다
- 나중에 observer/builder 흐름을 붙여도 userspace 안에서 확장 가능하다

## 커널이 먼저 보완해야 할 최소 기반

유저공간 OS를 올리려면 커널에서 최소한 아래 기반이 필요하다.

### 이미 어느 정도 있는 것

- `Kernel Room snapshot`
- `Axis Gate` 메타데이터
- health / stability summary
- `SYS_INFO_BOOTSTRAP` read-only snapshot
- `SYS_SLM_HW_SNAPSHOT` read-only hardware / userspace-access / clock distribution / NodeBit catalog profile
- `SYS_SLM_NODEBIT_LOOKUP` effective NodeBit single-node lookup
- memory fabric domain / window
- AI syscall surface
- driver bootstrap / boot inventory / boot matrix testkit

### 아직 필요한 것

#### `ring3 handoff`

- 실제 user RIP / user RSP 전환
- user task가 커널에 다시 복귀할 수 있는 최소 entry

#### `static ELF loader`

- 첫 `aios-init`를 올리는 가장 작은 실행기

#### `object / wait / channel` 계열 UAPI

- 긴 작업을 syscall 블로킹 대신 submit/wait로 다루기 위한 경계

#### `copy_from_user` / `copy_to_user` / `access_ok` 계열 검증

- userspace 진입 후 가장 먼저 안정성을 좌우할 기초
- 현재는 `kernel/user_access.c`에 구조적 검증과 copy helper가 들어갔다
- tensor create, model load/info, inference ring, train forward, autonomy control, SLM plan status/list/submit 일부 경로는 이 helper 위에서 동작한다
- 큰 bootstrap / SLM hardware snapshot은 아직 구조적 guard 후 직접 채우는 경로가 남아 있다
- 다음 단계는 ring3 handoff / page table 정보와 연결해 실제 user range 및 권한 검사를 적용하는 것이다

#### `Cell metadata`

- `memory_fabric` domain을 user-space service와 연결할 수 있게 하는 논리 구획 정보

즉, 지금 커널에서 가장 중요한 다음 작업은
"더 큰 AI 기능 추가"보다
"유저공간을 받아들일 최소 실행 경계"를 완성하는 것이다.

## 단계별 구현 방향

### Phase 1. `aios-init`를 띄운다

목표:

- ring3 handoff
- static ELF loader
- serial 기반 첫 userspace 로그

완료 기준:

- `aios-init`가 부팅 후 한 줄 이상 로그를 남긴다

### Phase 2. 유저공간 bootstrap service를 분리한다

목표:

- `aios-init`
- `aios-osd`
- `aios-compatd`

완료 기준:

- 최소한 하나의 service를 별도 userspace binary로 실행

### Phase 3. agent runtime을 userspace로 올린다

목표:

- `aios-agentd`
- `aios-modeld`
- `aios-kvcached`

완료 기준:

- 메인 AI supervisor가 커널 내부 control plane 대신 userspace coordinator로 동작

### Phase 4. 후보형 SLM 운영 구조를 붙인다

목표:

- seed SLM
- candidate registry
- observer service
- promotion policy

완료 기준:

- 모델 선택이 정적 설정이 아니라 runtime 승급/강등 루프로 동작

### Phase 5. observer / builder 확장

목표:

- continuity trace
- adapter/distillation 기반 미세 조정
- 실패 시 rollback / quarantine

완료 기준:

- builder 결과가 직접 base model을 덮지 않고,
  bounded artifact와 승급 정책을 통해 반영됨

## 하지 말아야 할 것

- userspace가 없는데 메인 AI OS가 이미 완성된 것처럼 쓰지 않기
- 커널 내부에 큰 학습 루프를 넣기
- `LONG_TERM` 메모리 프로파일을 영구 저장소와 같은 뜻으로 과장하지 않기
- `flow vector` 같은 설계 파라미터를 너무 빨리 ABI 상수로 고정하지 않기
- 후보 평가와 privilege 승급을 health gate 없이 직접 연결하지 않기

## 문서 연결

같이 보는 문서:

- `docs/user_space_compat_architecture_ko.md`
- `docs/user_space_os_build_slices_ko.md`
- `docs/autonomous_os_execution_roadmap_ko.md`
- `docs/kernel-room/kernel_room_topology_ko.md`
- `docs/kernel-room/development_guide_ko.md`
- `os/README.md`
- `os/runtime/README.md`

## 결론

현재 AIOS에 맞는 유저공간 OS 방향은
`커널은 최소 고성능 기반`,
`유저공간은 메인 AI와 후보형 SLM 운영층`
으로 분리하는 것이다.

첫 번째 목표는
`shell이 풍부한 범용 OS`가 아니라,
`부팅 커널 위에 메인 AI supervisor와 코어 서비스를 안정적으로 올릴 수 있는 userspace substrate`
를 만드는 데 있다.

그리고 SLM 문제도
`정답 모델 하나 선택`
보다
`작은 seed + 후보군 운영 + 점진 승급`
구조로 푸는 편이 지금 프로젝트와 더 잘 맞는다.
