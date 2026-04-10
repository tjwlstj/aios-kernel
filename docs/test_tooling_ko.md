# AIOS 테스트 툴링 구조와 올인원 도구

작성일: 2026-03-29

추가 갱신: 2026-04-10

## 현재 테스트 구조

AIOS 저장소의 테스트 구조는 크게 두 갈래다.

- 커널 빌드/부팅 테스트
  - Linux: `Makefile`
  - Windows: `scripts/build-windows.ps1`
- OS 레이어 도구 테스트
  - `os/tools/*.py`
  - 샘플 입력 기반 smoke test

이 구조는 기능적으로는 충분했지만, 엔트리포인트가 분산되어 있었다.

## 문제

- 커널 테스트와 OS 도구 테스트를 한 번에 돌리는 진입점이 없다
- Windows와 Linux가 서로 다른 스크립트를 사용한다
- macOS 같은 비주류 개발 호스트에서 무엇이 가능하고 무엇이 skip인지 기준이 없다
- CI에서 어떤 조합을 공통 기준으로 볼지 명확하지 않다

## 현재 권장 구조

전용 디렉토리:

- `testkit/`
  - `aios-testkit.py`
  - `lib/common.py`
  - `lib/kernel_lane.py`
  - `lib/os_lane.py`
  - `kernel/build-windows.ps1`

호환 래퍼:

- `scripts/aios-allinone.py`
- `scripts/build-windows.ps1`

지원 명령:

- `python testkit/aios-testkit.py info`
- `python testkit/aios-testkit.py os`
- `python testkit/aios-testkit.py kernel --target all`
- `python testkit/aios-testkit.py kernel --target test --strict`
- `python testkit/aios-testkit.py all --strict`
- `pwsh -File .\testkit\kernel\build-windows.ps1 -Target test`

## 동작 원리

### Kernel lane

- Windows
  - `testkit/kernel/build-windows.ps1`를 호출
- Linux / macOS
  - `make all`, `make iso`를 호출
  - `test`는 Python이 직접 QEMU timeout/log 검증 수행

### OS lane

아래 도구를 샘플 입력으로 자동 검증한다.

- `os/tools/score_static_chaos.py`
- `os/tools/build_learning_dataset.py`
- `os/tools/summarize_learning_corpus.py`
- `os/compat/wit/aios-agent-host.wit`

출력:

- `build/tool-smoke/summary.json`
- `build/tool-smoke/static-chaos-score.json`
- `build/tool-smoke/memory_journal.jsonl`
- `build/tool-smoke/adapter_candidates.jsonl`

## 호스트 호환성 정책

### Windows

- kernel build/test: PowerShell 경유 정식 지원
- os tool smoke: 지원

### Linux

- kernel build/test: 정식 지원
- os tool smoke: 지원
- GitHub Actions 기본 기준 호스트

### macOS

- os tool smoke: 지원
- kernel build/test: 툴체인, GRUB, QEMU가 설치된 경우 동작
- 호스트에 커널 의존 도구가 없으면 `strict`가 아닐 때 명시적 skip를 허용

## CI 권장 구조

1. Linux에서 kernel + os 통합 smoke
2. Windows에서 os tool smoke
3. 필요 시 Windows local kernel smoke는 수동 또는 self-hosted

## 병렬 실행 정책

현재 `testkit`은 `build/.testkit-lock/` 디렉토리 락으로 동시 실행을 막는다.

이유:

- kernel build/test와 os smoke가 모두 `build/`를 공유
- Windows에서는 같은 object/ISO를 동시에 만질 때 lock 충돌이 나기 쉬움
- 지금 단계에서는 병렬 처리보다 산출물 무결성과 재현성이 더 중요함

자세한 구조와 확장 규칙은 [docs/testkit_guide_ko.md](docs/testkit_guide_ko.md)를 참고한다.

## 이유

이 구조는 현재 AIOS 계획과 잘 맞는다.

- 커널은 부팅/메모리/드라이버/SLM을 검증
- OS 레이어는 메인 AI / 학습 / WIT 호환성 / 데이터셋 도구를 검증
- 한 엔트리포인트로 둘을 함께 돌릴 수 있어 개발 속도와 회귀 감지가 좋아진다
