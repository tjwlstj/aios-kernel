# AIOS Testkit 가이드

작성일: 2026-04-10

최종 갱신: 2026-08-03 (process-owned trap evidence snapshot v0 검증 계약)

## 목적

기존 테스트 도구는 `scripts/` 아래에 커널/OS smoke 엔트리포인트가 섞여 있었고,
같은 `kernel/build/` 산출물을 병렬로 건드릴 때 Windows에서 object file lock 충돌이
나기 쉬웠다.

이번 정리는 다음을 목표로 한다.

- 테스트 도구를 전용 디렉토리로 분리
- kernel lane / os lane / 공통 헬퍼를 세분화
- 호환 래퍼였던 `scripts/`는 제거하고 `tools/testkit/`로 일원화
- 같은 `kernel/build/`를 동시에 쓰는 실행을 명시적으로 차단

## 새 디렉토리

- `tools/testkit/aios-testkit.py`
  - 메인 엔트리포인트
- `tools/testkit/lib/common.py`
  - 공통 경로, 실행 함수, host 판별, run lock
- `tools/testkit/lib/kernel_lane.py`
  - 커널 빌드/ISO/QEMU smoke
- `tools/testkit/lib/boot_matrix_lane.py`
  - `full/minimal/storage-only` 프로파일을 순차 실행하고 matrix 요약 생성
- `tools/testkit/lib/boot_inventory.py`
  - compact inventory를 baseline fixture와 비교
- `tools/testkit/lib/boot_perf.py`
  - host-local perf baseline과 threshold 기반 비교
- `tools/testkit/lib/boot_log.py`
  - serial log를 checkpoint / health / inventory / selftest 요약 JSON으로 파싱
- `tools/testkit/lib/boot_verdict.py`
  - 전체 serial log의 fatal, health, terminal checkpoint 순서·중복을 fail-closed로 판정
- `tools/testkit/lib/baseline_guard.py`
  - inventory/perf baseline 쓰기의 strict matrix·profile·verdict 출처를 검증
- `tools/testkit/lib/shell_lane.py`
  - 같은 response record의 커널 shell 교환, 전체 transcript boot verdict,
    reader drain, reboot acknowledgement와 QEMU termination을 검증
- `tools/testkit/lib/os_lane.py`
  - `os/tools` smoke와 샘플 기반 검증
- `tools/testkit/tests/`
  - QEMU 없이 실행하는 verdict/baseline/matrix/shell host unit test
- `tools/testkit/kernel/build-windows.ps1`
  - Windows 전용 커널 빌드/부팅 엔트리포인트

## 엔트리포인트

과거 `scripts/` 아래에 있던 호환 래퍼(`aios-allinone.py`, `build-windows.ps1`)는
모노레포 정리 과정에서 제거했고, 모든 엔트리포인트를 `tools/testkit/` 아래로 일원화했다.

- `tools/testkit/aios-testkit.py`
- `tools/testkit/kernel/build-windows.ps1`

## 병렬 실행 방지

공유 경로:

- `kernel/build/aios-kernel.bin`
- `kernel/build/aios-kernel.iso`
- `kernel/build/tool-smoke/*`
- `kernel/build/serial_output.log`

이 파일들은 서로 다른 lane이 동시에 건드리면 충돌 가능성이 있다.
그래서 `testkit`은 `kernel/build/.testkit-lock/` 디렉토리 락을 사용한다.

동작:

1. `info`를 제외한 모든 실행은 락을 먼저 잡는다.
2. 이미 다른 실행이 락을 잡고 있으면 즉시 실패한다.
3. 충돌 시 `owner.json`을 읽어 label/pid/host를 보여준다.
4. 이전 실행이 비정상 종료한 경우에만 락을 수동 제거한다.

이 정책은 "병렬 처리 최적화"보다 "빌드 산출물 무결성"을 우선한다.

## 권장 명령

### Testkit host unit test

```powershell
py -3 -m unittest discover -s tools/testkit/tests -t tools/testkit -p "test_*.py" -v
pwsh -NoProfile -File .\tools\testkit\tests\test_build_windows_verdict.ps1
```

이 검사는 QEMU보다 먼저 실행하며 PASS 뒤 panic, health 실패, checkpoint 역순·중복,
인용/접두사/들여쓰기 마커, 중복 key와 중복 exact observation record,
불완전 baseline/perf 출처, stale shell artifact,
clean-exit 누락, process pair 레코드 누락/불완전, pressure marker
누락/불완전·apply-capable 변형, resource marker 누락/축약/상충 변형,
trapframe 계약/유저 캡처 marker의 값 변조·확장 변형, process-owned trap
snapshot의 malformed/extended/duplicate/owner/sequence/current/stale/resume 반례와
`state user` trap/`saved_*` 증거 flip을 62개 Python unit으로 고정한다.
별도의 `test_build_windows_verdict.ps1` 27개 사례가 Windows 직접 판정기의 IDE evidence
문법과 process pair/pressure/resource/trapframe/process snapshot 필수성 및 exact
record 단일성을 같은 의미론으로 검증한다.

### 전체

```powershell
python .\tools\testkit\aios-testkit.py all --strict
python .\tools\testkit\aios-testkit.py all --strict --smoke-profile minimal
python .\tools\testkit\aios-testkit.py all --strict --smoke-profile minimal --export-boot-summary
```

### 커널만

```powershell
python .\tools\testkit\aios-testkit.py kernel --target all --strict
python .\tools\testkit\aios-testkit.py kernel --target test --strict
python .\tools\testkit\aios-testkit.py kernel --target test --strict --export-boot-summary
python .\tools\testkit\aios-testkit.py kernel --target test --strict --smoke-profile minimal
python .\tools\testkit\aios-testkit.py kernel --target test --strict --smoke-profile minimal --export-boot-summary
python .\tools\testkit\aios-testkit.py kernel --target test --strict --smoke-profile storage-only --export-boot-summary
```

### 대화형 커널 셸

```powershell
python .\tools\testkit\aios-testkit.py shell --strict
python .\tools\testkit\aios-testkit.py shell --strict --skip-build
```

현재 레인은 `state resource`와 `state pressure`를 포함한 16개 교환을 수행한다.
`state list`도 `resource` 토픽을 지원 목록에 포함해야 한다.
resource 응답은 schema 1, `observation_only=1`, aggregate 5행, owner row 0/
unattributed row 5, source별 used/limit와 validity 수를 같은 `[STATE] resource ...`
레코드에서 확인한다. pressure 응답은
schema 1, `observation_only=1`, `gate_filter_separate=1`, 계층 깊이와 세 plane,
queue/fabric/NodeBit raw 증거가 같은 `[STATE] pressure ...` 레코드에 있는지
토큰 경계로 확인한다.
shell 전체 transcript에는 normal boot verdict도 다시 적용해 exact resource/pressure
boot marker와 런타임 state 레코드를 서로 독립적으로 검증한다.

### 부팅 매트릭스

```powershell
python .\tools\testkit\aios-testkit.py boot-matrix --profiles full minimal storage-only --strict
```

### 부팅 인벤토리

```powershell
python .\tools\testkit\aios-testkit.py boot-inventory --profiles full minimal storage-only --strict
python .\tools\testkit\aios-testkit.py boot-inventory --profiles full minimal storage-only --strict --write-baseline
```

### 부팅 성능

```powershell
python .\tools\testkit\aios-testkit.py boot-perf --profiles full minimal storage-only --strict --write-baseline
python .\tools\testkit\aios-testkit.py boot-perf --profiles full minimal storage-only --strict
```

### OS 도구만

```powershell
python .\tools\testkit\aios-testkit.py os
```

### Windows 커널 전용

```powershell
pwsh -File .\tools\testkit\kernel\build-windows.ps1 -Target all
pwsh -File .\tools\testkit\kernel\build-windows.ps1 -Target test
pwsh -File .\tools\testkit\kernel\build-windows.ps1 -Target test -SmokeProfile minimal
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
- `storage-only`
  - 현재 QEMU 토폴로지는 `minimal`과 같다
  - 대신 storage bring-up과 `storage-bootstrap` seed를 추가로 요구한다
  - 즉, "저장장치만 남은 최소 경로"를 별도 프로파일로 강하게 본다

이 프로파일은 "고장난 장치 시뮬레이션"이 아니라 "optional 장치가 없는 상태"를 검증하는 용도다.
그래서 부팅 기준선과 optional 초기화 경로를 분리해 회귀를 찾기 좋다.

현재 smoke 검증은 세 프로파일을 로그 패턴으로도 구분한다.

- `공통`
  - `[DEV] Peripheral probe ready`
  - `[USER] Ring3 scaffold ready=1`
  - `[USER] bootstrap process pair PASS runs=2 order=1,2 ... between_clean=1 ... both_restored=1`
  - `[TRAP] frame contract selftest PASS size=176 canaries=15 ... frame_addr_exact=1 rflags_bit1=1 df_clear=1`
  - `[TRAP] user frame capture PASS pid_a=1 pid_b=2 ... from_user=1 cs=0x23 ss=0x1b ... frame_addr_exact=1 contract=1`
  - `[PROC] trap evidence snapshot PASS schema=1 captures=2 pid_a=1 slot_a=0 seq_a=1 ... pid_b=2 slot_b=1 seq_b=2 ... current_pid=0 stale_owner=0 resume_ready=0`
  - `[RESOURCE] ledger selftest PASS schema=1 kinds=5 units=2 entries=5 ... owners_unattributed=1 observation_only=1`
  - `[PRESSURE] tracker selftest PASS schema=1 planes=3 max_levels=4 active_levels=2 ... observation_only=1`
  - `[ROOM] snapshot stability=stable`
  - `[HEALTH] stability=stable ... degraded=0 failed=0`
- `full`
  - `[NET] E1000 ready`
  - `[USB] XHCI ready=1`
- `minimal`
  - `[NET] No Intel E1000-compatible controller found`
  - `[USB] No USB host controller found`
- `storage-only`
  - `[NET] No Intel E1000-compatible controller found`
  - `[USB] No USB host controller found`
  - `[STO] IDE ready=1`
  - `[STO] IDE channels`
  - `label=storage-bootstrap`

`[STO] IDE channels`는 marker-only 증거로 인정하지 않는다. `primary`와 `secondary`가
서로 다른 command/control 주소 쌍으로 한 번씩 나타나고, 각 채널의 `status`와 `live`가
완전한 레코드 안에 있어야 한다.

필수 문자열 존재만으로 PASS하지 않는다. 정상 verdict v1은 전체 로그에서 panic, exception,
대문자 단어 `FAIL`/`FATAL`을 금지하고, ring3 scaffold부터 shell 시작까지의 terminal checkpoint가
각각 정확히 한 번 순서대로 나타나는지 검증한다. 증거는 정해진 행 시작점과 토큰 경계에서만
인정하므로 `PASSFAIL`, `ready=10`, 인용된 과거 마커는 통과하지 않는다. contract-bearing 행의
중복 key도 거부하고, verdict line number는 raw serial artifact와 일치한다. `failed=0`,
`apply_failed=0` 같은 소문자 상태 필드는 fatal로 오인하지 않는다.
Resource와 pressure selftest, trapframe 계약의 두 `[TRAP]` marker
(`frame contract selftest`, `user frame capture`), process-owned snapshot `[PROC]`
marker는 required substring 뒤의 임의 필드를 허용하지 않는 exact record다.
snapshot은 세 profile 모두에서 user trap 뒤, Kernel Room 앞에 정확히 한 번 필요하며,
owner/sequence/current/stale/resume 의미론도 정본과 같아야 한다. canonical record에
`apply_enabled=1`이나 `extra=1`을 덧붙이거나 같은 exact record를 두 번 제시한 로그도
정상 PASS가 아니다. 선행 공백을 제거해 증거로 승격하지도 않는다.

## 부팅 요약 export

`kernel` lane과 `all` lane은 `--export-boot-summary`를 지원한다.
이 옵션은 현재 `test` 부팅 smoke에서만 유효하다.

출력 위치:

- `kernel/build/boot-summary/test-full.json`
- `kernel/build/boot-summary/test-minimal.json`
- `kernel/build/boot-summary/test-storage-only.json`

현재 JSON에는 다음 정보가 들어간다.

- checkpoint별 line/text/seen
- selftest 결과와 `memset` / `memcpy` / `memmove`
- profile 요약과 cache/latency 정보
- device summary
- health summary
- `user_mode` scaffold 상태
- `process_stack`의 primary PID 1 entry-stack proof
- `process_pair`의 PID 1→PID 2 순차 실행, 고유 CR3/backing/stack, 실행 사이·최종 cleanup proof
- `process_trap_snapshot`의 exact record 수, fullmatch 수, PID/slot owner,
  sequence, CR3/`rsp0`, distinct storage, current/stale/resume 상태
- `kernel_room` snapshot / gate 요약
- `resource`의 schema, kind/unit/entry 수, source/validity 수와 unattributed/observation-only 계약
- `pressure`의 schema, active/max level, balanced/hotspot/overlap/gate-mask
  selftest와 observation-only 계약
- network / usb / storage controller 상태
- network / USB / storage bootstrap candidate 선택 정보와 점수
- SLM MainAI 설정과 seeded plan 목록

즉, 지금 단계의 export는 "부팅 이벤트 파서 + bounded 두 process 순차 실행 +
non-resumable process-owned trap evidence snapshot"까지 구현된 상태다.
`process_pair.ready`와 `process_trap_snapshot.ready`는 PASS 접두사만으로 참이 되지 않는다.
snapshot은 `record_count`(prefix 행 수)=1, `fullmatch_count=1`이고 모든 의미 값이 exact일 때만
`ready=true`다. 이 증거는 context resume, switch 또는 preemption 구현을 뜻하지 않는다.

## boot-matrix

`boot-matrix`는 현재 `full`, `minimal`, `storage-only`를 지원하는 첫 번째 matrix orchestration이다.
이 lane은 각 프로파일마다 기존 `kernel --target test --export-boot-summary` 경로를 재사용한다.

산출물:

- `kernel/build/boot-matrix/full.json`
- `kernel/build/boot-matrix/minimal.json`
- `kernel/build/boot-matrix/storage-only.json`
- `kernel/build/boot-matrix/summary.json`

`summary.json`에는 다음이 들어간다.

- 요청한 프로파일 순서
- baseline profile
- profile별 ready / stability / device summary / controller state / SLM seeded plan count / primary process stack proof
- baseline 대비 device delta
- baseline 대비 controller state delta
- baseline 대비 seeded plan 수 차이

## boot-inventory

`boot-inventory`는 `boot-matrix` 실행 결과를 compact inventory로 다시 정리해서,
repo 안의 baseline fixture와 비교하는 lane이다.

현재 inventory 필드:

- `ready`
- `stability`
- `device_summary`
- `health_summary`
- `controller_states`
- `slm_seeded_plan_count`
- `process_stack` (정적 owner/CR3/backing/16KiB stack 고유성 + PID 1의 BSP `rsp0` 게시·`int 0x80` entry·복원 증거)

두 process 순차 실행 증거는 필수 smoke verdict, profile별 full boot summary의
`process_pair`/`process_trap_snapshot`, shell `state user`의 trap/`saved_*` mirror에
존재한다. 기존 compact inventory/baseline의
`process_stack` 스키마는 승인 없는 fixture 변경을 피하기 위해 이번 조각에서 확장하지 않는다.

출력 위치:

- `kernel/build/boot-inventory/current/<profile>.json`
- `kernel/build/boot-inventory/summary.json`
- baseline fixture: `tools/testkit/fixtures/boot-baseline/<profile>.json`

동작:

- 기본 실행
  - baseline과 비교만 수행
- `--write-baseline`
  - `--strict`가 반드시 필요하다
  - 전체 matrix가 PASS하고 요청 profile과 결과 순서가 정확히 일치해야 한다
  - canonical verdict와 profile별 controller/process/numeric proof가 정확할 때만 fixture를 갱신한다

현재 단계의 baseline은 QEMU `full/minimal/storage-only` 프로파일용 정적 fixture다.
즉, 성능 수치가 아니라 장치 수, health 요약, controller state, seeded plan 수,
정적 process/entry-stack 불변식 같은 비교적 안정적인 항목만 포함한다.

## boot-perf

`boot-perf`는 serial log에서 이미 뽑아둔 selftest/profile 정보를 다시 사용해,
같은 호스트에서의 성능 회귀를 완만한 threshold로 확인하는 lane이다.

현재 baseline 위치:

- `kernel/build/boot-perf/baseline/<profile>.json`

현재 결과 위치:

- `kernel/build/boot-perf/current/<profile>.json`
- `kernel/build/boot-perf/summary.json`

현재 비교 대상:

- `profile memcpy MiB/s`
- `memset` / `memcpy` / `memmove`의 `cyc_per_kib`
- `dram latency x100`

기본 threshold:

- throughput(`memcpy MiB/s`)
  - baseline 대비 35% 이상 하락 시 실패
- cycle/latency 계열
  - baseline 대비 45~50% 이상 상승 시 실패

중요:

- `boot-perf` baseline은 기본적으로 로컬 `kernel/build/` 아래에만 둔다
- 즉, inventory처럼 팀 공용 fixture를 기본값으로 삼지 않는다
- 이유는 QEMU와 호스트 환경 차이로 성능 수치가 장치/OS마다 크게 흔들릴 수 있기 때문이다
- 비교는 같은 profile/size/iterations/tier끼리만 허용하고 필수 metric 양쪽이 finite positive여야 한다
- `--write-baseline`은 inventory와 같은 strict source guard와 perf 필수 metric 완전성을 통과해야 한다

## 확장 규칙

앞으로 테스트가 늘어날 때는 다음 규칙을 권장한다.

1. 새 lane은 `tools/testkit/lib/`에 모듈로 추가
2. shared state를 쓰면 반드시 기존 lock 정책을 그대로 사용
3. 샘플/fixture는 lane 밖에서 재사용 가능한 위치에 두고, lane은 orchestration만 담당
4. host-specific 스크립트는 `tools/testkit/kernel/`, `tools/testkit/os/`처럼 하위 디렉토리로 분리
5. 모든 구현·엔트리포인트는 `tools/testkit/` 아래에만 둔다 (별도 `scripts/` 래퍼는 두지 않는다)

## 현재 범위

현재 `testkit`은 다음까지만 정리한다.

- kernel build / ISO / smoke
- Windows kernel helper
- OS tool smoke
- host/tool info
- shared build lock
- boot summary parser와 normal verdict v1
- full/minimal/storage-only matrix
- checked-in boot inventory와 host-local boot perf baseline
- interactive shell state lane, 같은 response record 판정, 전체 transcript verdict,
  reader drain과 clean reboot/exit termination gate
- PID 1→PID 2 순차 ring3 pair 필수 checkpoint, 구조 파서와 shell state 계약
- process-owned non-resumable trap evidence snapshot의 세 profile 공통 exact-once
  marker, structured `process_trap_snapshot`, shell `state user saved_*` mirror
- `state autonomy` schema 1의 read-only mode/support/counter/last-decision 계약
- AI Pressure Tracker required marker, structured `pressure` summary,
  `state pressure` observation-only/gate-separation 계약
- AI Resource Ledger exact required marker와 structured `resource` summary
  및 append-only `SYS_INFO_RESOURCE=0x706`, `state resource` same-record 계약
  (aggregate-only; owner attribution/quota/apply는 아직 없음)
- QEMU 없는 host unit test와 CI 선행 gate

아직 하지 않은 것:

- 일반 kernel/matrix의 timeout/guest-exit/host-kill을 분리하는 streaming collector
- profile별 raw log와 provenance를 보존하는 run bundle
- shared Python/PowerShell marker manifest
- post-link ELF structural verifier
- fault injection과 expected outcome lane
- trap snapshot을 실제 재개 context로 사용하는 switch와 timer preemption
- `-cpu max` 정규 CI profile
- trace dataset 전용 lane
- per-lane config file

즉, 현재는 정상 bootstrap의 fail-closed verdict와 기본 회귀 lane까지 구현됐고,
shell 종료 의미는 구현됐다. 일반 kernel/matrix 종료 의미·재현 artifact·fault/stress
검증은 후속 단계다.

## 추가 구상 문서

부팅 커널 테스트를 더 세분화하는 다음 구상은 아래 문서에 정리한다.

- `docs/tools/verification_tooling_evolution_design_ko.md`
  - 현재 검증 계약과 V0~V5 진화 로드맵의 정본
- `docs/tools/boot_kernel_testkit_expansion_plan_ko.md`
  - 2026-04 초기 확장 기록(OLD/REVIEW)
