# AIOS Testkit

이 디렉토리는 AIOS의 테스트 도구를 기능별로 분리해 담는 전용 공간이다.

구조:

- `aios-testkit.py`
  - 공통 엔트리포인트
- `lib/common.py`
  - 호스트 탐지, 공통 실행 함수, build lock
- `lib/kernel_lane.py`
  - 커널 빌드/ISO/QEMU smoke
- `lib/os_lane.py`
  - OS 계층 도구 smoke
- `kernel/build-windows.ps1`
  - Windows 커널 빌드/부팅용 전용 엔트리포인트

원칙:

- `scripts/` 아래 파일은 호환 래퍼로 유지
- 실제 구현은 `testkit/` 아래에서만 확장
- `build/.testkit-lock/`으로 동시 실행을 차단
- `all`은 항상 `kernel -> os` 순서로 순차 실행

스모크 프로파일:

- `full`
  - 기본값
  - QEMU에 `e1000` NIC와 `qemu-xhci` USB 컨트롤러를 추가해 optional 장치 초기화까지 본다
- `minimal`
  - optional NIC/USB 없이 커널이 기본 부팅 경로를 완료하는지 본다
  - 하드웨어가 비어 있는 환경에서도 부팅 기준선을 유지하는지 확인할 때 쓴다
  - 로그에서는 `No Intel E1000-compatible controller found`, `No USB host controller found`를 기대한다

권장 사용:

```powershell
python .\testkit\aios-testkit.py info
python .\testkit\aios-testkit.py kernel --target test --strict
python .\testkit\aios-testkit.py kernel --target test --strict --smoke-profile minimal
python .\testkit\aios-testkit.py os
python .\testkit\aios-testkit.py all --strict
pwsh -File .\testkit\kernel\build-windows.ps1 -Target test
pwsh -File .\testkit\kernel\build-windows.ps1 -Target test -SmokeProfile minimal
```
