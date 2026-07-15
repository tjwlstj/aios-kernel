# AIOS Testkit

이 디렉토리는 AIOS의 테스트 도구를 기능별로 분리해 담는 전용 공간이다.
저장소 루트 기준 경로는 `tools/testkit/`이며, 빌드 산출물은 `kernel/` 도메인의 `kernel/build/`에 모인다.

구조:

- `aios-testkit.py`
  - 공통 엔트리포인트
- `lib/common.py`
  - 호스트 탐지, 공통 실행 함수, build lock
- `lib/kernel_lane.py`
  - 커널 빌드/ISO/QEMU smoke
- `lib/boot_matrix_lane.py`
  - `full/minimal/storage-only` 부팅 smoke를 순차 실행하고 matrix 요약을 생성
- `lib/boot_inventory.py`
  - compact inventory를 baseline과 비교하고 baseline fixture를 갱신
- `lib/boot_perf.py`
  - host-local perf baseline을 생성하고 threshold 기반 회귀를 비교
- `lib/boot_log.py`
  - serial log를 checkpoint / health / inventory / microbench 요약 JSON으로 파싱
- `lib/shell_lane.py`
  - QEMU `-serial stdio`로 부팅 후 커널 셸의 기계 판독형 `state` 명령을 구동/검증
- `lib/os_lane.py`
  - OS 계층 도구 smoke
- `kernel/build-windows.ps1`
  - Windows 커널 빌드/부팅용 전용 엔트리포인트

원칙:

- 실제 구현은 `tools/testkit/` 아래에서만 확장한다 (별도 호환 래퍼는 두지 않는다)
- 빌드는 루트 `Makefile`이 `kernel/`로 위임하므로, testkit은 항상 저장소 루트에서 실행한다
- `kernel/build/.testkit-lock/`으로 동시 실행을 차단
- `all`은 항상 `kernel -> os` 순서로 순차 실행

스모크 프로파일:

- `full`
  - 기본값
  - QEMU에 `e1000` NIC와 `qemu-xhci` USB 컨트롤러를 추가해 optional 장치 초기화까지 본다
- `minimal`
  - optional NIC/USB 없이 커널이 기본 부팅 경로를 완료하는지 본다
  - 하드웨어가 비어 있는 환경에서도 부팅 기준선을 유지하는지 확인할 때 쓴다
  - 로그에서는 `No Intel E1000-compatible controller found`, `No USB host controller found`를 기대한다
- `storage-only`
  - 현재 QEMU 토폴로지는 `minimal`과 같다
  - 대신 storage bring-up과 `storage-bootstrap` SLM seed를 추가로 요구한다
  - 즉, "저장장치만 남은 최소 부팅 경로"를 별도 프로파일로 강하게 본다

부팅 요약 내보내기:

- `--export-boot-summary`
  - smoke 성공 후 `kernel/build/boot-summary/test-<profile>.json` 생성
  - checkpoint, selftest, perf profile, device summary, health, user-mode scaffold,
    Kernel Room snapshot, controller state, network/USB/storage bootstrap selection,
    SLM seed 결과를 저장

공통 smoke marker:

- `[DEV] Peripheral probe ready`
- `[USER] Ring3 scaffold ready=1`
- `[ROOM] snapshot stability=...`
- `[HEALTH] stability=...`

부팅 매트릭스:

- `boot-matrix`
  - 현재는 `full`, `minimal`, `storage-only` 프로파일을 지원
  - 각 프로파일의 full summary를 `kernel/build/boot-matrix/<profile>.json`에 저장
  - aggregate summary를 `kernel/build/boot-matrix/summary.json`에 저장

부팅 인벤토리:

- `boot-inventory`
  - `kernel/build/boot-matrix/summary.json`을 재사용해 compact inventory를 비교
  - 현재 inventory는 `ready`, `stability`, `device_summary`, `health_summary`, `controller_states`, `slm_seeded_plan_count`, `process_stack`
  - current 결과는 `kernel/build/boot-inventory/current/<profile>.json`
  - baseline fixture는 `tools/testkit/fixtures/boot-baseline/<profile>.json`

대화형 셸 레인:

- `shell`
  - QEMU 시리얼을 stdio에 붙여 "실행 중 커널"과 명령/응답으로 대화하는 레인
  - `[SHELL] Interactive shell started` 대기 → `ping`, `state list/health/mem/sched/nodes/pipeline/slm/user/sec/time/version`,
    미지 토픽 오류 응답까지 순차 검증 → `reboot`로 클린 종료 (`-no-reboot` 덕에 QEMU exit)
  - 응답 프로토콜: 한 줄 `[STATE] <topic> key=value ...` (값에 공백 없음).
    리스트형 토픽(`state nodes`)은 요약 한 줄 + 항목당 `[STATE] node id=...` 한 줄
  - 아티팩트: `kernel/build/shell-smoke/transcript.log` (전체 시리얼 대화),
    `kernel/build/shell-smoke/summary.json` (교환별 pass/fail)
  - `--skip-build`로 기존 ISO 재사용 가능
  - 새 `state` 토픽을 추가하면 `lib/shell_lane.py`의 `DEFAULT_EXCHANGES`에 교환을 등록한다

부팅 성능:

- `boot-perf`
  - `kernel/build/boot-summary/test-<profile>.json`을 재사용해 perf record를 비교
  - current 결과는 `kernel/build/boot-perf/current/<profile>.json`
  - baseline은 로컬 전용 `kernel/build/boot-perf/baseline/<profile>.json`
  - 기본 비교는 `memcpy MiB/s`, `memset/memcpy/memmove cyc_per_kib`, `dram latency x100`

권장 사용 (저장소 루트에서 실행):

```powershell
python .\tools\testkit\aios-testkit.py info
python .\tools\testkit\aios-testkit.py kernel --target test --strict
python .\tools\testkit\aios-testkit.py kernel --target test --strict --export-boot-summary
python .\tools\testkit\aios-testkit.py kernel --target test --strict --smoke-profile minimal
python .\tools\testkit\aios-testkit.py kernel --target test --strict --smoke-profile minimal --export-boot-summary
python .\tools\testkit\aios-testkit.py kernel --target test --strict --smoke-profile storage-only --export-boot-summary
python .\tools\testkit\aios-testkit.py boot-matrix --profiles full minimal storage-only --strict
python .\tools\testkit\aios-testkit.py boot-inventory --profiles full minimal storage-only --strict
python .\tools\testkit\aios-testkit.py boot-inventory --profiles full minimal storage-only --strict --write-baseline
python .\tools\testkit\aios-testkit.py boot-perf --profiles full minimal storage-only --strict --write-baseline
python .\tools\testkit\aios-testkit.py boot-perf --profiles full minimal storage-only --strict
python .\tools\testkit\aios-testkit.py shell --strict
python .\tools\testkit\aios-testkit.py shell --strict --skip-build
python .\tools\testkit\aios-testkit.py os
python .\tools\testkit\aios-testkit.py all --strict
python .\tools\testkit\aios-testkit.py all --strict --smoke-profile minimal --export-boot-summary
pwsh -File .\tools\testkit\kernel\build-windows.ps1 -Target test
pwsh -File .\tools\testkit\kernel\build-windows.ps1 -Target test -SmokeProfile minimal
```

추가 구상:

- 부팅 커널 테스트 확장안은 `docs/tools/boot_kernel_testkit_expansion_plan_ko.md`에 정리한다
