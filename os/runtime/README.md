# AIOS User-Space Runtime

> 문서 역할: native userspace runtime 설계 진입 문서
>
> 구현 경계: 커널 내장 static ELF의 bounded ring3 순차 실행 증거는 `CURRENT`다.
> 일반 process runtime은 `PARTIAL`이며, 아래 core service와 component/bundle
> 실행체는 `PLANNED`다.

이 디렉토리는 AIOS native kernel 부팅 이후 ring3에서 동작할 유저 공간 런타임의
설계 기준 위치다. 현재 `os/runtime/`에는 장기 실행 service binary가 없다.
Linux-hosted 기본 delivery runtime은 `../../hosted/`의 별도 제품
도메인이며 이 경로의 service와 동일한 실행 환경으로 간주하지 않는다.

## 목표 역할 (`PLANNED`)

- 메인 AI supervisor 실행
- 하위 노드 트리 orchestration
- 모델 서비스 / 메모리 서비스 / KV-cache 서비스 구동
- native ELF 실행과 WASI component 실행을 함께 수용
- 커널 AI syscall / SLM snapshot / health gate를 사용자 공간 정책으로 연결

## 계획된 코어 서비스

아래 서비스는 모두 이름과 책임의 설계이며 실행 구현은 `PLANNED`다.

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

## 데이터 평면 설계

- control plane 후보는 기존 AI syscall 표면을 사용한다.
- `kernel/include/runtime/ai_ring.h`의 shared submit/completion ring 등록 표면은
  존재하지만 장기 실행 userspace consumer는 아직 없다.
- 고빈도 `infer submit / completion`을 shared memory로 넘기고 syscall을
  등록/notify/wait에 한정하는 서비스 data path는 `PLANNED`다.
- 메인 AI와 worker가 같은 ring ABI와 kernel snapshot 힌트를 소비하는 runtime
  통합도 `PLANNED`다.

## 계획된 실행 레인

### Native lane

- 형식: x86_64 ELF
- ABI: SysV ABI
- 현재 증거: 커널 내장 static ELF64 데모의 bounded 순차 실행만 `CURRENT`
- 목표 용도: 메인 AI, 모델 서비스, 고성능 worker (`PLANNED`)

### Component lane

- 형식: WASI 0.2 component
- 인터페이스: WIT
- 목표 용도: verifier, summarizer, distiller, plugin worker (`PLANNED`)

### Bundle lane

- 형식: OCI-like bundle
- 목표 용도: 배포/패키징/재현 가능한 실행 (`PLANNED`)

## 세분화 구조

- `bootstrap/`
  bounded ring3/static ELF proof와 아직 `PLANNED`인 `aios-init` 경계를 설명
- `services/`
  `PLANNED`인 `aios-osd`, `aios-agentd`, `aios-modeld`, `aios-memd`,
  `aios-kvcached`, `aios-compatd`
- `policy/`
  seed SLM, candidate registry, observer/builder, promotion policy

## 커널 연결점과 서비스 경계

아래 이름 중에는 현재 커널 ABI 표면이 존재하는 것도 있지만, ABI 존재가 이
디렉터리의 userspace service 구현이나 end-to-end runtime 지원을 뜻하지 않는다.

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

향후 userspace consumer가 생기면 `SYS_INFER_*`를 다음 두 층으로 분리한다.

- control path: ring 등록, health gate, notify, completion wait
- data path: submit/completion shared ring

## 장기 호환성 순서 (`PLANNED`)

이 순서는 native runtime 내부의 장기 설계 순서이며, 프로젝트 전체의 현재 작업
우선순위가 아니다.

1. native ELF 안정화
2. POSIX-lite libc shim
3. WASI component host
4. OCI bundle import
5. ONNX import pipeline

## 현재 작업·문서 진입점

- [통합 작업 가이드](../../docs/meta/integrated_work_guide_ko.md)
  요청별 정본과 검증 경로를 선택하는 첫 진입점
- [성숙도 우선 작업흐름](../../docs/meta/minimal_io_and_maturity_workflow_ko.md)
  현재 K/M/C/W/H축 우선순위와 native execution substrate 경계
- [전체 문서 인덱스](../../docs/README.md)
  현재 정본, 작업 준비서, `OLD`/`REVIEW` 상태 확인

다음 문서는 모두 경로를 보존한 역사 자료이며 현재 작업 큐로 사용하지 않는다.

- [유저공간 OS 구현 방향](../../docs/os/user_space_os_direction_ko.md) — `OLD`
- [유저공간 OS 세분화 빌드 계획](../../docs/os/user_space_os_build_slices_ko.md) — `OLD`
- [유저 공간 OS 아키텍처](../../docs/os/user_space_compat_architecture_ko.md) — `OLD`
