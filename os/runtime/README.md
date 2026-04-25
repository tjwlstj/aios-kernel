# AIOS User-Space Runtime

이 디렉토리는 커널 부팅 이후 ring3에서 동작할 AIOS 유저 공간 런타임의 기준 위치다.

## 역할

- 메인 AI supervisor 실행
- 하위 노드 트리 orchestration
- 모델 서비스 / 메모리 서비스 / KV-cache 서비스 구동
- native ELF 실행과 WASI component 실행을 함께 수용
- 커널 AI syscall / SLM snapshot / health gate를 사용자 공간 정책으로 연결

## 계획된 코어 서비스

- `aios-init`
  PID1. early user-space bootstrap 담당
- `aios-osd`
  유저 공간 control plane
- `aios-agentd`
  메인 AI와 하위 트리 관리
- `aios-modeld`
  모델 import/load/convert 담당
- `aios-memd`
  장기기억 / journal / adapter artifact 관리
- `aios-kvcached`
  HOT/WARM/COLD KV-cache 정책, TurboQuant / kvtc orchestration
- `aios-compatd`
  ELF loader, WASI host, OCI bundle launcher

## 데이터 평면

- control plane은 기존 AI syscall을 유지
- data plane은 `include/runtime/ai_ring.h` 기준의 shared submit/completion ring을 사용
- 고빈도 `infer submit / completion`은 shared memory로 넘기고, syscall은 등록/notify/wait에 한정
- 메인 AI와 worker는 같은 ring ABI를 쓰되, queue depth, ring entry 수, zero-copy window는 kernel snapshot 힌트를 따른다

## 실행 레인

### Native lane

- 형식: x86_64 ELF
- ABI: SysV ABI
- 용도: 메인 AI, 모델 서비스, 고성능 worker

### Component lane

- 형식: WASI 0.2 component
- 인터페이스: WIT
- 용도: verifier, summarizer, distiller, plugin worker

### Bundle lane

- 형식: OCI-like bundle
- 용도: 배포/패키징/재현 가능한 실행

## 세분화 구조

- `bootstrap/`
  ring3 handoff, ELF loader, `aios-init` 같은 첫 userspace 진입 조각
- `services/`
  `aios-osd`, `aios-agentd`, `aios-modeld`, `aios-memd`, `aios-kvcached`, `aios-compatd`
- `policy/`
  seed SLM, candidate registry, observer/builder, promotion policy

## 커널 연결점

- `SYS_SLM_HW_SNAPSHOT`
  hardware, health, main AI mode, pipeline hints, agent tree, NodeBit catalog 읽기
- `SYS_SLM_NODEBIT_LOOKUP`
  특정 API/tool/device/policy NodeBit를 단건 조회해 userspace policy broker가 빠르게 gate 판단
- `SYS_INFO_BOOTSTRAP`
  health / room / user scaffold / SLM snapshot을 early userspace가 한 번에 읽는 bootstrap surface
- `SYS_MODEL_*`
  모델 lifecycle
- `SYS_TENSOR_*`
  tensor allocation / control
- `SYS_INFER_*`
  inference submission / wait
- `SYS_AUTONOMY_*`
  안전한 정책 변경 / rollback
- `SYS_SLM_PLAN_*`
  드라이버 / I/O plan 관리

향후에는 `SYS_INFER_*`를 다음 두 층으로 분리한다.

- control path: ring 등록, health gate, notify, completion wait
- data path: submit/completion shared ring

## 호환성 우선순위

1. native ELF 안정화
2. POSIX-lite libc shim
3. WASI component host
4. OCI bundle import
5. ONNX import pipeline

## 현재 방향 문서

- `../../docs/user_space_os_direction_ko.md`
  현재 커널 구현 상태를 기준으로, 어떤 userspace OS를 먼저 올릴지와
  `seed SLM -> candidate registry -> promotion policy` 방향을 정리한 문서
- `../../docs/user_space_os_build_slices_ko.md`
  `ring3 -> loader -> init -> service -> policy` 순서로 더 잘게 자른 구현 계획 문서
- `../../docs/user_space_compat_architecture_ko.md`
  호환성, 실행 레인, ABI 선택지를 더 넓게 다루는 상위 설계 문서
