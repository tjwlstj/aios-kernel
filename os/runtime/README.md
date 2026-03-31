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
- 메인 AI와 worker는 같은 ring ABI를 쓰되, queue depth와 zero-copy window는 kernel snapshot 힌트를 따른다

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

## 커널 연결점

- `SYS_SLM_HW_SNAPSHOT`
  hardware, health, main AI mode, pipeline hints, agent tree 읽기
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
