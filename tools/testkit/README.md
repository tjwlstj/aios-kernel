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
- `lib/boot_verdict.py`
  - 전체 serial log의 fatal, health, terminal checkpoint 순서·중복을 fail-closed로 판정
- `lib/baseline_guard.py`
  - inventory/perf baseline 쓰기의 strict matrix·profile·record 출처를 검증
- `lib/shell_lane.py`
  - QEMU `-serial stdio`로 커널 셸을 구동하고 reboot acknowledgement와 clean exit까지 검증
- `lib/os_lane.py`
  - OS 계층 도구 smoke
- `kernel/build-windows.ps1`
  - Windows 커널 빌드/부팅용 전용 엔트리포인트
- `tests/`
  - QEMU 없이 verdict, baseline guard, matrix, shell 반례를 검증하는 Python host unit test 55개
  - `test_build_windows_verdict.ps1`은 직접 PowerShell IDE/process-pair/pressure/resource 판정 15개를 검증

원칙:

- 실제 구현은 `tools/testkit/` 아래에서만 확장한다 (별도 호환 래퍼는 두지 않는다)
- 빌드는 루트 `Makefile`이 `kernel/`로 위임하므로, testkit은 항상 저장소 루트에서 실행한다
- `kernel/build/.testkit-lock/`으로 동시 실행을 차단
- `all`은 항상 `kernel -> os` 순서로 순차 실행
- 정상 부팅은 전체 로그의 fatal/health/terminal chain을 fail-closed로 판정
- baseline 쓰기는 `--strict`와 완전한 trusted matrix 결과를 모두 요구

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
  - `IDE channels`는 marker-only가 아니라 서로 다른 primary/secondary 주소와 각 채널의 `status/live`가 완전한 레코드여야 한다
  - 즉, "저장장치만 남은 최소 부팅 경로"를 별도 프로파일로 강하게 본다

부팅 요약 내보내기:

- `--export-boot-summary`
  - smoke 성공 후 `kernel/build/boot-summary/test-<profile>.json` 생성
  - checkpoint, selftest, perf profile, device summary, health, user-mode scaffold,
    primary process stack, 두 process 순차 실행 `process_pair`, Kernel Room snapshot,
    controller state, network/USB/storage bootstrap selection, SLM seed 결과,
    AI Resource Ledger와 AI Pressure Tracker schema/selftest를 저장

공통 smoke marker:

- `[DEV] Peripheral probe ready`
- `[USER] Ring3 scaffold ready=1`
- `[USER] bootstrap process pair PASS runs=2 order=1,2 ... between_clean=1 ... both_restored=1`
- `[RESOURCE] ledger selftest PASS schema=1 kinds=5 units=2 entries=5 ... owners_unattributed=1 observation_only=1`
- `[PRESSURE] tracker selftest PASS schema=1 planes=3 max_levels=4 active_levels=2 ... observation_only=1`
- `[ROOM] snapshot stability=...`
- `[HEALTH] stability=...`

필수 문자열 존재만으로 PASS하지 않는다. normal verdict v1은 전체 로그의 panic,
exception, 대문자 단어 `FAIL`/`FATAL`을 금지하고, health가
`stability=stable degraded=0 failed=0`인지 확인한다. ring3 scaffold부터 shell 시작까지의
terminal checkpoint는 각각 정확히 한 번, 정의된 순서로 나타나야 한다. 증거 행과 토큰
경계를 고정하고 contract-bearing 행의 중복 key를 거부하므로 인용·접두사 위장도 PASS하지 않는다.
Resource와 pressure의 observation-only selftest는 행 전체 exact record라서 정본 뒤에
`apply_enabled=1` 같은 상충 필드를 붙여도 PASS하지 않는다.

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
  - `--write-baseline`은 `--strict`, matrix 전체 PASS, 정확한 profile 순서,
    canonical verdict와 profile별 controller/process/numeric proof를 모두 요구

대화형 셸 레인:

- `shell`
  - QEMU 시리얼을 stdio에 붙여 "실행 중 커널"과 명령/응답으로 대화하는 레인
  - `[SHELL] Interactive shell started` 대기 → `ping`, `state list/health/mem/sched/nodes/pipeline/pressure/slm/autonomy/user/sec/time/version`,
    미지 토픽 오류 응답까지 순차 검증 → `reboot`로 클린 종료 (`-no-reboot` 덕에 QEMU exit)
  - 응답 프로토콜: 한 줄 `[STATE] <topic> key=value ...` (값에 공백 없음).
    리스트형 토픽(`state nodes`)은 요약 한 줄 + 항목당 `[STATE] node id=...` 한 줄
  - `state autonomy`는 schema, 안전 모드, target별 지원도, queue/event 통계와 마지막 decision/reason을 read-only로 노출
  - `state pressure`는 schema 1, observation-only/gate 분리 계약, 세 pressure plane과 raw queue/fabric/NodeBit 증거를 한 줄로 노출
  - 각 교환은 한 response record의 토큰 경계로 검증하고, 종료 전 reader를 drain한 뒤
    전체 transcript에 normal boot verdict를 다시 적용한다
  - 아티팩트: `kernel/build/shell-smoke/transcript.log` (전체 시리얼 대화),
    `kernel/build/shell-smoke/summary.json` (교환별 pass/fail, boot verdict, termination)
  - `--skip-build`로 기존 ISO 재사용 가능
  - 새 `state` 토픽을 추가하면 `lib/shell_lane.py`의 `DEFAULT_EXCHANGES`에 교환을 등록한다

부팅 성능:

- `boot-perf`
  - `kernel/build/boot-summary/test-<profile>.json`을 재사용해 perf record를 비교
  - current 결과는 `kernel/build/boot-perf/current/<profile>.json`
  - baseline은 로컬 전용 `kernel/build/boot-perf/baseline/<profile>.json`
  - 기본 비교는 `memcpy MiB/s`, `memset/memcpy/memmove cyc_per_kib`, `dram latency x100`
  - 비교는 같은 profile/size/iterations/tier와 finite positive metric만 허용
  - `--write-baseline`은 inventory와 같은 trusted-source guard와 필수 perf metric 완전성을 요구

권장 사용 (저장소 루트에서 실행):

```powershell
py -3 -m unittest discover -s tools/testkit/tests -t tools/testkit -p "test_*.py" -v
pwsh -NoProfile -File .\tools\testkit\tests\test_build_windows_verdict.ps1
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

- 현재 검증 계약과 단계별 로드맵의 정본은
  `docs/tools/verification_tooling_evolution_design_ko.md`다
- `docs/tools/boot_kernel_testkit_expansion_plan_ko.md`는 초기 확장 기록으로 유지한다
