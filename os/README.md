# AIOS OS Layer

> 문서 역할: native reference/proof용 OS 도메인 진입 문서
>
> 구현 경계: bounded ring3/static ELF 실행 증거는 `CURRENT`, 전체 native process
> 모델은 `PARTIAL`이다. 장기 실행 native userspace 서비스는 `PLANNED`다.

이 디렉토리는 AIOS native kernel 이후 ring3에서 실행할 상위 OS 계층의 설계와
host 도구를 둔다. 현재 동작하는 bounded 실행 증거는 `kernel/`이 소유하며,
이 디렉터리에 장기 실행 userspace 서비스 구현이 존재한다는 뜻은 아니다.
Linux-hosted 기본 delivery userspace service는 별도 `../hosted/` 도메인이 소유하며,
이 디렉터리에 섞지 않는다.

목적:

- 메인 AI 상태모델과 하위 트리 구조를 커널과 분리해 설계
- 장기기억, 온라인 학습, adapter 운영 도구를 커널 바깥 계층으로 정리
- 추후 user-space agent runtime, policy daemon, memory service의 기반을 제공

초기 구조:

- `main_ai/`
  메인 AI의 상위 설계와 상태/노드 트리 매니페스트
- `runtime/`
  커널 부팅 이후 ring3에서 동작할 유저 공간 런타임 설계. 실제 core service는
  아직 `PLANNED`
- `compat/`
  WASI component / native ELF / OCI bundle 호환 계층 설계
- `tools/`
  정적-혼돈 점수 계산, 학습 데이터셋 정리, 코퍼스 통계 도구
- `examples/`
  메트릭/학습 trace 예제
- `apps/`
  AIOS 전용 프로그램(에이전트 앱) 배치 영역 (스캐폴드, `apps/README.md` 참고)

설계 원칙:

- 커널은 하드웨어, 시간원, 메모리, 스케줄, 안전한 액추에이터에 집중
- OS 계층은 메인 AI, 하위 트리, 기억, 학습 파이프라인을 담당
- 메인 AI만 정적-혼돈 연산자를 사용하고, 하위 노드는 소형 역할 모델로 구성

현재 커널 연결점:

- `SYS_SLM_HW_SNAPSHOT`
  하드웨어 상태와 함께 메인 AI operator, agent tree, pipeline optimization, NodeBit catalog 힌트를 함께 노출
- `SYS_SLM_NODEBIT_LOOKUP`
  userspace policy broker가 특정 API/tool/device/policy 노드를 단건 조회하는 빠른 경로
- `kernel/runtime/slm_orchestrator.c`
  커널 텔레메트리에서 메인 AI 모드, worker 수, queue depth, token pipeline depth를 계산
- `kernel/include/runtime/ai_ring.h`
  커널-유저 공간이 함께 쓰는 shared submit/completion ring ABI 초안

현재 작업·문서 진입점:

- [통합 작업 가이드](../docs/meta/integrated_work_guide_ko.md)
  요청을 분류하고 현재 정본·작업 준비서·검증 경로를 고르는 첫 진입점
- [성숙도 우선 작업흐름](../docs/meta/minimal_io_and_maturity_workflow_ko.md)
  K/M/C/W/H축의 현재 우선순위와 구현 성숙도 정본
- [전체 문서 인덱스](../docs/README.md)
  도메인별 정본, 작업 준비서, 참고·역사 문서의 상태 색인
- [H1 binding trace/replay 작업 준비서](../docs/os/h1_binding_trace_replay_workplan_ko.md)
  현재 직접 조각인 OS-neutral H1 계약. 구현 성숙도는 `PLANNED`
- [AI resource 관리 개발 계획](../docs/autonomy/ai_resource_management_development_plan_ko.md)
  native resource subsystem 내부 계획이며 프로젝트 전역 우선순위를 대체하지 않음
- [runtime/README.md](runtime/README.md)
  native user-space runtime의 목표 역할과 현재 구현 경계
- `compat/wit/aios-agent-host.wit`
  WASI component용 AIOS host interface 초안

역사·재검토 문서:

- [유저 공간 OS 아키텍처](../docs/os/user_space_compat_architecture_ko.md) — `OLD`; ring3 caller 이전 설계
- [유저공간 OS 구현 방향](../docs/os/user_space_os_direction_ko.md) — `OLD`; 현재 작업 순서로 사용하지 않음
- [유저공간 OS 세분화 빌드 계획](../docs/os/user_space_os_build_slices_ko.md) — `OLD`; M1/M2 이전 순서
- [코드 경계와 구조 트리](../docs/kernel/code_boundary_and_structure_tree_ko.md) — `REVIEW`; 일부 구조 원칙만 참고

`OLD`/`REVIEW` 문서는 역사적 배경 확인용이다. 현재 작업 선정과 성숙도 판정에는
통합 작업 가이드, 성숙도 우선 작업흐름, 전체 문서 인덱스를 먼저 사용한다.
