# AIOS 검증 도구 진화 설계

작성일: 2026-07-15  
최종 갱신: 2026-08-15 (native K2-a binding exact/fail-closed 계약)
기준 시작 체크포인트: `463a8b9`

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
5. **사람용 로그와 기계용 계약을 구분한다.** 문자열 마커와 bounded process event journal v1의 구조 계약은 함께 검증한다. generic `[EVT]{json}`, shared manifest, `events.jsonl` 전환은 아직 장기 계획이다.
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
- AI Resource Ledger의 5개 aggregate row, append-only kind/unit,
  validity/unattributed/observation-only selftest
- AI Pressure Tracker balanced/hotspot reducer, Memory Fabric overlap,
  gate-mask 분리 selftest와 observation-only required marker
- PID 1 / slot 0 뒤 PID 2 / slot 1의 private CR3 synchronous ring3 실행
- process별 16KiB ring0 entry stack, TSS `rsp0` publish/readback/restore
- 각 process의 세 `int 0x80` entry가 자기 process stack 위치에 있음을 확인
- ring3 entry AC hardening: CPL3 `#BP` common entry 2회와 `int 0x80` 6회에서
  saved user RFLAGS 불변, live AC=0을 확인. SMAP active는 `clac`, 비활성·미지원은
  `pushfq/btr/popfq` fallback이며 `common_saved_ac=2 int80_saved_ac=4`
- 실행 사이와 최종 CR3, caller IF, private leaf policy, backing scrub, current owner, process stack canary 복원 proof
- exact pair record의 PID/slot/CR3/backing/stack 고유성 및 `process_pair` 구조 파싱
- 각 process descriptor에 복사한 176B trap evidence snapshot의 PID/slot,
  run generation, CR3, BSP `rsp0`, capture sequence 귀속과 각 cleanup 및
  최종 pair 경계의 양쪽 재조회 보존 proof
- `[PROC] trap evidence snapshot PASS schema=1 ...` exact record와
  `process_trap_snapshot` 구조 파싱. 이 snapshot은 `resume_ready=0`인 관찰 증거이며
  재개 가능한 context나 switch state가 아니다.
- capacity 8/no-overwrite process event journal v1의 acquire/capture/release 여섯
  record와 별도 event/capture sequence, PID/slot/generation, before/after CR3·`rsp0`,
  owner/IF/frame-reference/outcome vector. exact `[PROC] process event journal PASS ...`
  계약은 `evidence_only=1 switch_events=0 resume_ready=0`이며 lifecycle
  `0→1→0→2→0`은 순차 bootstrap 증거이지 CPU switch가 아니다.
- K1 Room→Cell→Node→NodeBit hierarchy v0: schema 1/1024B snapshot, capacity
  2/4/8, bootstrap count 1/1/2, exact parent/source/generation, zero tail과
  duplicate/orphan/unknown/stale/overflow rejection. 전체 조각은
  `observation_only=1 management_only=1`이며 apply/authorize edge가 없다.
- native K2-a source binding: producer-owned 64B SLM MAIN source copy와 K1 ABI에
  독립적인 schema 1/256B binding snapshot. canonical/binding/source generation을
  분리하고 init-order, missing, schema/malformed, overflow, duplicate, orphan,
  namespace/kind/role/instance mismatch, zero/rollback/stale generation, non-zero tail을
  fail-closed로 거부한다. boot-local immutable oracle이며 refresh/apply edge가 없다.
- health registry, Kernel Room snapshot, panic/exception serial output
- C structure/enum static asserts, linker layout assert, stack protector

### 4.2 외부 도구 — `CURRENT`

- `all`, `kernel`, `os`, `info`
- `boot-matrix`, `boot-inventory`, `boot-perf`, `shell`
- `full`, `minimal`, `storage-only` smoke profiles와 별도 `default`, `max-smap` CPU profiles
- Python/Linux QEMU path and Windows PowerShell kernel path
- boot summary, matrix, inventory, perf, shell transcript/summary artifacts
- cppcheck CI gate and Linux full smoke/minimal shell gate
- normal boot verdict v1의 전체 로그 fatal, anchored/token-boundary evidence,
  health, terminal-chain, duplicate-key/exact-record 판정과 host unit test
- Python/PowerShell 공통 resource/pressure exact required marker, structured
  boot-summary `resource`/`pressure` section, process trap snapshot과 event journal의
  exact required marker, `process_trap_snapshot`/`process_event_journal` section,
  shell `state resource`/`state pressure`와 `state user`의 `saved_*`/`event_*`
  same-record 계약
- CPU profile별 exact `[SEC] ring3 entry AC hardening PASS ...`와 `state sec`
  canonical full-row 계약. `default`는 fallback 2/6과 `gate_skips=8`,
  `max-smap`은 CLAC 2/6과 `gate_skips=0`을 요구하고 양쪽 모두
  `common_post_ac0=2 int80_post_ac0=6 gate_mismatch=0`이어야 함
- K1 exact `[ROOM] management hierarchy selftest PASS ... tail_rejected=1`과
  structured `kernel_room_management`, `state room` canonical full row. missing,
  duplicate, truncation, extension, field drift와 family sibling을 fail-closed로 거부
- K2-a exact `[ROOM] source binding selftest PASS ... tail_rejected=1`과 structured
  `kernel_room_binding`, `state binding` canonical full row. typed identity, three
  generations, producer ownership/copied read, named rejection, duplicate/truncation/
  extension/family sibling을 Python과 PowerShell에서 fail-closed로 거부
- structured boot-summary `security`의 feature/entry record count, fullmatch,
  ASCII uint32 anchored full-row·안정성 의미 검사를 통과한 ROOM exact-one,
  `feature < entry-AC < legacy ROOM` 순서, requested profile,
  `profile_match`, `ready` 결속
- 별도 `kernel_room_binding.ready`의 exact-one semantic과
  `entry-AC < K1 management < K2 binding < legacy ROOM` 순서 결속
- Linux CI의 기본 CPU all/shell에 더해 `max-smap` minimal kernel smoke와
  `max-smap` shell lane
- `process_trap_snapshot.ready`는 `record_count`(prefix 행 수)=1,
  `fullmatch_count=1`, 모든 semantic 값 exact를 함께 만족할 때만 참
- `process_event_journal.ready`도 prefix record와 fullmatch가 각각 정확히 하나이고,
  six-record ordered vector, `dropped=0 overflow=0 evidence_only=1 switch_events=0
  resume_ready=0`이 모두 exact일 때만 참
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
- entry AC hardening의 `CURRENT` 범위는 QEMU CPL3 `#BP`와 `int 0x80`,
  `default`/`max-smap` 재현까지다. future ring3 IRQ/NMI/IST entry와 실기기에서
  같은 계약이 성립한다는 증거는 아직 없다.
- exception frame의 C/NASM offset·크기 계약과 CPL0/CPL3 `from_user` 판별은 2026-08-02에 `CURRENT`가 됐다(`trapframe.h` static assert + NASM mirror + `[TRAP]` canary 실경로 증명). long mode는 CPL0-origin frame에도 `rsp/ss`를 push하므로 두 frame은 같은 176B 레이아웃이고 CS RPL로만 구분한다. from_user fault teardown 분기는 아직 없다.
- PID 1→PID 2 순차 실행, 각 descriptor의 process-owned trap evidence snapshot,
  capacity 8/no-overwrite process event journal v1은 검증하지만 trapframe 기반 재개·전환과
  두 process 타이머 선점은 아직 없다. snapshot의 capture sequence 1,2와 journal의
  event sequence 1..6은 분리되며, 여섯 record가 보이는 owner lifecycle
  `0→1→0→2→0`은 A→B→A switch sequence가 아니다. 176B frame 계약,
  `from_user` 판별, non-resumable snapshot 귀속, evidence-only lifecycle journal까지만
  `CURRENT`다. resumable context와 runnable-state binding은 `PLANNED`다.
- pressure schema 1은 system→plane 두 단계의 순간 snapshot과 NodeBit 누적
  counter만 제공한다. fast/slow EWMA, stall window, domain/entity child와
  scheduler apply/migration은 아직 없다.
- resource schema 1은 owner-valid bit가 모두 0이고 `OWNER_UNATTRIBUTED=1`인
  5개 aggregate row다. cross-source snapshot은 single-BSP best-effort다.
  `SYS_INFO_RESOURCE=0x706`과 `state resource`는 CURRENT지만 per-owner attribution,
  공통 denial accounting, quota/apply는 아직 없다.
- K1 hierarchy와 native K2-a oracle은 고정 bootstrap Node 101 ↔ boot-local SLM MAIN
  binding 하나만 검증한다. source exit/recreate, refresh/rebind, live
  lifecycle/reconciliation, hosted source, K3 legacy NodeBit projection은 없다.

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
[USER] bootstrap process pair PASS
[TRAP] user frame capture PASS
[PROC] trap evidence snapshot PASS
[PROC] process event journal PASS
[SEC] nx=... smap_supported=... smap=...
[SEC] ring3 entry AC hardening PASS
[ROOM] management hierarchy selftest PASS schema=1 struct_size=1024 generation=1 cells=1 nodes=1 bound_nodes=1 nodebits=2 bound_nodebits=2 source_valid=1 generation_valid=1 duplicate_rejected=1 orphan_rejected=1 unknown_rejected=1 stale_rejected=1 overflow_rejected=1 tail_rejected=1 observation_only=1 management_only=1
[ROOM] source binding selftest PASS schema=1 struct_size=256 binding_generation=1 bindings=1 capacity=2 canonical_namespace=2 canonical_id=101 canonical_kind=1 canonical_generation=1 parent_cell_id=1 parent_generation=1 source_namespace=1 source_id=1 source_instance=1 source_generation=1 source_kind=1 source_role=1 kind_match=1 role_match=1 producer_owned=1 copied_read=1 missing_rejected=1 duplicate_rejected=1 orphan_rejected=1 namespace_rejected=1 kind_rejected=1 role_rejected=1 instance_rejected=1 zero_generation_rejected=1 generation_rollback_rejected=1 stale_rejected=1 init_order_rejected=1 schema_rejected=1 overflow_rejected=1 tail_rejected=1 source_valid=1 generation_valid=1 binding_valid=1 observation_only=1 management_only=1
[ROOM] snapshot stability=stable
[HEALTH] stability=stable
=== AIOS Kernel Ready ===
[KERNEL] Boot complete. Launching interactive shell...
[SHELL] Interactive shell started
```

초기 required proof에는 terminal chain보다 앞서 방출되는 다음 exact contract도
포함된다.

```text
[RESOURCE] ledger selftest PASS schema=1 kinds=5 units=2 entries=5 capacity=8 source_flags=31 limit_kinds=5 used_kinds=5 high_water_kinds=1 denied_kinds=0 owners_unattributed=1 observation_only=1
[PRESSURE] tracker selftest PASS schema=1 planes=3 max_levels=4 active_levels=2 balanced=1 hotspot=1 overlap=1 gate_mask=1 observation_only=1
[TRAP] frame contract selftest PASS size=176 canaries=15 int_no=3 err=0 cpl0=1 cs_match=1 ss_match=1 rip_exact=1 rsp_exact=1 frame_addr_exact=1 rflags_bit1=1 df_clear=1
[SEC] ring3 entry AC hardening PASS schema=1 smap_supported=0 smap=0 gate_active=0 common_entries=2 common_saved_ac=2 common_clac=0 common_fallback=2 common_post_ac0=2 int80_entries=6 int80_saved_ac=4 int80_clac=0 int80_fallback=6 int80_post_ac0=6 gate_skips=8 gate_mismatch=0
[SEC] ring3 entry AC hardening PASS schema=1 smap_supported=1 smap=1 gate_active=1 common_entries=2 common_saved_ac=2 common_clac=2 common_fallback=0 common_post_ac0=2 int80_entries=6 int80_saved_ac=4 int80_clac=6 int80_fallback=0 int80_post_ac0=6 gate_skips=0 gate_mismatch=0
```

위 resource/pressure/trapframe 레코드와 선택된 CPU profile에 해당하는 entry-AC
레코드 하나, terminal chain 안의 `[TRAP] user frame capture PASS`,
`[PROC] trap evidence snapshot PASS`, `[PROC] process event journal PASS`는 행 전체가
정본과 일치하고 정확히 한 번만 나타나야 한다. journal 정본은 여섯 record의
sequence/kind/reason/from/to PID/slot/generation/capture sequence/owner/CR3/`rsp0`/IF/
snapshot reference/outcome vector와 `dropped=0 overflow=0 evidence_only=1
switch_events=0 resume_ready=0`을 한 행에서 결속한다. 누락, 불완전·확장 필드,
`gate_mask=0`, `observation_only=0`, snapshot 또는 journal 값 변조, 정본 뒤
`apply_enabled=1` 같은 상충 증거는 Python/PowerShell 양쪽에서 정상 PASS가 아니다.
entry-AC 레코드는 `smap_supported`, `smap`, `gate_active`, CLAC/fallback/skip
카운터가 선택된 CPU profile과 맞지 않거나 saved-AC challenge, post-AC0,
`gate_mismatch=0` 중 하나라도 다르면 FAIL한다. saved user RFLAGS와 live kernel
AC는 서로 다른 증거이므로 saved AC를 지운 결과를 hardening 성공으로 인정하지 않는다.

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
| 순수 boot verdict evaluator | `CURRENT` | 전체 로그 fatal, anchored evidence, duplicate key/exact record, health, terminal order/duplicate 판정 |
| verdict host unit tests | `CURRENT` | panic-after-PASS, token/행 위장, 중복 키, health, resource/pressure/process/K1 hierarchy, security order/family, stale artifact, shell, baseline/perf 반례 고정 |
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
- C/NASM trapframe offset 및 전체 크기 계약 — `CURRENT` (2026-08-02, `[TRAP] frame contract selftest PASS` — 전 필드 static assert + NASM mirror + canary 15개 실경로 증명)
- `from_user` 판별과 CPL0/CPL3 frame 차이 처리 — 판별과 양쪽 frame 증거는 `CURRENT` (2026-08-02, ring3 `int3` 캡처 `[TRAP] user frame capture PASS`); from_user fault teardown 분기 동작은 아직 없다
- slot 1 실제 ring3 실행 — `CURRENT` (2026-07-26, 순차 pair proof)
- process-owned trap evidence snapshot v0 — `CURRENT` (2026-08-03, 각 static
  descriptor에 frame 사본과 owner/run generation/CR3/`rsp0`/capture sequence를
  결속하고 cleanup 뒤에도 같은 사본임을 검증). `resume_ready=0`이며 재개 가능한
  saved context는 아니다.
- process event journal v1 — `CURRENT` (2026-08-03, per-boot capacity 8/no-overwrite
  내부 journal의 acquire/capture/release 여섯 record와 exact ordered vector,
  structured `process_event_journal`, `state user event_*` mirror를 Python/PowerShell
  fail-closed 반례로 검증). event sequence와 capture sequence는 별도이며
  `evidence_only=1 switch_events=0 resume_ready=0`이다.
- ring3 `#BP` common entry + `int 0x80` entry AC hardening — `CURRENT`
  (2026-08-09, saved user RFLAGS 불변과 live AC=0, SMAP `clac`/non-SMAP
  fallback을 `default`/`max-smap` exact marker와 `state sec`로 재현)
- CPL3 timer IRQ의 process entry stack 귀속 증거
- 실제 A -> B -> A의 PID, CR3, BSP `rsp0`, current owner live-switch 순서 이벤트
- IF=0 원자 전환과 stale current 부재
- full register canary 보존
- 두 주소공간의 동일 VA 격리 유지
- syscall 왕복과 process fault teardown
- bounded execution budget
- 반복 전환에서 첫 실패 trace 보존

aggregate counter만으로 순서를 추론하지 않는다. 현재 journal의 `0→1→0→2→0`
lifecycle은 순차 bootstrap 관찰이다. 실제 process 전환은 resumable context와 원자 교대를
갖춘 뒤 별도 live-switch kind와 sequence로 보존한다.

## 11. CI 계층

### PR fast gate — 목표

- verdict/parser host unit tests
- cppcheck
- post-link structural checks
- 기본 CPU full smoke
- minimal shell lane

### PR matrix — 목표

- `full`, `minimal`, `storage-only`
- `default`, `max-smap` CPU profile verifier와 CI의 `max-smap` minimal/shell은
  `CURRENT`; smoke-profile×CPU-profile 전체 교차 matrix는 목표
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
인용/접두사/들여쓰기 마커, 중복 key/exact record, stale shell artifact,
불완전 baseline/perf는 모두
단위 테스트에서 FAIL한다.

구현 근거:

- `tools/testkit/lib/boot_verdict.py`, `baseline_guard.py`
- `tools/testkit/tests/` host unit test 90개
- PowerShell 직접 verdict host selftest 131개와 CI 선행 gate
- Resource Ledger exact marker/structured summary와 missing/truncated/
  observation-only/apply-capable 상충 반례
- pressure marker도 같은 exact-record 규칙으로 강화해 trailing apply 필드를 거부
- process trap snapshot의 세 profile 공통 required/exact-once 판정,
  malformed/extended/duplicate/owner/sequence/current/stale/resume 반례와
  structured `process_trap_snapshot`, shell `state user saved_*` mirror
- process event journal의 세 profile 공통 exact ordered-vector 판정,
  missing/duplicate/truncated/extended/reordered/stale/sequence/overflow/
  switch-capable 반례, structured `process_event_journal`, shell `state user event_*` mirror
- ring3 entry-AC marker의 `default`/`max-smap` profile별 exact 판정,
  saved-AC/post-AC0/CLAC/fallback/skip/mismatch 반례와 shell `state sec entry_*` mirror
- structured `security.ready/profile_match`, ASCII uint32 anchored full-row·안정성 의미 검사를
  통과한 legacy ROOM exact-one와 `feature < entry-AC < legacy ROOM` 순서,
  Linux CI `max-smap` minimal/shell gate
- K1 hierarchy marker의 exact-once/full-row 판정, structured
  `kernel_room_management`, malformed/extended/duplicate/순서/negative-proof 반례,
  shell `state room` canonical full-row mirror
- native K2-a marker의 exact-once/full-row 판정, structured `kernel_room_binding`,
  malformed/extended/duplicate/order/typed identity/generation/negative-proof 반례,
  shell `state binding` canonical full-row mirror
- 2026-08-15 K1/K2-a 정규 재검증: default full/minimal/storage-only와 max-smap minimal
  strict kernel summary export, default/max-smap strict shell 18/18 PASS
- Python boot matrix의 QEMU `full/minimal/storage-only` 통과와 Python host unit 90개,
  PowerShell 직접 verdict host selftest 131개 통과
- shell 18개 교환(`state room`, `state binding`, `state resource`, `state pressure`, `state autonomy` 포함)과 `reader_drained=true reboot_ack=true clean_exit=true exit_code=0`,
  `termination.reason=guest-reboot-exit`, 전체 transcript boot verdict PASS
- strict boot inventory 3프로필 baseline 일치

### H1 연동 레인. OS-neutral source-binding trace/replay — `PLANNED`

- 세부 field, lifecycle, reason, fixture와 일정은
  [`H1 binding trace/replay 작업 준비서`](../os/h1_binding_trace_replay_workplan_ko.md)가
  소유한다.
- raw JSONL evidence, fixture manifest의 expected verdict, replay가 계산한 verdict를
  분리하고 exact-one terminal·연속 sequence·bounded size를 fail-closed로 검사한다.
- 동일 fixture가 Ubuntu/Windows에서 같은 outcome과 first reason을 내야 한다.
- 이 host-only 레인은 generic `[EVT]{json}` V1, QEMU 부팅, boot marker 또는 inventory
  baseline 갱신을 선행조건으로 삼지 않는다.
- 완료되어도 H1 contract/replay만 `CURRENT`로 승격할 수 있다. live native/hosted
  producer, Linux adapter, K2 reconciliation과 apply 경로는 계속 별도 상태다.

### V1. 실행 종료와 산출물 — `PLANNED`

- streaming serial collector와 terminal outcome
- timeout/guest-exit/host-kill 분리
- profile별 raw artifact bundle과 provenance
- shared marker manifest와 `make test`의 testkit 위임
- generic `[EVT]{json}` parser와 `events.jsonl` artifact. 현재 process event journal v1의
  bounded 내부 schema/summary는 이 일반 이벤트 이행을 완료한 것으로 보지 않는다.
- post-link structural verifier

### V2. 내부 fail-closed — `PLANNED`

- required UNKNOWN 및 health monotonicity
- address-space restore fail-stop
- NX outcome 결속
- 최소 expected degraded/panic hook

### V3. 두 process proof harness — `PARTIAL`

- 176B full trapframe C/NASM ABI와 CPL0/CPL3 실경로 canary — `CURRENT`
- slot 1 순차 실행 + exact cleanup pair record — `CURRENT`
- `process_pair` structured boot summary + missing/incomplete record host negative test — `CURRENT`
- process-owned non-resumable trap evidence snapshot, exact record,
  `process_trap_snapshot` summary와 `state user saved_*` mirror — `CURRENT`
- process event journal v1의 capacity 8/no-overwrite six-record lifecycle/capture evidence,
  exact ordered-vector record, `process_event_journal` summary와 `state user event_*`
  mirror — `CURRENT`
- 실제 QEMU CPL3 `#BP`/`int 0x80` entry AC hardening과 `default`/`max-smap`
  재현 — `CURRENT`
- 재개 가능한 saved context, runnable-state 결속, live continuation/switch와 실제
  A -> B -> A 전환 이벤트 — `PLANNED`
- timer IRQ entry stack 귀속과 실제 선점 — `PLANNED`
- future ring3 IRQ/NMI/IST entry와 실기기 AC proof — `PLANNED`
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
