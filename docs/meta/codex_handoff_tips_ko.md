# Codex 작업 핸드오프 팁 (2026-07-15)

최종 갱신: 2026-08-03 (process event journal v1)

이 커널에서 Claude가 M1~M3 작업 중 실제로 밟은 지뢰와 관례를 모았다. 다음 작업자(Codex)가 같은 함정에 빠지지 않도록 하는 실전 노트다. **CLAUDE.md의 규칙이 정본이고, 이 문서는 "왜 그런지"와 "어떻게 디버깅했는지"를 보완한다.**

M3-b-3a부터 M3-b-3b2b까지(주소공간 전환 → private leaf → process-owned 동기 runner) 작업은 우리 규약을 정확히 따랐다(셀프테스트 마커 + `state` 노출 + 스모크 3곳 + shell 레인 + 문서). 이 형식을 계속 유지하면 된다.

---

## 1. 작업 사이클 (매 변경 공통) — 이대로만 하면 회귀 없음

1. **셀프테스트 우선.** 새 경로는 부팅 셀프테스트로 왕복 검증하고 `[XXX] ... PASS key=value` 한 줄 마커를 남긴다. 실패도 `[XXX] ... FAIL ...`로 한 줄.
2. **스모크 마커 필수화 (현재는 두 곳 동시!).** shared manifest가 도입되기 전까지 마커를 반드시 **둘 다** 추가한다:
   - `tools/testkit/lib/kernel_lane.py`의 `required_smoke_patterns()` (리눅스/CI)
   - `tools/testkit/kernel/build-windows.ps1`의 `Get-SmokeRequiredPatterns` (윈도우 로컬)
   - 한쪽만 넣으면 다른 OS에서 스모크가 통과해버려 회귀를 놓친다.
3. **관측 연결.** 런타임 상태는 `state <topic>` 셸 토픽(한 줄 `[STATE] topic key=value`, 값에 공백 금지)으로 노출하고, 새 토픽/필드는 `tools/testkit/lib/shell_lane.py`의 `DEFAULT_EXCHANGES`에 교환을 등록한다. `state list`의 토픽 목록 문자열도 갱신.
   부팅 전용 관측 조각은 structured boot summary와 Python/PowerShell exact
   required record를 함께 추가한다. Resource/pressure처럼 `observation_only=1`인
   레코드는 정본 뒤 상충 필드도 허용하지 않는다.
4. **고정밀 계측.** 시간이 걸리는 경로는 `kernel_time_monotonic_ns()`(TSC 기반)로 재서 관측에 ns로 포함.
5. **검증 세트 (커밋 전 전부):**
   ```
   py -3 -m unittest discover -s tools/testkit/tests -t tools/testkit -p "test_*.py" -v
   cppcheck --std=c11 --platform=unix64 --enable=warning,performance,portability \
     --inline-suppr --suppress=missingIncludeSystem --error-exitcode=1 -Ikernel/include kernel/
   pwsh -File tools/testkit/kernel/build-windows.ps1 -Target test                       # full
    pwsh -File tools/testkit/kernel/build-windows.ps1 -Target test -SmokeProfile minimal
    pwsh -File tools/testkit/kernel/build-windows.ps1 -Target test -SmokeProfile storage-only
    py -3 tools/testkit/aios-testkit.py shell --strict --skip-build                      # shell 레인
    py -3 tools/testkit/aios-testkit.py boot-inventory --profiles full minimal storage-only --strict
   ```
   Windows에서 `python`이 Store alias로 잡히면 `py -3`를 쓴다.
   normal verdict v1은 필수 문자열만 보지 않고 전체 로그의 panic/exception/대문자
   `FAIL`·`FATAL`, health, terminal checkpoint 순서·중복, 증거 행/토큰 경계와 중복 key를
   판정한다. shell PASS에는 같은 response record, 전체 transcript verdict, reader drain,
   reboot acknowledgement와 QEMU exit code 0도 필요하다. 최신 계약은
   `docs/tools/verification_tooling_evolution_design_ko.md`를 따른다.
6. **작업 브랜치는 `beta`.** main으로의 병합/PR은 사람이 결정한다.

---

## 2. 이 커널의 지뢰 (실제로 밟았던 것들)

### 2.1 `sti`는 PIC 리매핑(subsystem 7) 이후에만
부팅 초기 PIC는 레거시 매핑이라 **IRQ0 → 벡터 8 = #DF(더블 폴트)**다. subsystem 7(`kernel_timer_irq_init`)이 PIC를 0x20+로 리매핑하기 전에 `sti`를 하면 타이머 첫 틱이 #DF로 떨어진다. M3-b-1에서 셀프테스트 끝의 무조건 `sti`로 이걸 밟았다.
- **규칙:** 셀프테스트/초기화에서 IF를 켜야 하면 무조건 `sti` 대신 **이전 IF를 보존**한다:
  ```c
  uint64_t flags;
  __asm__ volatile ("pushfq; pop %0; cli" : "=r"(flags) :: "memory");
  /* ... critical ... */
  if (flags & (1ULL << 9)) __asm__ volatile ("sti" ::: "memory");
  ```
- 부팅 경로는 shell_run의 `sti`까지 IF=0이다. 이 상태를 함부로 바꾸지 말 것.

### 2.2 갓 스위치된 커널 스레드는 IF=0을 상속한다
`kthread_switch`는 callee-saved+rsp만 바꾸고 RFLAGS는 건드리지 않는다. IRQ/부트 컨텍스트(IF=0)에서 fresh 스레드로 처음 진입하면 그 스레드도 IF=0 → 타이머가 못 들어와 선점 불가 → 무한 루프 행. **선점 대상 스레드의 진입점 첫 줄에서 `sti`** 해야 한다(M3-b-2의 워커가 그렇게 한다). 이후 선점→복귀는 iretq가 IF=1을 복원하므로 첫 진입만 챙기면 된다.

### 2.3 선점 스위치 전에 EOI를 보낸다
타이머 IRQ 핸들러에서 컨텍스트를 바꿔 나가면, 그 틱의 EOI를 먼저 보내지 않는 한 PIC이 다음 타이머 IRQ를 다음 스레드에 전달하지 않는다. `idt.c`의 타이머 경로는 `kernel_timer_irq_handler()` → `pic_send_eoi()` → `kthread_preempt_tick()` 순서다. 이 순서를 지켜라.

### 2.4 커널이 유저 페이지를 만지면 SMAP fence로 감싼다
M1에서 SMAP을 켠 뒤, `user_exec`의 스테이징 `memcpy`가 즉시 #PF 났다 — SMAP이 브래킷 밖 유저 페이지 접근을 실제로 잡는 것. 커널이 유저 페이지(U=1)를 직접 read/write하는 모든 구간은 `user_access_fence_begin()`/`user_access_fence_end()`로 감싼다(SMAP 미지원 CPU에선 no-op). `copy_to_user`/`copy_from_user`는 이미 내부에서 감싸므로 그대로 쓰면 된다.

### 2.5 유저 접근은 4단계 페이지 전부 U/S=1이어야
ring3에서 유저 페이지 접근 시 PML4→PDPT→PD→PT의 **모든** 레벨에 User 비트가 있어야 한다(최종 권한은 AND). PDE에만 U를 세우고 상위를 빼먹으면 #PF(error code에 instruction-fetch/user 비트). M3-b-3b2a부터 `address_space.c`가 private PML4/PDPT/PD의 해당 경로에만 U/S를 설정하며, `user_exec.c`는 부트 page table을 직접 변경하지 않는다.

### 2.6 #DF 프레임의 레지스터 값은 신뢰하지 말 것
#DF는 첫 폴트가 이미 스택을 손상시킨 뒤라 캡처된 RIP/RSP가 쓰레기일 수 있다(0x8 등). #DF가 뜨면 **원인은 직전의 다른 폴트**다. 이럴 땐 시리얼 디버그(`serial_printf`)로 실패 지점을 좁혀라 — 이 커널은 예외 시 CR2 덤프(#PF)와 전 예외 panic 봉쇄가 있어, 조용한 폴트 루프 대신 즉시 로그가 남는다.

### 2.7 Multiboot info 포인터는 EBP 보존값에서 복구한다
`_start`는 GRUB의 EBX(Multiboot info)를 EBP에 보존하지만 `setup_page_tables`가 EDI를 page-directory cursor로 덮는다. 과거 `long_mode_start`가 이 EDI를 C에 넘겨 부팅 로그의 info 주소가 항상 `p2_table_3`과 같았고, ACPI는 BIOS scan fallback 덕분에 통과했다. M3-b-3b1에서 `r13d = ebp`로 바로잡고, header/정렬/전체 tag chain/terminating tag를 bounded walk로 검증해 `[BOOT] Multiboot2 handoff PASS`로 남긴다. 실패한 handoff는 ACPI 같은 소비자에게 전달하지 않는다. 다시 EDI를 handoff 포인터로 사용하지 말 것. 실제 PMM 전에는 복구된 MBI의 memory-map tag 의미/예약 범위 셀프테스트와 Linux GRUB 교차검증이 별도로 필요하다.

### 2.8 private 유저 VA가 low identity direct-map을 가린다
현재 커널은 첫 4GiB를 identity mapping하고 유저 VA 64MiB도 그 안에 있다. private CR3에서 이 leaf를 별도 backing으로 바꾸면 커널 VA 64–66MiB가 더는 물리 64–66MiB를 가리키지 않는다. 따라서 그 물리 구간을 tensor allocator가 반환하면 private CR3 체류 중 잘못된 backing을 접근한다. M3-b-3b2a는 해당 구간을 모든 tensor free list와 활성 tensor record에서 live 검사로 제외하고 `[MM] bootstrap user tensor exclusion PASS ... excluded=2097152 ... boundary=1 coalesce=1`를 필수화했다. 이건 tensor allocator의 관리 범위를 설정 960MiB에서 958MiB로 줄이는 bootstrap 안전장치일 뿐, 전역 PMM 예약이나 물리 메모리 소유권 증명은 아니다. 장기 해법은 high-half kernel direct map 또는 PMM 기반 유저 VA 배치다.

### 2.9 process CR3/TSS 전환은 IF=0 순서를 깨지 않는다
M3-b-3b2b의 활성화 순서는 `caller IF 저장+cli → private CR3 activate → BSP boot-TSS rsp0에 process stack top 게시 → CPL3`다. 복귀는 `process rsp0 → boot rsp0 exact 복원 → boot CR3 exact 복원 → private leaf seal/backing scrub → current owner 해제 → caller IF 복원` 순서다. IF를 먼저 열면 IRQ가 boot CR3/rsp0와 stale current process의 모순을 관측한다. IF readback 실패는 pending guard를 유지하고 다시 `cli`; int80 raw entry RSP의 stack 범위 이탈, stack floor canary 손상, TSS baseline 불일치, leaf seal 실패도 계속 부팅하지 않고 fail-stop한다. `syscall_stack_top`은 이제 BSP baseline/fallback일 뿐 실제 process 실행 스택이 아니다. 현재는 BSP 단일 boot TSS만 갱신하며 SMP/per-CPU TSS 구현이 아니다. `kstack_floor_canary=1`도 guard page가 아니라 8바이트 floor canary 생존 증거다.

### 2.10 interrupt gate는 DF를 자동으로 지우지 않는다
ring3가 `std`를 실행한 뒤 인터럽트/시스콜로 들어오면 live DF=1이 커널 C 경계까지 따라온다. `interrupt/isr_stub.asm`의 common C call과 `core/user_entry.asm`의 syscall C call 앞 `cld`를 제거하지 말 것. CPU가 저장한 user RFLAGS frame은 그대로라 `iretq`는 사용자 DF를 복원한다. exit처럼 `iretq` 없이 커널로 돌아오는 경로도 이 `cld` 덕분에 DF=0을 유지한다.

### 2.11 순차 pair counter는 선점 증거가 아니다
PID 1 실행 뒤 PID 2를 호출하는 것만으로는 scheduler 전환을 증명하지 못한다. pair runner는 첫 teardown 뒤 `current_pid=0`, `last_pid=1`, boot CR3/BSP `rsp0` baseline과 정확한 publish/restore 카운터를 확인하고서만 slot 1에 진입한다. 최종 `runs=2`, `last_pid=2`, `rsp0_publishes=2`, `rsp0_restores=2`는 두 번의 독립 synchronous run과 cleanup 증거다. process event journal v1도 이 기존 경계를 벗어나지 않는다. 여섯 acquire/capture/release record의 owner lifecycle `0→1→0→2→0`은 순차 bootstrap 관찰이며 A→B→A CPU switch가 아니다. 실제 live switch는 별도 event kind, saved context, CPL3 timer IRQ stack 증거가 모두 생긴 뒤에만 주장한다.

---

## 3. 알아두면 좋은 구조

- **두 스케줄러 개념이 분리돼 있다:** `sched/ai_sched.c`는 vruntime 장부질만 하는 **워크로드 회계 모델**(실제 CPU 전환 없음). 진짜 문맥전환은 `sched/kthread.c`(+`kthread_switch.asm`)다. 헷갈리지 말 것.
- **두 NodeBit 체계가 병존한다:** 런타임 capability 게이트(`runtime/nodebit.c`)와 SLM 하드웨어 정책(`slm_orchestrator.c`의 `slm_nodebit`)은 별개 네임스페이스다. 억지로 합치지 말 것 — 통합은 로드맵 M7의 명시적 작업이다.
- **Kernel Room 게이트 테이블은 분류 메타데이터**다. 디스패처가 per-call로 검사하지 않는다. 실제 강제는 NodeBit 게이트/autonomy safe-mode/health 플래그가 한다. "모든 시스콜 전에 검사한다"고 서술하지 말 것.
- **pressure와 gate bitmap은 별도 축이다:** `runtime/ai_pressure.c`의 점수는 부하/중첩 관측이고, eligibility는 `online & affinity & policy_gate & health & budget` 교집합이다. 거부된 목적지를 낮은 pressure로 위장하거나 두 값을 한 scalar로 섞지 말 것. 현재 tracker는 `observation_only=1`이며 scheduler apply/migration 호출자가 없다.
- **Memory Fabric `map_count`는 순간 동시성이 아니다:** attach는 증가시키지만 detach API가 없으므로 누적 logical-map 사건이다. pressure에는 reader/writer mask popcount, writer pair, read/write pair, weighted shared bytes만 사용한다.
- **pressure 계층 깊이를 과장하지 말 것:** schema 1은 `max_levels=4`지만 `active_levels=2`인 system→plane까지만 CURRENT다. domain/task/window/ring child, fast/slow EWMA, stall window, SMP migration은 PLANNED다.
- **resource ledger는 owner별 quota가 아니다:** `runtime/ai_resource.c` schema 1은
  heap/tensor bytes, active fabric windows, registered rings, runnable tasks의
  aggregate 5개 row만 읽는다. owner 4필드는 별도 validity bit를 가지며 현재는
  모두 `OWNER_UNATTRIBUTED=1`, owner-valid bit 0이다. ID 0 자체를 NONE으로
  고정하지 말 것. high-water는 tensor 1종만 valid하고 denial accounting은 없다.
  `SYS_INFO_RESOURCE=0x706`과 `state resource`는 CURRENT지만 reserve/apply와
  owner attribution은 PLANNED다.
- **시스콜 추가 시:** 번호는 추가만(재번호 금지), 그리고 **커버하는 Kernel Room 게이트의 `syscall_end`를 확장**해야 한다(`kernel/core/kernel_room.c`). 이걸 빼먹으면 ROOM 스냅샷이 새 시스콜을 분류에서 누락한다(체크포인트 때 실제로 드리프트가 났던 부분).
- **ABI 불변식:** SLM 스냅샷 구조체(`slm_hw_snapshot_t`)나 health 구조체 레이아웃을 바꾸면 소비자와 baseline이 깨진다. 관측 필드는 스냅샷 안이 아니라 별도 접근자로 노출하는 패턴을 따랐다(예: `slm_plan_observation_read`).
- **bootstrap run-state C/NASM ABI:** `kernel/include/kernel/process.h`의 explicit offset + size static assert와 `kernel/core/user_entry.asm`의 `RUN_STATE_*`가 한 쌍이다. 기존 offset은 재번호하지 말고 append-only로 늘린다. 실제 값은 process-local이지만 `g_active_user_run_state`는 현재 동기 runner 한 개를 가리키는 단일 active pointer다.
- **trapframe C/NASM 계약 (2026-08-02):** `interrupt_frame_t`의 모든 offset과 176B 크기는 `kernel/include/interrupt/trapframe.h`에 static assert되고 `isr_stub.asm`이 byte 상수를 mirror한다. 이 레이아웃은 append-only가 아니라 **exact 계약**이다(스텁 push 순서 + 하드웨어 frame이 고정). 함정 셋: ① `#BP` 게이트는 DPL=3이다 — ring3 `int3`가 CPL3 증거 경로라서, DPL=0로 되돌리면 유저 int3가 `#GP` panic이 된다. ② verdict가 로그 전체에서 `!!! EXCEPTION`을 금지하므로 셀프테스트 int3는 반드시 armed capture로 **조용히** 소비해야 한다(`trapframe_capture_consume`). ③ long mode는 frame push 전에 RSP를 16B로 내림 정렬한다 — same-CPL 기대 frame 주소는 `(rsp & ~15) - 176`이고, CPL3 진입은 TSS `rsp0`(=stack_top, 16B 정렬)라서 정확히 `stack_top - 176`이다. 데모의 int3는 int80 카운터를 건드리지 않는다(`int80_entries`는 여전히 3). 알려진 latent 항목: ring3에서 도달 가능한 공통 스텁/int 0x80 진입은 `cld`만 하고 `clac`은 하지 않으므로 유저가 세팅한 RFLAGS.AC가 핸들러까지 살아온다 — 현재 두 경로 모두 커널이 유저 메모리를 만지지 않아 무해하지만, 더 풍부한 ring3 IRQ 경로나 uaccess 밖 유저-page 접근을 넣기 전에는 SMAP 지원 CPU에서만 실행되는 feature-gated entry `clac` 하드닝을 별도 선행 후보로 검증한다.
- **process-owned trap evidence snapshot v0 (2026-08-03):** ring3 `int3`의 armed capture를 global 결과로만 끝내지 않고 ISR 안에서 live process descriptor에 full 176B 복사한다. 이때 current owner, private CR3, BSP TSS `rsp0`, IF=0, exact `stack_top-176`, CPL3 RFLAGS 경계를 함께 확인한다. snapshot은 각 finish 뒤와 두 번째 실행이 끝난 최종 pair 경계에서 양쪽 모두 다시 읽어 같은 값임을 확인하며, per-boot sequence 1,2는 캡처 순서 증거일 뿐 switch event sequence가 아니다. prepare 코드 경로는 이전 snapshot을 지우고 run generation을 올리지만, live slot reuse/re-prepare와 stale-generation 거부는 아직 부팅으로 증명하지 않았다. exact marker는 `[PROC] trap evidence snapshot PASS ... stale_owner=0 resume_ready=0`이다. 이 상태를 continuation/saved-state switch로 과장하지 말 것.
- **process event journal v1 (2026-08-03):** per-boot capacity 8의 고정 journal에 PID 1과 PID 2 각각의 acquire/capture/release를 append한다. 기존 record는 덮어쓰지 않고 여섯 record 뒤 `dropped=0 overflow=0`을 요구한다. event sequence 1..6과 capture sequence 1,2를 분리하며 exact kind/reason/from/to PID·slot·generation·owner/CR3/`rsp0`/IF/frame-reference vector를 `[PROC] process event journal PASS ... evidence_only=1 switch_events=0 resume_ready=0`, structured `process_event_journal`, `state user event_*` mirror로 고정한다. current lifecycle은 `0→1→0→2→0`이고 실제 CPU switch가 아니다. capacity 초과, 누락·중복·축약·확장·역순·stale owner·sequence 혼용은 fail-closed다.

---

## 4. 문서 규율

- 미구현을 구현된 것처럼 쓰지 말 것. 상태는 실제 코드 기준으로.
- 완료 시 로드맵 문서(`docs/meta/minimal_io_and_maturity_workflow_ko.md`)의 해당 마일스톤에 "✅ 완료 (날짜)"와 검증 마커를 남긴다(M3-b-3a가 그렇게 했다).
- 낡은 문서는 이동/삭제하지 말고 상단에 `OLD` 배너 + 최신 기준 링크를 붙인다(`old_docs_check_2026_07_03_ko.md` 참조).

---

## 5. 다음 실행축 작업

리소스 관리축의 Slice 2 read-only UAPI/`state resource`는 2026-08-02에 완료됐다.
다음 작은 후보는 bounded policy schema만 정의하는 Slice 3이며, handler/apply,
owner attribution, quota를 함께 넣지 않는다. 프로세스 실행축은 M3-b-3b2c를
계속 따른다.

2026-08-03 최신 조사에서 고른 **process-owned trap evidence snapshot v0**와
**process event journal v1**은 완료됐다. 기존 ring3 `int3` frame을 각 static process의
snapshot/validity/capture sequence에 결속하고, capacity 8/no-overwrite journal로
여섯 lifecycle/capture record를 보존한다. 둘 다 `evidence_only=1 switch_events=0
resume_ready=0` 경계이며 전체 process 모델은 계속 `PARTIAL`이다. 다음은 resumable
saved context와 runnable-state 결속을 포함한 live continuation/switch이고, bounded 실제
A→B→A를 증명한 뒤 마지막으로 timer preemption을 연결한다.
근거와 외부 참고선은 [AIOS 빌드 참고 프로젝트 최신 조사](aios_build_project_landscape_2026_08_03_ko.md)를 본다.

`address_space_selftest`는 부트 PML4 복제 + CR3 왕복까지 증명했다(공유 매핑). 다음은:
1. **정적 주소공간 슬롯별 private user leaf proof ✅ M3-b-3b1 완료 (2026-07-14)** — 정적 2슬롯에서 유저 영역(현재 고정 64MiB)을 서로 다른 2MiB backing에 매핑하고 canary 격리를 검증했다. 범용 주소공간 객체, PMM, 실제 프로세스 실행 연결은 아직 아니다.
2. **private CR3 단일 runner ✅ M3-b-3b2a 완료 (2026-07-15)** — slot 0에서 기존 ELF를 동기 실행한다. exact raw boot CR3와 IF bit를 readback한 뒤에만 leaf policy reset/backing scrub을 수행하고, `leaf_sealed`와 hardware `nx_enforced`를 분리해 관측한다. 물리 64–66MiB는 tensor free/active set에서 제외하지만 PMM 예약으로 과장하지 않는다.
3. **static bootstrap process + BSP TSS entry stack ✅ M3-b-3b2b 완료 (2026-07-15)** — 정적 descriptor 2개가 unique CR3/backing, process-local run state, unique 16KiB ring0 entry stack을 소유한다. 이 완료 시점에는 PID 1/slot 0에서만 `rsp0` exact publish/restore와 3회 `int 0x80`의 `stack_top-40` 진입을 증명했다.
4. **slot 1 순차 ring3 실행 + pair cleanup proof ✅ 완료 (2026-07-26)** — PID 1 teardown이 exact clean state인지 확인한 뒤 PID 2/slot 1도 동일 ELF를 독립 CR3/backing/entry stack에서 실행한다. 두 process 각각 syscall/uaccess/exit/복원을 통과하고 `[USER] bootstrap process pair PASS ... between_clean=1 ... both_restored=1`과 `state user`의 `pair_*` 필드로 노출한다. 이는 선점이 아니다.
5. **trapframe C/NASM 계약 + from_user 판별 ✅ 진입 게이트 완료 (2026-08-02)** — `interrupt_frame_t` 전 필드 offset과 176B 크기를 static assert + NASM mirror로 고정하고, CPL0 canary `int3`(`[TRAP] frame contract selftest PASS`)와 두 process의 ring3 `int3` 캡처(`[TRAP] user frame capture PASS`, `cs=0x23`/`ss=0x1b`/user RSP·RIP/`stack_top-176` exact)로 실경로를 증명했다. `state user`의 `trap_*` 필드로 노출된다. 이는 계약·판별 증거이며 아직 trapframe 기반 전환이 아니다.
6. **process-owned trap evidence snapshot v0 ✅ 완료 (2026-08-03)** — ISR 시점 owner/current/private CR3/TSS `rsp0`/IF=0 검사를 통과한 full 176B frame을 해당 descriptor에 복사한다. per-boot seq 1,2, finish 뒤 보존, final pair 경계 양쪽 재조회, distinct storage, final current owner=0을 exact `[PROC]` record와 `state user`의 `saved_*` 필드로 고정한다. next-prepare reset과 run generation 증가는 구현됐지만 live reuse/re-prepare 증거는 `PLANNED`다. `resume_ready=0`이며 schedulable continuation이 아니다.
7. **process event journal v1 ✅ 완료 (2026-08-03)** — append-only numeric kind/reason/outcome과 별도 event/capture sequence를 사용한다. capacity 8/no-overwrite 내부 journal의 여섯 acquire/capture/release record를 exact ordered vector, `[PROC] process event journal PASS`, structured `process_event_journal`, `state user event_*`로 고정하고 missing/duplicate/truncation/확장/역순/stale-owner/aggregate-only/overflow 반례를 Python과 PowerShell에서 fail-closed로 거부한다. `evidence_only=1 switch_events=0 resume_ready=0`이며 owner lifecycle `0→1→0→2→0`은 CPU switch 증거가 아니다.
8. **live continuation + ring3 process 2개 선점 교대** — process에 full trapframe continuation/runnable state를 결속하고, IF=0 원자 집합에 current process·CR3·BSP TSS `rsp0`·saved frame·`g_active_user_run_state`를 함께 넣는다. bounded A→B→A 후 kthread 선점(M3-b-2)과 timer IRQ를 연결하고, 두 ring3 process의 syscall 왕복과 순서 이벤트를 `[SCHED]`/`[MM]`/`state user`로 검증한다.

주의: 두 static process 모두 실제 ring3 실행은 하지만 현재 resume 모델은 여전히 순차 동기 C 호출 프레임이다. descriptor의 trap snapshot과 process event journal은 증거 소유권과 순서만 고정하며 `resume_ready=0`이다. journal 완료를 live transition 완료로 해석하지 말고, 다음 단계에서만 current process·CR3·BSP `rsp0`·saved frame·`g_active_user_run_state`를 IF=0에서 함께 교대하는 live continuation으로 나아가야 한다.
