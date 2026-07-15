# AIOS 검증 도구 진화 설계

작성일: 2026-07-15  
기준 체크포인트: `981e651` (`M3-b-3b2b: bind ring3 to process-owned entry stack`)

## 1. 문서 역할

이 문서는 AIOS의 **커널 내부 증거와 외부 판정 계약**을 함께 다루는 검증 아키텍처 정본이다.

- 실제 명령과 산출물 사용법은 `docs/tools/testkit_guide_ko.md`가 담당한다.
- 제품 기능과 마일스톤 순서는 `docs/meta/minimal_io_and_maturity_workflow_ko.md`가 담당한다.
- 커널 작업 중 실제로 확인된 지뢰는 `docs/meta/codex_handoff_tips_ko.md`가 담당한다.
- 이 문서는 검증기가 무엇을 성공과 실패로 판정해야 하는지, 다음 기능이 어떤 증거를 갖춰야 하는지를 고정한다.

이 문서는 일반 프로세스 모델, PMM/VMM, SMP, 드라이버 데이터 경로 자체를 설계하지 않는다. 해당 기능을 검증하는 계약만 다룬다.

## 2. 상태 표기

문서와 구현의 낙관적 드리프트를 막기 위해 다음 네 상태만 사용한다.

| 상태 | 의미 |
|---|---|
| `CURRENT` | 현재 코드와 정규 검증 경로에서 동작한다 |
| `PARTIAL` | 구현은 있으나 판정, 플랫폼, 실패 경로 또는 범위가 제한된다 |
| `SCAFFOLD` | 인터페이스나 수동 경로만 있고 정규 검증 계약은 없다 |
| `PLANNED` | 아직 코드가 없다 |

완료 표시는 코드, 테스트, 아티팩트의 세 근거가 함께 있을 때만 `CURRENT`로 바꾼다.

## 3. 설계 원칙

1. **증거와 판정을 분리한다.** 커널은 사실을 방출하고 호스트 도구는 성공 조건을 판정한다.
2. **정상 검증은 fail-closed다.** 증거 누락, 상충, 역순, 치명 이벤트, 불명확한 종료를 성공으로 해석하지 않는다.
3. **timeout은 성공이 아니다.** timeout은 hang 검출 경계이며 별도 outcome이다.
4. **PASS 뒤도 검사한다.** 필수 마커가 모두 나온 뒤의 panic, exception, FAIL도 전체 실행을 실패시킨다.
5. **사람용 로그와 기계용 계약을 구분한다.** 현재 문자열 마커는 유지하되 장기적으로 versioned event를 병행한다.
6. **기준선 쓰기는 검증보다 강하다.** 성공·완전성·출처가 증명된 결과만 candidate가 될 수 있다.
7. **내부 검증 실패 자체도 안전해야 한다.** CR3, TSS `rsp0`, IF, current owner의 복원이 불확실하면 계속 실행하지 않는다.
8. **프로덕션과 fault hook을 분리한다.** fault injection은 명시적인 test build/boot gate 없이는 활성화되지 않는다.

## 4. 현재 검증 구조

현재 흐름은 다음 다섯 층으로 구성된다.

```text
kernel selftest / static assert / health / panic guards
                       |
                       v
serial markers + shell `state` observations
                       |
                       v
boot_log parser + boot summary
                       |
                       v
kernel / shell / matrix / inventory / perf verdict
                       |
                       v
CI exit status + build artifacts
```

### 4.1 커널 내부 증거 — `CURRENT`

- memory correctness/microbench
- heap lock invariants
- address-space CR3 round trip
- private user leaf isolation
- bootstrap user tensor exclusion
- cooperative and timer-driven kthread switching
- uaccess negative probes and syscall contract probes
- SLM plan apply and Node pipeline selftest
- PID 1 / slot 0 private CR3 synchronous ring3 execution
- process-owned 16KiB ring0 entry stack, TSS `rsp0` publish/readback/restore
- three `int 0x80` entries at the expected process stack location
- CR3, caller IF, private leaf policy, backing scrub, process stack canary proof
- health registry, Kernel Room snapshot, panic/exception serial output
- C structure/enum static asserts, linker layout assert, stack protector

### 4.2 외부 도구 — `CURRENT`

- `all`, `kernel`, `os`, `info`
- `boot-matrix`, `boot-inventory`, `boot-perf`, `shell`
- `full`, `minimal`, `storage-only` smoke profiles
- Python/Linux QEMU path and Windows PowerShell kernel path
- boot summary, matrix, inventory, perf, shell transcript/summary artifacts
- cppcheck CI gate and Linux full smoke/minimal shell gate
- normal boot verdict v1의 전체 로그 fatal, anchored/token-boundary evidence,
  health, terminal-chain, duplicate-key 판정과 44개 host unit test
- shell 전체 transcript verdict, 동일 response record 검증, reader drain,
  reboot acknowledgement, QEMU exit-code/termination gate
- inventory/perf baseline의 strict matrix·profile·semantic record 완전성 guard와
  perf 비교 가능성·finite positive metric gate

`storage-only`는 현재 `minimal`과 QEMU 토폴로지가 같고 storage 관련 기대 계약만 더 강하다. 실제 storage fault topology로 과장하지 않는다.

## 5. 현재 판정 한계 — `PARTIAL`

### 5.1 외부 판정

- evidence summary parser는 여전히 첫 일치 중심이지만, 정상 verdict는 별도 evaluator가
  전체 로그의 fatal, 증거 행/토큰 경계, 중복 키, terminal chain을 검증한다.
- 일반 kernel/matrix QEMU smoke는 아직 timeout까지 기다린 뒤 강제 종료하며
  timeout/guest-exit/host-kill outcome을 구분하지 않는다.
- 일반 kernel/matrix 실패 verdict는 예외 메시지에 남지만 profile별 `verdict.json` artifact로 독립 보존되지는 않는다.
- Python과 PowerShell에 smoke 마커 목록이 중복되어 있다.
- `--strict`는 모든 판정 규칙을 강화하는 전역 모드가 아니다.
- baseline 쓰기는 strict·정상 matrix·정확한 profile·record 완전성을 요구하지만 candidate/승인 단계는 아직 분리되지 않았다.
- matrix 실행 시 raw `serial_output.log`가 profile별로 보존되지 않는다.
- kernel/matrix summary에는 git 상태, kernel/ISO hash, QEMU 명령·버전,
  toolchain 버전, 종료 이유가 없다. shell summary만 현재 termination을 보존한다.

### 5.2 커널 내부 판정

- 일부 address-space selftest는 boot CR3 복원 불확실을 SCHED degraded로만 반환할 수 있다.
- NX가 지원되지 않거나 강제되지 않은 상태와 ring3 proof의 내부 성공 조건이 완전히 결속되지 않았다.
- required subsystem의 `UNKNOWN`이 required failure로 계산되지 않는다.
- health state는 후속 mark가 앞선 심각도를 낮출 수 있다.
- ring3 synchronous runner에는 내부 실행 budget과 process fault teardown이 없다.
- 현재 exception frame은 CPL0-origin frame에도 `rsp/ss`가 있다고 표현하며 C/NASM offset 계약이 생성되지 않는다.
- PID 1 / slot 0 단회 실행만 검증한다. slot 1 실행과 두 process 선점은 아직 없다.

## 6. 정상 부트 판정 계약 v1

### 6.1 outcome

호스트 판정기는 실행 결과를 최소한 다음 outcome으로 구분해야 한다.

| outcome | 의미 |
|---|---|
| `PASS` | 모든 필수 증거, health, 순서, 종료 계약을 만족한다 |
| `FAIL` | 커널 또는 검증 계약이 명시적으로 실패했다 |
| `TIMEOUT` | terminal outcome 없이 시간 경계를 넘었다 |
| `INFRA_ERROR` | QEMU, toolchain, artifact I/O 등 호스트 인프라가 실패했다 |
| `SKIP` | non-strict 실행에서 지원 도구 부재로 실행하지 않았다 |
| `UNSUPPORTED` | 요청 profile/기능을 현재 호스트가 지원하지 않는다 |

`passed: bool`은 호환용 요약으로 유지할 수 있지만 정본은 outcome과 reason 목록이다.

### 6.2 필수 정상 조건

정상 smoke의 `PASS`는 다음 조건을 모두 요구한다.

1. profile별 required pattern이 모두 존재한다.
2. 전체 로그 어디에도 금지 이벤트가 없다.
3. health는 `stability=stable`, `degraded=0`, `failed=0`이다.
4. 최종 terminal chain이 정확히 한 번씩, 아래 순서로 나타난다.
5. 증거 마커는 정해진 행 시작점과 토큰 경계에서만 인정하며 contract-bearing
   한 행에 같은 key가 중복되면 거부한다.
6. shell lane은 모든 교환을 같은 response record에서 검증하고 reader drain,
   reboot acknowledgement, clean QEMU exit를 완료한다.
7. QEMU 종료 이유가 기록된다.

현재 V0 일반 kernel/matrix PASS는 1~5의 **로그 판정**까지 구현한다. shell lane은
1~7을 구현해 `guest-reboot-exit`, exit code, timeout, host kill을 summary에 남긴다.
일반 kernel/matrix의 streaming 종료 판정과 outcome 결속은 V1이며, 이 공백을
정상 종료 증명으로 과장하지 않는다.

현재 정상 부팅의 terminal chain:

```text
[USER] Ring3 scaffold ready=1
[PROC] bootstrap ownership selftest PASS
[USER] ring3 exec PASS
[USER] private address space exec PASS
[USER] bootstrap process stack PASS
[ROOM] snapshot stability=stable
[HEALTH] stability=stable
=== AIOS Kernel Ready ===
[KERNEL] Boot complete. Launching interactive shell...
[SHELL] Interactive shell started
```

드라이버와 초기 selftest 전체의 엄격한 전역 순서 검증은 v1 범위에 넣지 않는다. 먼저 process 이후 terminal chain만 고정해 false positive를 줄인다.

### 6.3 금지 이벤트

정상 smoke에서는 다음 이벤트를 발견한 즉시 `FAIL`로 기록한다. 필수 마커 뒤에 나타나도 예외가 아니다.

- `*** KERNEL PANIC ***`
- `!!! EXCEPTION`
- 단어 경계의 대문자 `FAIL`
- 단어 경계의 대문자 `FATAL`

`failed=0`, `apply_failed=0` 같은 소문자 상태 필드는 금지 이벤트가 아니다. 향후 expected-fault lane은 별도 contract로 허용 이벤트와 기대 outcome을 선언한다.

### 6.4 판정 결과 스키마

v1 verdict에는 최소한 다음 필드가 있어야 한다.

```text
schema_version
outcome
passed
reasons[]
missing_patterns[]
fatal_events[{kind,line,text}]
health{parsed,stability,degraded,failed,passed}
checkpoints{expected_order,occurrences,duplicates,order_violations,passed}
termination{reason,exit_code,timed_out,duration_ms}
```

순수 log verdict는 raw artifact와 같은 line number, first failure, duplicate evidence를
보존한다. shell은 termination을 채우지만 일반 kernel/matrix verdict의 termination은
`not-evaluated`다. streaming termination 결합은 다음 단계다.

## 7. 기준선 쓰기 계약

baseline 갱신은 일반 비교보다 엄격해야 한다.

- `--write-baseline`은 `--strict`와 함께만 허용한다.
- matrix aggregate가 `passed=true`여야 한다.
- 요청 profile과 결과 profile의 개수·순서가 정확히 같아야 한다.
- skip, unsupported, 누락 summary가 하나라도 있으면 거부한다.
- inventory controller/process proof는 profile별 기대값과 정확히 맞아야 한다.
- perf는 같은 profile/size/iterations/tier끼리만 비교하며 양쪽 metric이 finite positive여야 한다.
- guard를 모두 통과하기 전에는 checked-in fixture를 수정하지 않는다.
- 장기적으로 candidate 생성과 승인/복사를 분리한다.

inventory와 perf가 같은 출처 신뢰 규칙을 공유해야 한다.

## 8. 외부 판정기 목표 구조

| 구성요소 | 상태 | 역할 |
|---|---|---|
| 순수 boot verdict evaluator | `CURRENT` | 전체 로그 fatal, anchored evidence, duplicate key, health, terminal order/duplicate 판정 |
| verdict host unit tests | `CURRENT` | panic-after-PASS, token/행 위장, 중복 키, health, shell, baseline/perf 반례 44개 고정 |
| shell reboot/clean-exit gate | `CURRENT` | 전체 transcript verdict, reader drain, reboot ack, exit code 0을 PASS 조건으로 강제 |
| baseline trusted-source guard | `CURRENT` | strict matrix/profile/verdict, profile-aware inventory와 comparable finite perf 검사 |
| shared marker manifest | `PLANNED` | Python/PowerShell 중복 계약 제거 |
| streaming serial collector | `PLANNED` | terminal event에서 즉시 판정하고 timeout 의미 분리 |
| per-run artifact bundle | `PLANNED` | profile별 raw log와 provenance 보존 |
| post-link verifier | `PLANNED` | ELF type/entry/W^X/GNU_STACK/relocation/layout 검사 |
| panic symbolizer | `PLANNED` | RIP를 kernel ELF와 `addr2line`로 자동 해석 |
| repeat/stress executor | `PLANNED` | 첫 실패 seed와 아티팩트를 보존하는 반복 실행 |

현재 설치된 cross `readelf`, `objdump`, `nm`, `addr2line`, GDB는 post-link와 panic triage에 재사용할 수 있다. 새 의존성을 먼저 추가할 필요는 없다.

## 9. 커널 내부 fail-closed 목표

다음 항목은 외부 verdict v1 이후의 독립 커널 슬라이스다.

1. required subsystem `UNKNOWN`을 unsafe로 취급한다.
2. health 심각도는 같은 boot generation 안에서 낮아지지 않는다.
3. address-space selftest의 boot CR3 복원 불확실은 IF를 다시 열지 않고 fail-stop한다.
4. NX 미지원과 미강제를 명시적 outcome/reason으로 구분하고 정상 security profile의 ring3 PASS 조건에 결속한다.
5. process CR3, BSP TSS `rsp0`, current owner, IF의 복원 실패는 공통 fail-stop reason으로 남긴다.
6. fault injection reason은 안정된 숫자 ID와 append-only 규칙을 사용한다.
7. test-only fault hook은 production build에서 제거되거나 명시적 boot/test gate 없이는 비활성이다.

숫자 reason/action ID를 추가할 때는 enum/ABI 무결성 검토를 함께 수행한다.

## 10. M3-b-3b2c 진입 검증 게이트

full trapframe과 두 ring3 process 선점 교대로 넘어가기 전에 최소한 다음을 갖춰야 한다.

- 정상 부트 verdict v1 host unit tests
- fatal-after-PASS와 health 값 검증
- C/NASM trapframe offset 및 전체 크기 계약
- `from_user` 판별과 CPL0/CPL3 frame 차이 처리
- slot 1 실제 ring3 실행
- CPL3 timer IRQ의 process entry stack 귀속 증거
- A -> B -> A의 PID, CR3, BSP `rsp0`, current owner 순서 이벤트
- IF=0 원자 전환과 stale current 부재
- full register canary 보존
- 두 주소공간의 동일 VA 격리 유지
- syscall 왕복과 process fault teardown
- bounded execution budget
- 반복 전환에서 첫 실패 trace 보존

aggregate counter만으로 순서를 추론하지 않는다. process 전환은 sequence가 있는 기계 판독 이벤트로 보존한다.

## 11. CI 계층

### PR fast gate — 목표

- verdict/parser host unit tests
- cppcheck
- post-link structural checks
- 기본 CPU full smoke
- minimal shell lane

### PR matrix — 목표

- `full`, `minimal`, `storage-only`
- 기본 CPU와 `-cpu max` security profile
- inventory strict comparison

### nightly/advisory — 목표

- expected degraded/panic fault lanes
- repeat/stress runs
- host-local perf advisory
- sanitizer/host fuzz

현재 Windows CI는 OS tool smoke만 수행한다. Windows kernel parity는 `PLANNED`이며 현재 상태로 과장하지 않는다.

## 12. Artifact와 provenance

권장 장기 경로:

```text
kernel/build/test-runs/<run-id>/<profile>/
  run-manifest.json
  serial.log
  events.jsonl
  verdict.json
  boot-summary.json
  postlink.json
```

`run-manifest.json`은 다음을 포함해야 한다.

- git SHA와 dirty 여부
- kernel/ISO SHA-256
- QEMU 실행 파일, 버전, 전체 args
- machine, CPU, RAM, accelerator
- compiler, linker, NASM 버전
- 시작/종료 시각과 duration
- 종료 이유, exit code, timeout 여부
- serial/events/verdict artifact hash

## 13. 단계별 로드맵

### V0. 정상 판정 신뢰성 — `CURRENT` (2026-07-15)

- 순수 verdict evaluator
- fatal/health/terminal order/duplicate와 anchored token/duplicate-field 판정
- 합성 로그 host unit tests
- shell same-record health, 전체 transcript, reader drain, clean-exit를 PASS 조건에 포함
- inventory/perf baseline trusted-source와 semantic comparability guard
- CI에서 host unit test를 QEMU보다 먼저 실행

완료 조건: 정상 로그는 PASS하고, PASS 뒤 panic·health degraded·역순·중복,
인용/접두사 마커, 중복 key, stale shell artifact, 불완전 baseline/perf는 모두
단위 테스트에서 FAIL한다.

구현 근거:

- `tools/testkit/lib/boot_verdict.py`, `baseline_guard.py`
- `tools/testkit/tests/` host unit test 44개
- PowerShell 직접 verdict host selftest 9개와 CI 선행 gate
- Python과 직접 PowerShell 정상 verdict 모두 QEMU `full/minimal/storage-only` 통과
- shell 14개 교환(`state autonomy` 포함)과 `reader_drained=true reboot_ack=true clean_exit=true exit_code=0`,
  `termination.reason=guest-reboot-exit`, 전체 transcript boot verdict PASS
- strict boot inventory 3프로필 baseline 일치

### V1. 실행 종료와 산출물 — `PLANNED`

- streaming serial collector와 terminal outcome
- timeout/guest-exit/host-kill 분리
- profile별 raw artifact bundle과 provenance
- shared marker manifest와 `make test`의 testkit 위임
- post-link structural verifier

### V2. 내부 fail-closed — `PLANNED`

- required UNKNOWN 및 health monotonicity
- address-space restore fail-stop
- NX outcome 결속
- 최소 expected degraded/panic hook

### V3. 두 process proof harness — `PLANNED`

- full trapframe ABI
- slot 1 실행
- A -> B -> A 전환 이벤트
- timer IRQ entry stack 귀속
- bounded repeat/stress

### V4. Fault/expected outcome — `PLANNED`

- test-only fault catalog
- controller init failure, allocator OOM, process activate/load/restore 실패
- expected `DEGRADED`, `FAIL`, `PANIC` contract
- reason ID와 마지막 checkpoint 보존

### V5. 다양성·분석 확장 — `PLANNED`

- CPU/machine/RAM/QEMU version matrix
- minimal freestanding UBSan과 host-side sanitizer
- boot log, ELF, uaccess parser fuzz
- host-side coverage
- SMP profile은 per-CPU TSS와 SMP 기반이 생긴 뒤에만 정규 gate로 승격

## 14. 변경 규율

검증 도구나 검증 대상 코드를 바꿀 때 다음 순서를 따른다.

1. 변경이 내부 evidence, 외부 verdict, CI/artifact, process gate 중 어디에 속하는지 분류한다.
2. 현재 marker/parser/baseline/shell/CI 소비자를 `rg`로 먼저 찾는다.
3. 새 검증 주장은 코드 위치, 방출 증거, host 판정, artifact를 함께 정의한다.
4. 정상과 최소 한 개의 반례를 먼저 host test로 고정한다.
5. 구현 전에는 이 문서에서 `CURRENT`로 표시하지 않는다.
6. baseline 갱신은 일반 구현 커밋과 분리 가능한 명시적 승인 동작으로 취급한다.
7. public numeric ID, enum, syscall, reason code는 append-only ABI 검토를 거친다.
8. 커널/스케줄러/MM/드라이버를 수정하면 해당 guardian skill과 검증 경로를 추가로 적용한다.

## 15. 문서 관계와 이전 계획 처리

| 문서 | 역할 |
|---|---|
| 이 문서 | 검증 아키텍처, 판정 계약, 진화 로드맵 정본 |
| `docs/tools/testkit_guide_ko.md` | 현재 구현의 명령과 산출물 운용 가이드 |
| `tools/testkit/README.md` | 짧은 사용자 진입점 |
| `docs/tools/boot_kernel_testkit_expansion_plan_ko.md` | 2026-04 초기 확장 기록; 새 정본 링크가 필요한 이전 문서 |
| `docs/tools/test_tooling_ko.md` | 초기 testkit 구조 기록 |
| `CLAUDE.md` | 현재 빌드 명령과 커널 불변식 |
| `docs/meta/codex_handoff_tips_ko.md` | 실전 지뢰와 작업 관례 |
| `docs/meta/minimal_io_and_maturity_workflow_ko.md` | 제품 성숙도 마일스톤 정본 |

shared marker manifest가 실제 구현되기 전까지는 Python과 PowerShell 양쪽 smoke marker를 함께 갱신하는 현재 규칙이 유효하다.
