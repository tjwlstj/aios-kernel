# AIOS 부팅 커널 테스트 확장안

작성일: 2026-04-11

## 현재 상태

지금 구현된 테스트 도구는 다음 범위까지 동작한다.

- 구현됨
  - `testkit/aios-testkit.py`
    - `info`, `kernel`, `os`, `all`
    - `--export-boot-summary`
  - `testkit/lib/kernel_lane.py`
    - 커널 `all` / `iso` / `test` / `clean` / `info`
    - QEMU smoke profile `full`, `minimal`, `storage-only`
    - serial log 기반 ready/selftest/probe/health 확인
    - smoke 성공 시 `build/boot-summary/test-<profile>.json` export 가능
  - `testkit/lib/boot_matrix_lane.py`
    - `full`, `minimal`, `storage-only` 프로파일을 순차 실행
    - `build/boot-matrix/<profile>.json`, `build/boot-matrix/summary.json` 생성
  - `testkit/lib/boot_inventory.py`
    - compact inventory를 `testkit/fixtures/boot-baseline/<profile>.json`과 비교
    - `build/boot-inventory/current/<profile>.json`, `build/boot-inventory/summary.json` 생성
  - `testkit/lib/boot_perf.py`
    - host-local `build/boot-perf/baseline/<profile>.json`과 threshold 비교
    - `build/boot-perf/current/<profile>.json`, `build/boot-perf/summary.json` 생성
  - `testkit/lib/boot_log.py`
    - serial log를 checkpoint / selftest / profile / inventory / health / controller / SLM 요약으로 파싱
  - `testkit/kernel/build-windows.ps1`
    - Windows에서 ISO 생성과 QEMU 부팅 smoke
  - `testkit/lib/os_lane.py`
    - OS 사용자 공간 도구 smoke

- 부분 구현
  - 부팅 검증은 여전히 serial log의 문자열 패턴에 의존한다
  - boot summary는 단일 실행 export와 `full/minimal/storage-only` matrix까지 가능하다
  - boot inventory baseline은 `full/minimal/storage-only` QEMU fixture 비교까지 가능하다
  - boot perf baseline은 로컬 `build/` 기준선과 `full/minimal/storage-only` threshold 비교까지 가능하다
  - `boot-matrix`는 아직 `debug-wait` 같은 추가 프로파일을 지원하지 않는다
  - `full` / `minimal` 프로파일은 있으나, 세부 실패 상황을 분리해 재현하지는 못한다

- 아직 없음
  - 부팅 단계별 artifact 저장
  - device inventory JSON 기준선 비교
  - boot performance baseline 관리
  - fault injection 전용 lane
  - 장치 누락/실패를 세분화한 preset

즉, 현재 testkit은 "커널이 준비 상태까지 도달하는가"를 보는 smoke는 갖췄지만,
"어느 단계에서 얼마나 달라졌는가"를 체계적으로 남기는 구조는 아직 없다.

## 핵심 갭

부팅 커널 테스트를 더 강하게 만들려면 아래 네 가지가 필요하다.

1. 단계 체크포인트 분리
   - 지금은 최종 ready 위주다.
   - 앞으로는 `boot -> acpi -> pci -> probe -> drivers -> slm -> health -> ready`를 분리해 봐야 한다.

2. 산출물 정규화
   - 현재는 `build/serial_output.log` 하나가 핵심이다.
   - 다음 단계에서는 manifest, summary, perf snapshot 같은 정형 결과물이 필요하다.

3. 실패 재현 경로
   - `minimal`은 optional 장치 부재 검증에는 좋다.
   - 하지만 "장치는 보이는데 bring-up 실패"와 "특정 체크포인트만 깨짐"은 아직 만들지 못했다.

4. 회귀 기준선
   - 장치 수, health 요약, microbench 수치가 다음 커밋에서 얼마나 달라졌는지 비교할 수 있어야 한다.

## 제안하는 추가 test lane

### 1. `boot-matrix` lane

목적:
- 여러 부팅 프로파일을 순차 실행하고 결과를 한 번에 요약

권장 프로파일:
- `full`
- `minimal`
- `storage-only`
  - `-nic none`
  - USB 생략
  - 현재 `minimal`과 유사하지만 storage 관련 기대치를 더 강하게 설정
- `debug-wait`
  - `-s -S`
  - 수동 GDB 연결 전 상태 확인용

권장 산출물:
- `build/boot-matrix/summary.json`
- `build/boot-matrix/<profile>.json`

최소 검증:
- profile별 ready 도달 여부
- health summary
- driver bootstrap 존재/부재 패턴

구현 위치:
- 새 모듈 `testkit/lib/boot_matrix_lane.py`
- 엔트리포인트 `python .\testkit\aios-testkit.py boot-matrix`

상태:
- 부분 구현
  - 현재 `full`, `minimal`, `storage-only` 지원
  - `debug-wait`는 아직 계획

### 2. `boot-checkpoint` parser

목적:
- serial log를 문자열 덩어리가 아니라 단계 이벤트로 파싱

추출 대상:
- multiboot 확인
- IDT 준비
- ACPI 준비
- PCI core 준비
- peripheral probe ready
- e1000/xhci/storage bootstrap 결과
- SLM orchestrator 준비
- health stability
- kernel ready

권장 산출물:
- `build/boot-checkpoint/boot-events.json`
- `build/boot-checkpoint/boot-summary.json`

구현 위치:
- `testkit/lib/boot_log.py`

상태:
- 계획

### 3. `boot-inventory` lane

목적:
- 장치 탐지 결과를 정형화해서 회귀 비교

추출 대상:
- PCI 총 함수 수
- matched 장치 수
- `eth` / `wifi` / `bt` / `usb` / `storage`
- health의 `ok/degraded/failed/unknown`

권장 산출물:
- `build/boot-inventory/current/<profile>.json`
- `testkit/fixtures/boot-baseline/<profile>.json`

비교 방식:
- strict
  - 카운트가 달라지면 실패
- advisory
  - 차이를 출력만 하고 통과

구현 위치:
- `testkit/lib/boot_inventory.py`

상태:
- 부분 구현
  - 현재 `full`, `minimal`, `storage-only` fixture 비교 가능
  - `strict`와 `--write-baseline` 지원
  - advisory 전용 리포트 모드는 아직 별도 분리하지 않았다

### 4. `boot-perf` lane

목적:
- 커널 부팅 직후 microbench와 profile 로그를 기준선으로 저장

대상 로그:
- `[SELFTEST] memset=...`
- `[SELFTEST] memcpy=...`
- `[SELFTEST] memmove=...`
- `[PROFILE] TSC=...`
- `[PROFILE] Cache KiB ...`

권장 산출물:
- `build/boot-perf/current/<profile>.json`
- `build/boot-perf/baseline/<profile>.json`
- `build/boot-perf/summary.json`

검증 방식:
- 절대 수치 고정이 아니라 허용 편차 기반
- 예: 이전 기준선 대비 `memcpy MiB/s`가 30% 이상 하락하면 경고

구현 위치:
- `testkit/lib/boot_perf.py`

상태:
- 부분 구현
  - 현재 `full`, `minimal`, `storage-only` 프로파일 지원
  - baseline은 로컬 `build/boot-perf/baseline/`에 저장
  - 공용 fixture 비교는 아직 하지 않는다

### 5. `boot-fault` lane

목적:
- "장치 부재"가 아니라 "초기화 실패"를 재현

현실적인 첫 단계:
- QEMU 옵션만으로 가능한 fault부터 시작
- 예:
  - NIC 제거
  - USB 제거
  - 메모리 축소
  - `-no-reboot` + 짧은 timeout

다음 단계:
- 커널에 test-only fault flag를 넣고 특정 bootstrap 지점에서 fail path를 강제

주의:
- 이 lane은 커널 코드에 test hook이 필요할 수 있다
- production path와 분리된 compile-time flag가 좋다

구현 위치:
- `testkit/lib/boot_fault_lane.py`
- 추후 `include/aios/test_fault.h` 같은 test-only header 가능

상태:
- 계획

## 가장 작은 다음 패치

지금 코드베이스에 바로 올리기 좋은 최소 슬라이스는 아래다.

1. 완료: `boot_log.py` 추가
   - 현재 serial log에서 checkpoint와 summary를 JSON으로 뽑는다

2. 완료: `kernel` lane에 `--export-boot-summary` 추가
   - smoke 성공 시 `build/boot-summary/test-<profile>.json` 생성

3. 완료: `boot-matrix` 엔트리 추가
   - 현재 `full` + `minimal` + `storage-only` 프로파일 순차 실행 가능

지금 testkit은 단순 smoke를 넘어서 "단일 부팅 기록 + 기본 matrix"까지는 올라왔다.
다음부터는 이 기록을 baseline 비교와 threshold 비교로 연결하면 된다.

## 권장 명령 형태

아래 명령은 현재 구현되어 있다.

```powershell
python .\testkit\aios-testkit.py boot-matrix --profiles full minimal
python .\testkit\aios-testkit.py kernel --target test --strict --smoke-profile full --export-boot-summary
python .\testkit\aios-testkit.py kernel --target test --strict --smoke-profile minimal --export-boot-summary
python .\testkit\aios-testkit.py kernel --target test --strict --smoke-profile storage-only --export-boot-summary
```

## 검증 경로

각 lane은 QEMU를 다시 발명하지 말고 기존 흐름을 재사용해야 한다.

- 부팅 실행
  - `testkit/lib/kernel_lane.py`
- Windows 실행
  - `testkit/kernel/build-windows.ps1`
- 공통 결과물 위치
  - `build/`
- 공통 실행 락
  - `testkit/lib/common.py`

즉, 새 lane은 가능하면 "새 부팅기"가 아니라 "기존 부팅기 위의 parser / orchestrator"여야 한다.

## 리스크

- QEMU 로그 형식이 바뀌면 parser가 깨질 수 있다
- 절대 성능 기준선을 너무 빡빡하게 잡으면 가상화 환경 편차에 취약하다
- fault injection을 커널에 섣불리 넣으면 production 경로를 오염시킬 수 있다

## 우선순위

1. `boot-fault` lane
2. perf 정책 세분화
3. `debug-wait` 같은 추가 profile

## 정리

현재 testkit은 부팅 smoke의 뼈대는 이미 있다.
다음에 필요한 것은 더 많은 QEMU 옵션보다,
"부팅 결과를 정형화하고 비교 가능하게 만드는 도구"다.

그래서 추천 순서는 다음과 같다.

1. perf 정책 세분화
2. `debug-wait` 같은 추가 profile
3. 그 다음에만 fault injection으로 넘어가기
