# AIOS OS Layer

이 디렉토리는 커널 소스와 분리된 상위 OS 계층이다.

목적:

- 메인 AI 상태모델과 하위 트리 구조를 커널과 분리해 설계
- 장기기억, 온라인 학습, adapter 운영 도구를 커널 바깥 계층으로 정리
- 추후 user-space agent runtime, policy daemon, memory service의 기반을 제공

초기 구조:

- `main_ai/`
  메인 AI의 상위 설계와 상태/노드 트리 매니페스트
- `runtime/`
  커널 부팅 이후 ring3에서 동작할 유저 공간 런타임 설계
- `compat/`
  WASI component / native ELF / OCI bundle 호환 계층 설계
- `tools/`
  정적-혼돈 점수 계산, 학습 데이터셋 정리, 코퍼스 통계 도구
- `examples/`
  메트릭/학습 trace 예제

설계 원칙:

- 커널은 하드웨어, 시간원, 메모리, 스케줄, 안전한 액추에이터에 집중
- OS 계층은 메인 AI, 하위 트리, 기억, 학습 파이프라인을 담당
- 메인 AI만 정적-혼돈 연산자를 사용하고, 하위 노드는 소형 역할 모델로 구성

현재 커널 연결점:

- `SYS_SLM_HW_SNAPSHOT`
  하드웨어 상태와 함께 메인 AI operator, agent tree, pipeline optimization 힌트를 함께 노출
- `runtime/slm_orchestrator.c`
  커널 텔레메트리에서 메인 AI 모드, worker 수, queue depth, token pipeline depth를 계산
- `include/runtime/ai_ring.h`
  커널-유저 공간이 함께 쓰는 shared submit/completion ring ABI 초안

추가 문서:

- `../docs/user_space_compat_architecture_ko.md`
  커널 이후 유저 공간 OS 구조와 호환성 중심 설계
- `../docs/code_boundary_and_structure_tree_ko.md`
  현재 C 커널을 유지하면서도 이후 구조 변경과 유저 공간 분리를 쉽게 하기 위한 코드 경계/구조 트리 가이드
- `runtime/README.md`
  AIOS user-space runtime 역할과 코어 서비스
- `compat/wit/aios-agent-host.wit`
  WASI component용 AIOS host interface 초안
