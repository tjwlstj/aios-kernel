# AIOS Testkit 가이드

작성일: 2026-04-10

## 목적

기존 테스트 도구는 `scripts/` 아래에 커널/OS smoke 엔트리포인트가 섞여 있었고,
같은 `build/` 산출물을 병렬로 건드릴 때 Windows에서 object file lock 충돌이
나기 쉬웠다.

이번 정리는 다음을 목표로 한다.

- 테스트 도구를 전용 디렉토리로 분리
- kernel lane / os lane / 공통 헬퍼를 세분화
- 기존 경로는 호환 래퍼로 유지
- 같은 `build/`를 동시에 쓰는 실행을 명시적으로 차단

## 새 디렉토리

- `testkit/aios-testkit.py`
  - 메인 엔트리포인트
- `testkit/lib/common.py`
  - 공통 경로, 실행 함수, host 판별, run lock
- `testkit/lib/kernel_lane.py`
  - 커널 빌드/ISO/QEMU smoke
- `testkit/lib/boot_matrix_lane.py`
  - `full/minimal` 프로파일을 순차 실행하고 matrix 요약 생성
- `testkit/lib/boot_log.py`
  - serial log를 checkpoint / health / inventory / selftest 요약 JSON으로 파싱
- `testkit/lib/os_lane.py`
  - `os/tools` smoke와 샘플 기반 검증
- `testkit/kernel/build-windows.ps1`
  - Windows 전용 커널 빌드/부팅 엔트리포인트

## 호환 엔트리

기존 엔트리는 바로 제거하지 않았다.

- `scripts/aios-allinone.py`
- `scripts/build-windows.ps1`

이 파일들은 앞으로는 `testkit/` 아래 구현을 호출하는 호환 래퍼다.
즉, 문서와 자동화는 새 경로를 기준으로 옮기되, 기존 사용자 습관은 당장 깨지지 않게 했다.

## 병렬 실행 방지

공유 경로:

- `build/aios-kernel.bin`
- `build/aios-kernel.iso`
- `build/tool-smoke/*`
- `build/serial_output.log`

이 파일들은 서로 다른 lane이 동시에 건드리면 충돌 가능성이 있다.
그래서 `testkit`은 `build/.testkit-lock/` 디렉토리 락을 사용한다.

동작:

1. `info`를 제외한 모든 실행은 락을 먼저 잡는다.
2. 이미 다른 실행이 락을 잡고 있으면 즉시 실패한다.
3. 충돌 시 `owner.json`을 읽어 label/pid/host를 보여준다.
4. 이전 실행이 비정상 종료한 경우에만 락을 수동 제거한다.

이 정책은 "병렬 처리 최적화"보다 "빌드 산출물 무결성"을 우선한다.

## 권장 명령

### 전체

```powershell
python .\testkit\aios-testkit.py all --strict
python .\testkit\aios-testkit.py all --strict --smoke-profile minimal
python .\testkit\aios-testkit.py all --strict --smoke-profile minimal --export-boot-summary
```

### 커널만

```powershell
python .\testkit\aios-testkit.py kernel --target all --strict
python .\testkit\aios-testkit.py kernel --target test --strict
python .\testkit\aios-testkit.py kernel --target test --strict --export-boot-summary
python .\testkit\aios-testkit.py kernel --target test --strict --smoke-profile minimal
python .\testkit\aios-testkit.py kernel --target test --strict --smoke-profile minimal --export-boot-summary
```

### 부팅 매트릭스

```powershell
python .\testkit\aios-testkit.py boot-matrix --profiles full minimal --strict
```

### OS 도구만

```powershell
python .\testkit\aios-testkit.py os
```

### Windows 커널 전용

```powershell
pwsh -File .\testkit\kernel\build-windows.ps1 -Target all
pwsh -File .\testkit\kernel\build-windows.ps1 -Target test
pwsh -File .\testkit\kernel\build-windows.ps1 -Target test -SmokeProfile minimal
```

## 스모크 프로파일

`kernel` lane이 QEMU를 직접 띄우는 경우에는 `--smoke-profile` 또는 `-SmokeProfile`으로
optional 하드웨어 구성을 나눌 수 있다.

- `full`
  - 기본값
  - `e1000` NIC와 `qemu-xhci` 컨트롤러를 추가한 일반 부팅 smoke
- `minimal`
  - `-nic none`으로 네트워크를 비우고 USB 컨트롤러도 추가하지 않는다
  - 즉, "부트 가능한 최소 하드웨어"에서 커널이 준비 상태까지 가는지를 본다

이 프로파일은 "고장난 장치 시뮬레이션"이 아니라 "optional 장치가 없는 상태"를 검증하는 용도다.
그래서 부팅 기준선과 optional 초기화 경로를 분리해 회귀를 찾기 좋다.

현재 smoke 검증은 두 프로파일을 로그 패턴으로도 구분한다.

- `full`
  - `[NET] E1000 ready`
  - `[USB] XHCI ready=1`
- `minimal`
  - `[NET] No Intel E1000-compatible controller found`
  - `[USB] No USB host controller found`

## 부팅 요약 export

`kernel` lane과 `all` lane은 `--export-boot-summary`를 지원한다.
이 옵션은 현재 `test` 부팅 smoke에서만 유효하다.

출력 위치:

- `build/boot-summary/test-full.json`
- `build/boot-summary/test-minimal.json`

현재 JSON에는 다음 정보가 들어간다.

- checkpoint별 line/text/seen
- selftest 결과와 `memset` / `memcpy` / `memmove`
- profile 요약과 cache/latency 정보
- device summary
- health summary
- network / usb / storage controller 상태
- SLM MainAI 설정과 seeded plan 목록

즉, 지금 단계의 export는 "부팅 이벤트 파서 + 단일 실행 기록"까지 구현된 상태다.

## boot-matrix

`boot-matrix`는 현재 `full`과 `minimal`만 지원하는 첫 번째 matrix orchestration이다.
이 lane은 각 프로파일마다 기존 `kernel --target test --export-boot-summary` 경로를 재사용한다.

산출물:

- `build/boot-matrix/full.json`
- `build/boot-matrix/minimal.json`
- `build/boot-matrix/summary.json`

`summary.json`에는 다음이 들어간다.

- 요청한 프로파일 순서
- baseline profile
- profile별 ready / stability / device summary / controller state / SLM seeded plan count
- baseline 대비 device delta
- baseline 대비 controller state delta
- baseline 대비 seeded plan 수 차이

## 확장 규칙

앞으로 테스트가 늘어날 때는 다음 규칙을 권장한다.

1. 새 lane은 `testkit/lib/`에 모듈로 추가
2. shared state를 쓰면 반드시 기존 lock 정책을 그대로 사용
3. 샘플/fixture는 lane 밖에서 재사용 가능한 위치에 두고, lane은 orchestration만 담당
4. host-specific 스크립트는 `testkit/kernel/`, `testkit/os/`처럼 하위 디렉토리로 분리
5. `scripts/`는 새 구현을 넣지 말고 wrapper만 유지

## 현재 범위

현재 `testkit`은 다음까지만 정리한다.

- kernel build / ISO / smoke
- Windows kernel helper
- OS tool smoke
- host/tool info
- shared build lock

아직 하지 않은 것:

- CI matrix용 세부 job preset
- artifact archive/export 도구
- trace dataset 전용 lane
- per-lane config file

즉, 이번 단계는 "확장 가능한 뼈대 + 병렬 충돌 방지"까지를 목표로 한다.

## 추가 구상 문서

부팅 커널 테스트를 더 세분화하는 다음 구상은 아래 문서에 정리한다.

- `docs/boot_kernel_testkit_expansion_plan_ko.md`
  - boot-matrix
  - boot-checkpoint parser
  - boot-inventory baseline
  - boot-perf snapshot
  - boot-fault lane
