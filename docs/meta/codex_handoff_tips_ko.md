# Codex 작업 핸드오프 팁 (2026-07-14)

이 커널에서 Claude가 M1~M3 작업 중 실제로 밟은 지뢰와 관례를 모았다. 다음 작업자(Codex)가 같은 함정에 빠지지 않도록 하는 실전 노트다. **CLAUDE.md의 규칙이 정본이고, 이 문서는 "왜 그런지"와 "어떻게 디버깅했는지"를 보완한다.**

M3-b-3a(주소공간 전환 primitive) 작업은 우리 규약을 정확히 따랐다(셀프테스트 마커 + `state` 노출 + 스모크 3곳 + shell 레인 + 문서). 이 형식을 계속 유지하면 된다.

---

## 1. 작업 사이클 (매 변경 공통) — 이대로만 하면 회귀 없음

1. **셀프테스트 우선.** 새 경로는 부팅 셀프테스트로 왕복 검증하고 `[XXX] ... PASS key=value` 한 줄 마커를 남긴다. 실패도 `[XXX] ... FAIL ...`로 한 줄.
2. **스모크 마커 필수화 (두 곳 동시!).** 마커를 반드시 **둘 다** 추가한다:
   - `tools/testkit/lib/kernel_lane.py`의 `required_smoke_patterns()` (리눅스/CI)
   - `tools/testkit/kernel/build-windows.ps1`의 `Get-SmokeRequiredPatterns` (윈도우 로컬)
   - 한쪽만 넣으면 다른 OS에서 스모크가 통과해버려 회귀를 놓친다.
3. **관측 연결.** 런타임 상태는 `state <topic>` 셸 토픽(한 줄 `[STATE] topic key=value`, 값에 공백 금지)으로 노출하고, 새 토픽/필드는 `tools/testkit/lib/shell_lane.py`의 `DEFAULT_EXCHANGES`에 교환을 등록한다. `state list`의 토픽 목록 문자열도 갱신.
4. **고정밀 계측.** 시간이 걸리는 경로는 `kernel_time_monotonic_ns()`(TSC 기반)로 재서 관측에 ns로 포함.
5. **검증 세트 (커밋 전 전부):**
   ```
   cppcheck --std=c11 --platform=unix64 --enable=warning,performance,portability \
     --inline-suppr --suppress=missingIncludeSystem --error-exitcode=1 -Ikernel/include kernel/
   pwsh -File tools/testkit/kernel/build-windows.ps1 -Target test                       # full
   pwsh -File tools/testkit/kernel/build-windows.ps1 -Target test -SmokeProfile minimal
   pwsh -File tools/testkit/kernel/build-windows.ps1 -Target test -SmokeProfile storage-only
   py -3 tools/testkit/aios-testkit.py shell --strict --skip-build                      # shell 레인
   ```
   Windows에서 `python`이 Store alias로 잡히면 `py -3`를 쓴다.
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
ring3에서 유저 페이지 접근 시 PML4→PDPT→PD→PT의 **모든** 레벨에 User 비트가 있어야 한다(최종 권한은 AND). PDE에만 U를 세우고 상위를 빼먹으면 #PF(error code에 instruction-fetch/user 비트). `user_exec.c`의 `map_user_region`이 p4/p3/p2에 모두 세우는 이유다.

### 2.6 #DF 프레임의 레지스터 값은 신뢰하지 말 것
#DF는 첫 폴트가 이미 스택을 손상시킨 뒤라 캡처된 RIP/RSP가 쓰레기일 수 있다(0x8 등). #DF가 뜨면 **원인은 직전의 다른 폴트**다. 이럴 땐 시리얼 디버그(`serial_printf`)로 실패 지점을 좁혀라 — 이 커널은 예외 시 CR2 덤프(#PF)와 전 예외 panic 봉쇄가 있어, 조용한 폴트 루프 대신 즉시 로그가 남는다.

### 2.7 Multiboot info 포인터는 EBP 보존값에서 복구한다
`_start`는 GRUB의 EBX(Multiboot info)를 EBP에 보존하지만 `setup_page_tables`가 EDI를 page-directory cursor로 덮는다. 과거 `long_mode_start`가 이 EDI를 C에 넘겨 부팅 로그의 info 주소가 항상 `p2_table_3`과 같았고, ACPI는 BIOS scan fallback 덕분에 통과했다. M3-b-3b1에서 `r13d = ebp`로 바로잡고, header/정렬/전체 tag chain/terminating tag를 bounded walk로 검증해 `[BOOT] Multiboot2 handoff PASS`로 남긴다. 실패한 handoff는 ACPI 같은 소비자에게 전달하지 않는다. 다시 EDI를 handoff 포인터로 사용하지 말 것. 실제 PMM 전에는 복구된 MBI의 memory-map tag 의미/예약 범위 셀프테스트와 Linux GRUB 교차검증이 별도로 필요하다.

---

## 3. 알아두면 좋은 구조

- **두 스케줄러 개념이 분리돼 있다:** `sched/ai_sched.c`는 vruntime 장부질만 하는 **워크로드 회계 모델**(실제 CPU 전환 없음). 진짜 문맥전환은 `sched/kthread.c`(+`kthread_switch.asm`)다. 헷갈리지 말 것.
- **두 NodeBit 체계가 병존한다:** 런타임 capability 게이트(`runtime/nodebit.c`)와 SLM 하드웨어 정책(`slm_orchestrator.c`의 `slm_nodebit`)은 별개 네임스페이스다. 억지로 합치지 말 것 — 통합은 로드맵 M7의 명시적 작업이다.
- **Kernel Room 게이트 테이블은 분류 메타데이터**다. 디스패처가 per-call로 검사하지 않는다. 실제 강제는 NodeBit 게이트/autonomy safe-mode/health 플래그가 한다. "모든 시스콜 전에 검사한다"고 서술하지 말 것.
- **시스콜 추가 시:** 번호는 추가만(재번호 금지), 그리고 **커버하는 Kernel Room 게이트의 `syscall_end`를 확장**해야 한다(`kernel/core/kernel_room.c`). 이걸 빼먹으면 ROOM 스냅샷이 새 시스콜을 분류에서 누락한다(체크포인트 때 실제로 드리프트가 났던 부분).
- **ABI 불변식:** SLM 스냅샷 구조체(`slm_hw_snapshot_t`)나 health 구조체 레이아웃을 바꾸면 소비자와 baseline이 깨진다. 관측 필드는 스냅샷 안이 아니라 별도 접근자로 노출하는 패턴을 따랐다(예: `slm_plan_observation_read`).

---

## 4. 문서 규율

- 미구현을 구현된 것처럼 쓰지 말 것. 상태는 실제 코드 기준으로.
- 완료 시 로드맵 문서(`docs/meta/minimal_io_and_maturity_workflow_ko.md`)의 해당 마일스톤에 "✅ 완료 (날짜)"와 검증 마커를 남긴다(M3-b-3a가 그렇게 했다).
- 낡은 문서는 이동/삭제하지 말고 상단에 `OLD` 배너 + 최신 기준 링크를 붙인다(`old_docs_check_2026_07_03_ko.md` 참조).

---

## 5. 다음 작업: M3-b-3b

`address_space_selftest`는 부트 PML4 복제 + CR3 왕복까지 증명했다(공유 매핑). 다음은:
1. **정적 주소공간 슬롯별 private user leaf proof ✅ M3-b-3b1 완료 (2026-07-14)** — 정적 2슬롯에서 유저 영역(현재 고정 64MB)을 서로 다른 2MiB backing에 매핑하고 canary 격리를 검증했다. 범용 주소공간 객체, PMM, 실제 프로세스 실행 연결은 아직 아니다.
2. **프로세스 소유 구조** — `process { address_space(CR3), registers, kernel_stack, capabilities }` (외부 평가 §6의 `struct process` 참고).
3. **ring3 프로세스 2개 선점 교대** — kthread 선점(M3-b-2) + CR3 스위치(M3-b-3a)를 결합. 타이머 틱에서 다음 프로세스로 CR3까지 전환.
4. 완료 기준: 두 ring3 프로세스가 각자 주소공간에서 시스콜을 왕복하며 선점 교대, `[SCHED]`/`[MM]`/`state user` 마커로 검증.

주의: ring3 프로세스 선점은 **유저→커널 진입 시 rsp0 스택**(현재 `syscall_stack`, 단일)이 프로세스마다 별도여야 한다. 지금은 단일 유저 슬라이스라 공유지만, 2개 이상이면 프로세스별 커널 스택이 필요하다.
