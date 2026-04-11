# Gemini CLI 1차 검토 기록

## 목적

이 문서는 `Gemini CLI`가 현재 `AIOS` 저장소에서 실제로 동작하는지 검증하고, 첫 번째 저장소 리뷰 결과를 기록하기 위한 메모이다.

관련 운영 방향 문서는 [gemini_cli_usage_strategy_ko.md](./gemini_cli_usage_strategy_ko.md)를 참고한다.

## 실행 검증 결과

2026-03-30 기준, 이 머신에서 전역 설치된 `Gemini CLI`는 다음 상태를 확인했다.

- 실행 파일: `C:\Users\tjwls\AppData\Roaming\npm\gemini.cmd`
- Node 런타임: `C:\Program Files\nodejs\node.exe`
- `gemini --version`: 성공 (`0.35.3`)
- `gemini --help`: 성공
- `gemini --list-sessions`: 성공
- 프로젝트 루트 세션 캐시 로드: 성공

실제 모델 호출도 다시 확인했다.

- `gemini -p ... -o text`: 성공
- 간단한 응답 확인: `GEMINI_OK`

즉, 현재는 `Gemini CLI`를 이 저장소에서 실제 검토 도구로 사용할 수 있는 상태다.

## 1차 검토 방식

읽기 전용 검토자로 사용하기 위해 비대화식 프롬프트로 다음 파일들을 우선 읽게 했다.

- `README.md`
- `docs/current_kernel_gap_report_ko.md`
- `docs/user_space_compat_architecture_ko.md`
- `kernel/main.c`
- `runtime/ai_syscall.c`
- `runtime/slm_orchestrator.c`
- `mm/tensor_mm.c`
- `sched/ai_sched.c`
- `drivers/pci_core.c`

요청한 검토 범위는 다음이었다.

- 가장 큰 리스크
- 다음 우선순위
- 나중으로 미뤄도 되는 것
- `kernel / os / runtime / tooling` 관점 메모

## Gemini 1차 리뷰 핵심 요약

Gemini의 실제 응답을 압축하면 다음과 같다.

### 1. 현재 프로젝트에 대한 판단

Gemini는 AIOS를 다음 단계까지는 올라온 상태로 봤다.

- 64-bit Long Mode 부팅과 초기화 기반은 안정적
- ACPI / PCI / 기본 드라이버 초기화 축은 잡혀 있음
- `Tensor MM`, `AI Scheduler`, `SLM/Autonomy` 같은 AI 특화 하위 시스템의 방향성이 분명함

즉, 현재 저장소는 "아이디어 문서만 있는 상태"는 아니고, 실제로 부팅 가능한 프로토타입 커널로 인식했다.

### 2. 강점으로 본 부분

Gemini가 강점으로 본 축은 다음과 같다.

- 커널 코어 / HAL / 자율 제어 평면 사이의 분리 방향이 비교적 명확함
- `Tensor Memory Manager`와 `AI Scheduler` 같은 AI 우선 primitive가 이미 커널 단에서 존재함
- `runtime/autonomy.c`를 중심으로 자율 제어 상태 기계의 뼈대가 있음

즉, 이 프로젝트의 강점은 "범용 OS를 억지로 AI용으로 바꾸는 것"이 아니라, 처음부터 AI 네이티브 축으로 자원을 설계하고 있다는 점으로 요약된다.

### 3. 가장 큰 부족점

Gemini는 다음 5개를 가장 핵심적인 공백으로 봤다.

1. `ring3 / TSS / syscall entry / ELF loader` 부재
2. 하드웨어 타이머와 연결된 진짜 선점 스케줄링 부재
3. `model runtime`의 실제 실행 경로 부재
4. `kernel/user` 격리를 포함한 VMM 부재
5. 드라이버의 실 I/O 데이터 경로 부재

이는 이미 내부 점검 문서 [current_kernel_gap_report_ko.md](./current_kernel_gap_report_ko.md) 에서 정리한 우선순위와도 거의 일치한다.

### 4. 다음 우선순위에 대한 시사점

Gemini의 응답을 기준으로 하면, 다음 실질 우선순위는 아래 순서가 타당하다.

1. `user-space init`을 띄울 수 있는 최소 실행 기반 만들기
2. 타이머 IRQ를 연결해 스케줄러를 자료구조에서 실행기로 올리기
3. AI syscall을 skeleton에서 실제 runtime 경로로 연결하기
4. 메모리 모델을 identity map 중심에서 `kernel/user` 분리형으로 전환하기
5. e1000 / storage / USB를 smoke에서 실제 data path로 끌어올리기

이 우선순위는 현재 AIOS가 "커널 데모"에서 "OS 초기형"으로 넘어가기 위해 필요한 가장 짧은 경로로 해석할 수 있다.

## 이번 검토로 얻은 결론

Gemini의 1차 평가는 크게 두 가지 점에서 유용했다.

### 내부 판단과 외부 판단이 정렬됨

이미 저장소 내부 문서에서 강조하던 핵심 부족점과 Gemini의 외부 검토 포인트가 거의 겹쳤다.  
즉 현재 로드맵 방향이 크게 어긋나 있지는 않다고 볼 수 있다.

### Gemini의 적합한 역할이 분명해짐

이번 실행 결과만 보면 Gemini는 다음 역할에 특히 적합하다.

- 현재 설계의 리스크 정리
- README와 실제 구현의 간극 점검
- 다음 단계 우선순위 재정렬
- 문서 감리

반대로 현재 단계에서는, 직접 구현자보다 "외부 리뷰어" 역할로 붙이는 편이 더 효율적이다.

## 권장 후속 활용

다음과 같은 흐름으로 Gemini CLI를 붙이는 것이 좋다.

1. 큰 기능 구현 전에 설계 검토 프롬프트 실행
2. 구현 후 README/문서/테스트 누락 점검
3. 큰 리팩터링 전후로 우선순위 재평가
4. 커널 / OS / runtime / tooling 을 나눠 별도 검토

## 2차 추가 점검 메모

2026-04-12에는 같은 흐름으로 범위를 더 좁혀 다시 검토했다.

- 드라이버 / boot bring-up
- `boot -> userspace` 전이

이번 추가 기록은 별도 문서
[gemini_driver_userspace_checkpoint_ko.md](./gemini_driver_userspace_checkpoint_ko.md)
에 정리했다.

핵심 결론만 요약하면 다음과 같다.

- 드라이버는 `e1000` 기준으로 smoke 가능한 bootstrap까지는 올라와 있음
- `storage` / `usb`는 host probe 중심으로 아직 얕음
- `ai_syscall` / `autonomy` / `SLM`은 커널 내부 control plane으로는 유효하지만
  실제 userspace ABI로 넘어가려면 `ring3 + TSS + init handoff`가 먼저 필요함
- 따라서 다음 병행 축은 `boot-fault`와 `ring3 scaffold`가 됨

## 한 줄 결론

이번 1차 검토 기준으로, `Gemini CLI`는 현재 `AIOS`에서 충분히 활용 가능하며, 특히 "설계 리뷰와 문서 감리 중심의 보조 검토자"로 붙이는 것이 가장 효과적이다.
