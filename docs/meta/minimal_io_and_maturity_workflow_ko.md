# AIOS 성숙도 우선 작업흐름 가이드 (2026-08-10 재정렬)

최종 갱신: 2026-08-12 (Linux-hosted 기본 delivery와 K2 semantic gate 정렬)

**결정 배경:** 2026-07에는 ring3 첫 실행 슬라이스 뒤 "하드웨어 드라이버 확장 vs 기술 성숙도·정밀화" 중 **성숙도 우선**으로 결정했다. 2026-08-10에는 그 실행 M축이 프로젝트 방향을 독점하면서 본래의 Kernel Room 관리 구조가 뒤로 밀린 점을 바로잡았다. 2026-08-12에는 Linux-hosted userspace service를 의도된 기본 delivery substrate로 결정했다. 이 문서는 **Room→Cell→Node→NodeBit 관리축을 backend-neutral 의미 정본**으로 두고, Linux-hosted H축을 기본 delivery 구현축으로, 기존 M1~M5를 native reference/proof substrate 레인으로 유지한다.

---

## 1. 최소 I/O 실태 점검 (2026-07-03 역사적 스냅샷)

이 절은 M축이 만들어질 당시의 판단 근거를 보존한다. 현재 구현 성숙도는
루트 `README.md`와 `CLAUDE.md`를 우선하며, 아래 표를 2026-08 현재 상태로 재해석하지 않는다.

| 서브시스템 | 있는 것 | 없는 것 | 판정 |
|---|---|---|---|
| **storage_host** (`kernel/drivers/storage_host.c`) | PCI 열거·분류(IDE/SCSI/AHCI/NVMe), BAR 매핑, PCI enable, IDE 채널 상태 프로브 | **데이터 경로 전부** — IDENTIFY 없음, 섹터 읽기 없음, DMA 없음 | 부트스트랩 only. **최소 I/O의 최대 공백** |
| **e1000** (`kernel/drivers/e1000.c`) | RX 링(8 descriptor) + 단일 프레임 폴(`e1000_driver_rx_poll`), TX smoke, MAC/EEPROM 읽기 | 연속 수신 경로, 인터럽트 구동 RX, 버퍼 수명 관리 | **유일하게 실제 데이터 경로 보유**. 최소 수준 |
| **usb_host (xHCI)** | 컨트롤러 선택, caps 레지스터 읽기 | transfer ring, 디바이스 열거 | 부트스트랩 only |
| **ai_ring** (`kernel/include/runtime/ai_ring.h`) | SQ/CQ 공유 링 등록·notify·wait 시스콜 표면 | 실제 소비자(모델 런타임) | **구조는 이미 io_uring 동형** — §3 참조 |

**시사점:** "부팅 후 디스크에서 무언가를 읽는다"가 현재 커널이 못 하는 가장 기초적인 I/O다. 이것이 ELF 로더(디스크에서 유저 프로그램 적재)와 store/ 카탈로그(온라인 다운로드 후 저장) 비전의 공통 전제 조건이므로, 드라이버 작업의 올바른 재진입점은 **storage read 단 하나**다.

## 2. 최신 기술 조사 요약 (2026-07)

이 절도 당시 substrate 선택 근거다. 실제 드라이버 착수 전에는 현재 스펙과 QEMU
지원 상태를 다시 확인한다.

### 2.1 스토리지 데이터 경로: virtio-blk 우선 (권장)

QEMU 우선 개발 커널의 2026년 컨센서스는 레거시 IDE PIO가 아니라 **virtio-blk**다.

- virtio는 하이퍼바이저-게스트 간 표준 반가상화 인터페이스로, 실제 하드웨어의 quirk 에뮬레이션 없이 단순한 virtqueue(descriptor ring)로 블록 I/O를 처리한다. 취미/연구 OS에서 가장 구현이 단순한 블록 경로로 통용된다.
- 선택 구현 기준은 OASIS VIRTIO **1.2 CS01**이다. 더 최신 승인 규격인
  **1.4 CS01**은 현재 `RESEARCH` 비교선이며 1.2 기준을 조용히 대체하지 않는다.
  exact upstream 역할은
  [Linux-hosted substrate와 resource 정책](../os/linux_hosted_substrate_and_resource_policy_ko.md)을 따른다.
- QEMU 연결: `-drive file=disk.img,if=none,id=d0,format=raw -device virtio-blk-pci,drive=d0`
- **주의 (현재 코드와의 접점):** virtio-blk PCI는 vendor `0x1af4`이며 class code가 SCSI(0x01/0x00)로 보고되므로, 현재 `classify_controller()`는 이를 "SCSI"로 오분류한다. virtio 채택 시 vendor 0x1af4 기반 분기(`STORAGE_HOST_CONTROLLER_VIRTIO`)를 추가해야 한다.
- **자산 재사용:** virtqueue는 descriptor ring 모델이라 e1000 RX 링 구현 경험이 그대로 이식된다.

### 2.2 NVMe: 실기기 경로, 2단계

NVMe는 큐페어(SQ/CQ) 모델이 개념적으로 깔끔하지만 admin queue 셋업, doorbell, MSI-X 등 초기화 비용이 virtio-blk보다 크다. **실기기 호환이 필요해지는 시점(M4 이후)의 두 번째 백엔드**로 미룬다. `storage_host`의 분류·선택 구조는 이미 NVMe를 인지하고 있으므로 백엔드 추가 형태로 자연스럽다.

### 2.3 IDE PIO: 폴백 전용

QEMU 기본 `-hda`와 즉시 호환되고 수십 줄로 섹터를 읽을 수 있으나 레거시·저속·PIO 폴링이다. virtio-blk 장애 시 폴백/디버깅 용도로만 유지한다 (현재 스모크의 IDE 프로브는 그대로 유효).

### 2.4 유저스페이스 I/O ABI: io_uring 모델 (이미 보유한 방향)

리눅스 io_uring(5.1+, 2019~)이 확립한 현대 I/O 인터페이스 모델 — **SQ/CQ 공유 링 + mmap + 시스콜 배칭으로 per-I/O 시스콜 제거** — 은 이 저장소의 `ai_ring.h`(SYS_INFER_RING_*)와 동형이다. 결론:

> 유저스페이스 블록/네트워크 I/O ABI를 새로 설계하지 말 것. **ai_ring의 SQ/CQ 패턴을 블록 I/O로 확장**하는 것이 최신 기술과 기존 자산이 일치하는 경로다. per-call `SYS_STORAGE_READ` 같은 표면은 부트스트랩 용도로만 최소 유지한다.

## 3. 이후 작업흐름 가이드 (성숙도 우선 로드맵)

각 단계는 §4 작업 규약을 따른다. 관리 K축은 substrate와 무관한 canonical 의미를
소유하고 Linux-hosted H축은 의도된 기본 delivery 구현을 소유한다. 실행 M축은
native reference/proof, 지속성 C축과 브라우저 W축은 각각 명시된 제품 표면을
지지한다. Hosted 구현 결과가 K축 maturity를 자동 승격시키지는 않으며, 반대로
native substrate backlog가 기본 delivery 우선순위를 다시 독점하지 않는다.

## 3-A. Kernel Room 관리축 (K0~K5) — 우선 정본

목표 계층은 **Room → Cell → Node → NodeBit**다. 현재 코드에는 Room의 aggregate
snapshot, K1 bounded bootstrap hierarchy v0, 각자 `CURRENT`인 subsystem, 이를
Room에 연결할 `SCAFFOLD` adapter seam이 있다. Memory Fabric `domain_id`, SLM
`agent_tree.node_id`, SLM `slm_nodebit_id`, runtime NodeBit `node_id`, pipeline
`owner_node`, scheduler task/PID/ring ID는 서로 다른 네임스페이스이며 숫자 일치로
관계를 추론하지 않는다.

| 단계 | 상태 | 관리 의미 |
|---|---|---|
| K0 문서/namespace 재정렬 | 문서 기준선 완료 | 정본 계층과 현재 구현의 간극을 명시한다. 코드 성숙도 `CURRENT` 판정이 아니다. |
| K1 hierarchy registry v0 | `CURRENT` (2026-08-11) | 1024B snapshot에 Cell 1 + bound Node 1 + parent-bound NodeBit 2를 한 번에 소유·조회한다. |
| K2 source binding hardening/expansion | `PLANNED` | canonical Node의 외부 source binding과 generation/reconciliation을 확장한다. |
| K3 legacy NodeBit namespace projection | `PLANNED` | 선택한 legacy NodeBit를 namespace adapter로 canonical NodeBit에 read-only projection한다. |
| K4 resource/pressure attribution | `PLANNED` | canonical Cell/Node owner에 관측치를 귀속하되 `observation_only=1`을 유지한다. |
| K5 principal/ownership + Axis Gate | `PLANNED` | 검증된 identity/binding/generation 위에서만 authorize/enforcement를 추가한다. |

### K0. 문서 정본과 ID 경계 — 문서 기준선 완료

- Kernel Room에는 aggregate snapshot + 9개 syscall-range 분류 descriptor와 별도 K1
  bootstrap hierarchy registry가 있다.
- K1 밖 external Cell/Node/NodeBit source binding, lifecycle/reconciliation,
  principal/ownership는 없다.
- `SYS_SLM_NODEBIT_LOOKUP`는 `slm_orchestrator.c`의 SLM policy catalog를 읽는다.
  `runtime/nodebit.c`의 별도 syscall은 `SYS_NODEBIT_REGISTER/UPDATE/STATS`다.

### K1. Room-owned hierarchy registry v0 — ✅ 완료 (2026-08-11)

- **구현:** Cell ID 1, 그 Cell에 bound된 Node ID 101, 그 Node에 parent-bound된
  read-only canonical NodeBit ID 1001/1002를 **같은 고정 용량 registry와
  같은 vertical proof**에 넣는다. 각 record는 append-only ID/state/reason,
  generation, exact parent를 가진다. Cell-only 성공을 K1 완료로 판정하지 않는다.
- **경계:** 첫 조각은 management-only이며 scheduler/resource/policy apply 호출자가
  없어야 한다. legacy SLM/runtime source 연결은 아직 필요하지 않으며 K1 record는
  명시적인 bounded bootstrap/management source를 사용한다. unknown source를 성공으로
  축약하지 않는다.
- **실패 경계:** duplicate ID, capacity 초과, 잘못된 상태 전이, missing/orphan parent,
  stale generation, 초기화 전 조회를 fail-closed로 거부한다.
- **검증 계약:** schema 1/1024B, capacity Cell 2/Node 4/NodeBit 8과 exact
  `[ROOM] management hierarchy selftest PASS ... cells=1 nodes=1 bound_nodes=1
  nodebits=2 bound_nodebits=2 ... tail_rejected=1 observation_only=1 management_only=1`
  boot record, structured `kernel_room_management`, read-only `state room` full row를
  Python/PowerShell과 shell lane이 exact-one/fail-closed로 검사한다.
- **정규 증거:** default full/minimal/storage-only와 max-smap minimal strict kernel,
  default/max-smap strict shell 17/17, 각 boot summary export가 통과했다.

### K2. source binding hardening/expansion

- **작업:** K1의 canonical `main-ai` Node에 선택한 기존 입력 source를
  `(namespace, source_id, source_generation)`으로 bind하고 source refresh/reconcile
  규약을 더한다. 필요하면 여러 Node로 확장하되 Cell parent는 계속 exact해야 한다.
- **완료 기준:** exact parent/generation/source 증거와 duplicate/orphan/stale binding
  반례 거부. SLM agent profile이나 runtime node의 숫자를 canonical ID로 재사용하지 않는다.

### K3. legacy NodeBit namespace projection

- **작업:** runtime capability registry와 SLM effective policy catalog를 합치지 않고,
  선택한 legacy 항목만 namespace adapter를 통해 K1 canonical NodeBit의 read-only
  source projection으로 연결한다.
- **완료 기준:** canonical NodeBit ID, source namespace/ID/generation, parent Node가
  exact evidence로 일치하며 unknown namespace/orphan/stale projection을 거부한다.

### K4. Cell/Node 관측 귀속

- **작업:** K1~K3 identity가 안정된 뒤 resource/pressure snapshot에 optional canonical
  owner reference를 더한다. validity flag가 없는 값은 미지원이며 기존 aggregate row는
  계속 보존한다.
- **완료 기준:** aggregate 합계와 owner projection 합계가 일치하고 unknown/stale owner는
  `UNATTRIBUTED` 또는 fail-closed 규약대로 처리된다. quota/reserve/migration/apply는 없다.

### K5. principal/ownership + Kernel Room Axis Gate

- **선행 조건:** K1~K4 canonical hierarchy, process/agent principal, target ownership,
  decision generation과 stale-token 거부가 먼저 검증되어야 한다.
- **작업:** 그 뒤에만 `kernel_room_authorize(principal, target, action, generation)`과
  dispatcher/actuator enforcement를 추가한다. 현재 9개 descriptor는 이 단계의 입력
  분류일 뿐 enforcement가 아니다.
- **완료 기준:** 허용/거부뿐 아니라 우회 불가, stale generation, rollback/teardown을
  fail-closed selftest와 `state room` 통계로 증명한다.

### Orbit — `RESEARCH`

Orbit은 Cell/Node placement, 분산 배치, fractal/vector 탐색을 위한 장기 연구축이다.
K1~K4 관리 증거와 bounded simulator 없이는 scheduler 기능이나 지원 topology로
승격하지 않는다.

## 3-B. 실행 substrate 축 (M1~M5) — 유지, 방향 독점 금지

M1~M5는 관리축을 실제 실행·저장·I/O에 연결하는 중요한 기반이다. 독립 안전 조각이나
K축 binding을 직접 여는 작업은 병행할 수 있지만, 아래 잔여 항목이 자동으로 최우선은 아니다.

### M1. uaccess 유저/커널 주소 경계 + SMAP  ✅ 완료 (2026-07-03)
- **왜 지금:** ring3 유저 페이지(64MiB 주소의 고정 영역)가 생겨 "유저 포인터는 유저 영역만"이 처음으로 정의 가능해짐.
- **반영:** `user_access.c`에 유저 주소 윈도우(ring3 실행 중에만 활성 — 커널 내부 uaccess는 윈도우 미설정이라 무영향) + `OUT_OF_WINDOW` 거부. `copy_*_user`와 `user_access_fence_begin/end`(프로그램 스테이징 등 직접 유저 페이지 접근용)에 SMAP 조건부 `stac`/`clac`. `cpu_sec.c`가 SMAP 지원 시 CR4.SMAP 활성화 후 uaccess에 통지.
- **검증 완료:** `[UACCESS] selftest ... window=1`, ring3 데모가 커널 주소로 시도한 시스콜이 거부됨(`[USER] ring3 exec PASS ... boundary_ok=1`), `-cpu max`에서 `[SEC] ... smap=1`로 SMAP 경로까지 통과. 기본 CPU(smap=0)/`-cpu max`(smap=1) + 스모크 3종 + shell 레인 + cppcheck 클린.
- **교훈:** SMAP을 켜자 `user_exec`의 스테이징 memcpy가 즉시 #PF — SMAP이 브래킷 밖 유저 페이지 접근을 실제로 잡는다는 증거. 앞으로 커널이 유저 페이지를 직접 만지는 모든 경로는 fence 필수.

### M2. static ELF64 로더 (Phase 1 핵심)  ✅ 완료 (2026-07-03)
- **반영:** `kernel/core/elf_loader.c` — Elf64_Ehdr/Phdr 검증(magic/class/machine=x86_64/type=EXEC), PT_LOAD 세그먼트를 `p_vaddr`로 복사 + `.bss`(memsz-filesz) 제로화 + image/region 경계 검사. 데모 프로그램을 `user_entry.asm`에 손수 조립한 **유효 ELF64 이미지**(`user_elf_image_start/end`)로 교체 — 별도 링크 단계 없이 make/PS1 동일 동작. `user_exec`가 blob memcpy 대신 `elf_load` 사용, `e_entry`로 ring3 진입.
- **검증 완료:** `[ELF] loaded entry=0x4000078 segments=1 filesz=44 memsz=108`(.bss 제로화 포함), `[USER] ring3 exec PASS ... private_cr3=1 slot=0 cr3_restored=1 if_restored=1 leaf_sealed=1 nx_enforced=1 tensor_excluded=1`. 기본/`-cpu max`(SMAP) + 스모크 3종 + shell 레인 + cppcheck 클린.
- **스코프 진화:** M2의 최초 ELF 왕복은 공유 부트 page table로 시작했고 M3-b-3b2a에서 정적 private CR3 실행, M3-b-3b2b에서 그 CR3·run state·ring0 entry stack을 묶는 bounded bootstrap process 실행으로 교체했다. 2026-07-26에는 두 정적 process를 각자 주소공간에서 순차 실행하는 pair proof까지 확장했다. 여전히 범용/동적 process table이나 동시 유저 태스크를 뜻하지 않는다.
- **잔여:** 후속 per-segment 4KiB 세분화 W^X, 디스크에서 ELF 적재(M5).

### M3. 타이머 선점 + 문맥전환 + 프로세스별 주소공간 (+ 힙 스핀락)
- **M3-a 힙 스핀락 ✅ 완료 (2026-07-04):** `mm/heap.c`의 kmalloc/kfree/get_stats를 `spinlock_irqsave`로 보호(향후 IRQ 컨텍스트 할당 대비 IRQ 마스킹), 락 획득 카운터 + `heap_lock_selftest`(락 유휴·정확히 1회 획득·해제 불변식 검증) + `[HEAP] lock selftest PASS acquires=4` 마커, `state mem`에 `lock_acquires` 노출. 선점의 독립 선행 조각으로 먼저 반영.
- **M3-b-1 협력적 컨텍스트 스위치 ✅ 완료 (2026-07-04):** `sched/kthread_switch.asm`(callee-saved+rsp 저장/복원) + `sched/kthread.c`(초기 스택 프레임 조립, 핑퐁 2스레드 셀프테스트). AI 워크로드 스케줄러(vruntime 장부질)와 분리된 진짜 CPU 컨텍스트 스위치. 각 스레드 스택의 루프변수가 전환을 넘어 보존됨을 검증 → `[SCHED] context switch selftest PASS switches=8 ping=3 pong=3`, `state sched`에 `kthread_switches` 노출. **함정 발견:** 셀프테스트에서 무조건 `sti` 시 PIC 리매핑 이전이라 IRQ0가 벡터 8(#DF)로 진입 → 이전 IF 보존 방식으로 수정(#PF/#DF 봉쇄가 즉시 원인 지목).
- **M3-b-2 타이머 선점 ✅ 완료 (2026-07-14):** 타이머 IRQ 핸들러가 EOI 후 `kthread_preempt_tick`을 호출해 러너블 커널 스레드를 라운드로빈 `kthread_switch`. 자발적 양보를 절대 안 하는 두 워커가 둘 다 진행함을 검증(= 타이머 강제 전환 증명) → `[SCHED] preempt selftest PASS ticks=2 switches=3`, `state sched`에 `preempt_ticks` 노출. **핵심 불변식(설계에 선반영해 #DF 없이 1트 통과):** ① 선점 스위치 전 EOI 전송, ② 갓 스위치된 스레드는 IF=0 상속이라 진입점에서 `sti` 필수(안 하면 선점 불가 무한루프), ③ PIC 리매핑(subsystem 7) 전 `sti` 금지(IRQ0=벡터8=#DF). tick 안전 상한으로 행 방지.
- **M3-b-3a 주소공간 전환 primitive ✅ 완료 (2026-07-14):** 부트 PML4의 정렬된 top-level 복제본을 만들고, IRQ 상태를 보존한 bounded 구간에서 `boot CR3 → clone CR3 → boot CR3` 왕복. 복제 주소공간에서도 실행 중 커널 스택/전역 매핑이 유지되고 원본 CR3로 복귀했음을 `[MM] address space selftest PASS`로 검증하며 `state sched`에 전환 수/준비 상태를 노출. 이 조각은 lower-level 커널 매핑을 공유하며 아직 프로세스 격리를 의미하지 않는다.
- **M3-b-3b1 private user leaf 격리 ✅ 완료 (2026-07-14):** 정적 2슬롯 각각에 2MiB 정렬 backing과 private PML4/PDPT/첫 PD를 두고, 같은 유저 VA(64MiB)의 huge PDE만 서로 다른 물리 backing으로 연결. IRQ-off CR3 교대 중 A/B canary가 교차 오염되지 않고 부트 CR3로 복귀함을 `[MM] user leaf isolation selftest PASS`로 검증하며 `state sched`에 `user_leaf_slots=2 user_leaf_isolated=1` 노출. 범용 주소공간 객체나 PMM 없는 현재 단계의 QEMU-bounded proof다.
- **M3-b-3b2a private CR3 단일 runner ✅ 완료 (2026-07-15):** 기존 ring3 ELF runner가 더 이상 부트 page table의 64MiB identity leaf를 U/RWX로 바꾸지 않고 static slot 0을 guarded activate/restore하여 실행한다. ELF staging·syscall·결과 판독은 private CR3 안에서 끝내고, exact raw CR3와 이전 IF bit의 readback을 확인한 뒤에만 slot 실행 정책을 reset하고 backing을 scrub한다. `leaf_sealed`(software policy reset+scrub)와 `nx_enforced`(hardware NX)는 별도 관측한다. private CR3에서 가려지는 물리 `[64MiB,66MiB)`는 tensor free list와 활성 tensor record에서 제외하며 `[MM] bootstrap user tensor exclusion PASS ... excluded=2097152 managed=1004535808 configured=1006632960 ... boundary=1 coalesce=1`과 `[USER] private address space exec PASS slot=0 cr3_restored=1 if_restored=1 leaf_sealed=1 nx_enforced=1 tensor_excluded=1`로 검증한다. 이는 tensor allocator의 2MiB 제외(설정 960MiB 중 관리 958MiB)이지 전역 PMM 예약이나 물리 메모리 소유권 주장이 아니다. 이 완료 시점에는 아직 process 객체나 PMM이 없었다.
- **M3-b-3b2b static bootstrap process + BSP TSS entry stack ✅ 완료 (2026-07-15):** 정적 descriptor 2개가 각 slot의 unique CR3/backing, process-local run state, unique 16KiB ring0 entry stack을 결속한다. 이 마일스톤 당시 실제 실행은 PID 1/slot 0 한 번이며, IF=0에서 기존 BSP boot-TSS `rsp0`와 다른 process stack top을 exact publish했다. 데모의 `int 0x80` 3회가 모두 `stack_top-40`의 동일 raw entry frame에서 시작했음을 확인하고 `rsp0 → CR3 → leaf seal/scrub → current owner 해제 → caller IF` 순으로 복원한다. 전역 resume/syscall/exit 값은 process run state로 이동했다. C layout은 explicit offset에 static-assert되고 NASM 상수는 이를 mirror하며 실제 boot proof가 양쪽 drift를 잡는다. `[PROC] bootstrap ownership selftest PASS slots=2 owned=2 stack_bytes=16384 unique_cr3=1 unique_backing=1 unique_stack=1`과 `[USER] bootstrap process stack PASS pid=1 slot=0 process_bound=1 kstack_bytes=16384 rsp0_changed=1 rsp0_published=1 int80_entries=3 all_int80_entries_in_stack=1 rsp0_restored=1 kstack_floor_canary=1`로 검증한다. 이는 static bootstrap binding/BSP 단일 TSS 증명이며, 후속 순차 slot 1 실행은 다음 항목에서 별도로 검증한다. 이 자체는 전체 register trapframe, 동적 lifecycle/PMM, guard page, SMP per-CPU TSS를 뜻하지 않는다.
- **M3-b-3b2c 진입 전 순차 pair proof ✅ 완료 (2026-07-26):** 공통 slot runner로 PID 1/slot 0을 마친 뒤 current owner=0, last PID=1, boot CR3/BSP `rsp0` baseline, publish/restore=1을 확인하고서만 PID 2/slot 1을 실제 CPL3에서 실행한다. 두 실행은 같은 유저 VA를 쓰지만 서로 다른 CR3/backing/16KiB entry stack을 사용하며, 각각 `int 0x80` 3회·uaccess hostile pointer 거부·`exit(42)`·leaf seal/scrub·stack canary·CR3/IF/`rsp0` 복원을 검증한다. 최종 증거는 `[USER] bootstrap process pair PASS runs=2 order=1,2 pid_a=1 slot_a=0 pid_b=2 slot_b=1 distinct_pid=1 distinct_slot=1 distinct_cr3=1 distinct_backing=1 distinct_stack=1 int80_a=3 int80_b=3 between_clean=1 current_pid=0 last_pid=2 rsp0_publishes=2 rsp0_restores=2 tss_rsp0_baseline=1 both_restored=1`이다. 이는 순차 synchronous proof이며 trapframe 교대나 타이머 선점 증거가 아니다.
- **M3-b-3b2c 진입 게이트: trapframe C/NASM 계약 + from_user 판별 ✅ 완료 (2026-08-02):** §10 게이트의 첫 두 증거 계약을 고정했다. `kernel/include/interrupt/trapframe.h`가 `interrupt_frame_t` 전 필드 offset과 176B 크기를 static assert하고 `isr_stub.asm`이 byte 상수를 mirror하며, `interrupt_frame_from_user`가 CS RPL로 CPL0/CPL3를 판별한다. CPL0 증명은 canary 15개를 실은 `int3`가 실제 `isr_common_stub`를 통과해 전 GPR offset·exact RIP/RSP·exact frame 주소(`(rsp&~15)-176`)를 맞춘다 → `[TRAP] frame contract selftest PASS size=176 canaries=15 ...`. CPL3 증명은 두 bootstrap process의 데모가 각각 canary `int3`를 발행해 ring3 frame(`cs=0x23 ss=0x1b`, user RSP/RIP)이 그 process의 entry stack `stack_top-176`에 정확히 착지함을 캡처한다 → `[TRAP] user frame capture PASS ... frame_addr_exact=1 contract=1`. `#BP` 게이트는 DPL=3로 승격했고(유일한 생존 예외 + 유저 증거 경로), armed capture가 예상된 breakpoint를 조용히 소비해 verdict의 exception 스캔을 깨지 않는다. 두 마커는 세 프로파일 공통 exact required record이며 `state user`의 `trap_*` 필드와 shell lane 교환으로도 고정된다. 이는 계약·판별·증거이지 아직 trapframe 기반 전환/teardown이 아니다.
- **M3-b-3b2c process-owned trap evidence snapshot v0 ✅ 완료 (2026-08-03):** 기존 ring3 `int3`의 full 176B frame을 ISR 안에서 현재 descriptor에 복사한다. 복사 전에 current owner, 해당 private CR3, BSP TSS `rsp0`, IF=0, exact `stack_top-176`, CPL3 RFLAGS 경계를 함께 검증한다. PID 1→PID 2 실행은 per-boot capture sequence 1,2, descriptor별 distinct storage, 각 finish 뒤 snapshot 보존과 두 번째 실행 뒤 양쪽 descriptor의 최종 재조회, 최종 `current_pid=0`을 `[PROC] trap evidence snapshot PASS schema=1 captures=2 ... seq_a=1 ... seq_b=2 ... distinct_storage=1 current_pid=0 stale_owner=0 resume_ready=0`과 `state user`의 `saved_*` 필드로 증명한다. prepare 코드 경로는 이전 snapshot을 지우고 run generation을 올리지만, 같은 slot의 live reuse/re-prepare와 stale-generation 거부 증거는 아직 `PLANNED`다. 이는 **증거 snapshot만 `CURRENT`**인 좁은 조각이다. 전체 process 모델은 `PARTIAL`이고, continuation/switch/timer preemption은 `PLANNED`다.
- **M3-b-3b2c process event journal v1 ✅ 완료 (2026-08-03):** per-boot capacity 8/no-overwrite 내부 journal에 두 synchronous run의 acquire/capture/release 여섯 record를 append한다. event sequence 1..6과 capture sequence 1,2를 분리하고, exact ordered-vector `[PROC] process event journal PASS ... dropped=0 overflow=0 evidence_only=1 switch_events=0 resume_ready=0`, structured `process_event_journal`, `state user event_*` mirror로 증명한다. owner lifecycle `0→1→0→2→0`은 두 독립 bootstrap run의 관찰 순서이며 CPU switch나 A→B→A 증거가 아니다. numeric schema/kind/reason/outcome은 append-only이고 Python/PowerShell verifier가 누락·중복·축약·확장·역순·stale owner·overflow를 fail-closed로 거부한다.
- **M3-b-3b2c ring3 entry AC hardening ✅ 완료 (2026-08-09):** 실제 QEMU bootstrap pair의 CPL3 `#BP` 공통 entry 2회와 `int 0x80` entry 6회에서 saved user RFLAGS는 보존하고 live AC는 C 진입 전에 항상 0으로 만든다. 첫 int80는 saved AC=0, 이후 각 process의 `int3`/hostile syscall/exit는 AC=1이어서 `common_saved_ac=2 int80_saved_ac=4`다. SMAP active는 `clac`, 비활성·미지원은 `pushfq/btr/popfq` fallback을 사용한다. exact `[SEC] ring3 entry AC hardening PASS schema=1 ... gate_mismatch=0`, `state sec entry_*`, 정규 `default`/`max-smap` CPU profile verifier가 CLAC/fallback 분기를 각각 고정한다. 이 조각만 `CURRENT`이고 future ring3 IRQ/NMI/IST와 실기기 범위는 아직 아니다.
- **잔여 작업(M3-b-3b2c+):** process-owned evidence snapshot과 process event journal까지 완료됐다. 다음에는 full saved-register/trapframe continuation과 scheduler runnable state를 process에 결속한다. 실제 교대의 IF=0 원자 집합에는 current process, CR3, BSP TSS `rsp0`, saved frame뿐 아니라 현재 동기 runner의 단일 active pointer인 `g_active_user_run_state`도 포함한다. bounded 실제 A→B→A 증명 뒤에만 `ai_sched_tick()` 요청과 CPL3 timer IRQ entry-stack 귀속을 연결하고, full register canary, bounded budget, process fault teardown을 검증한다. 장기 단계에서 동적 PMM/VMM, guard page, SMP per-CPU TSS로 일반화한다.
- **착수 게이트:** 남은 live continuation/switch 작업은 `docs/tools/verification_tooling_evolution_design_ko.md` §10의
  rollback/process-transition 증거 계약을 유지한 채 시작한다. journal의 lifecycle evidence나 완료된 bounded `#BP`/int80 entry-AC proof만으로 원자 교대를 증명했다고 간주하지 않는다. future ring3 IRQ/NMI/IST를 추가할 때는 각 진입 경로의 saved/live AC 계약을 별도로 확장 검증한다. 정상 부팅만으로 fault/hang 복구나 원자적 교대를 증명했다고 간주하지 않는다.
- **완료 기준:** ring3 프로세스 2개가 각자 주소공간에서 선점 교대, 힙 동시성 셀프테스트(M3-a로 충족).

### M4. storage read — virtio-blk 최소 데이터 경로
- **작업:** virtio-pci 트랜스포트(modern, 1.2 기준) + virtqueue 1개 + 동기 섹터 읽기. `storage_host`에 `STORAGE_HOST_CONTROLLER_VIRTIO` 분기. testkit에 디스크 이미지 생성 + `-device virtio-blk-pci` 스모크 프로파일(`storage-virtio`) 추가.
- **완료 기준:** 부팅 중 알려진 패턴이 담긴 섹터를 읽어 검증하는 `[STO] read selftest PASS`, `state storage` 토픽(읽기 카운트/latency ns — 고정밀 관측 규약 유지).
- **이후 확장:** NVMe 백엔드(실기기), ai_ring 패턴의 비동기 블록 링.

### M5. 로더-스토리지 연결: 디스크에서 유저 프로그램 적재
- **작업:** 디스크 이미지의 고정 오프셋(파일시스템 이전 단계)에서 ELF를 읽어 M2 로더로 실행. `os/apps/` 첫 실물 앱.
- **완료 기준:** "디스크의 프로그램이 ring3에서 돌고 시스콜로 관측 데이터를 읽는다" — 이 시점에 커널→OS 계층 연결 고리가 완성된다.

### 성숙도 축(실행 경로) 후순위 — 순서 유연
- e1000 일반 RX 경로(연속 수신), 4K 단위 W^X 정밀화, UBSan 디버그 레인, xHCI transfer ring, KASLR, 간단한 파일시스템(또는 tar 아카이브 파싱).

## 3-C. AI 지속성·보안 축 — K축과 M축 뒤에 연결

신원과 정책의 순서가 바뀌었다. canonical Cell/Node identity와 binding은 실행 M5를
기다릴 필요가 없는 **관리 상태**이므로 K1~K3에서 먼저 만든다. 반대로 실제
principal authorization은 허공의 ID나 syscall 번호만으로 만들지 않고 K1~K4와 필요한
process 경계가 준비된 뒤 K5에서 시작한다. 이전의 authorize-first 및 두 NodeBit
source 즉시 단일-table 통합 순서는 이 문서에서 폐기한다.

### C1. 영속 정책/관리 저널 (기존 M8) — `PLANNED`

- **작업:** M4 virtio-blk와 K5 action model 위에 append-only 저널
  (PROPOSED→VALIDATED→PREPARED→APPLIED→VERIFIED→COMMITTED/ROLLED_BACK)을 둔다.
  hierarchy/binding generation도 함께 기록해 재부팅 뒤 stale 대상에 재적용하지 않는다.
- **완료 기준:** 재부팅 경계를 넘어 미완료 action과 hierarchy generation을 찾아
  rollback 또는 안전한 재검증으로 복구하는 셀프테스트.

### C2. AI Flow 지속 실행 컨텍스트 (기존 M9) — `PLANNED`

- **작업:** `process`(주소공간/레지스터/커널스택/capabilities)와
  `ai_flow`(flow_id/canonical node/state/continuation/compute·io queue/memory
  handles/deadline)를 분리한다. 공통 SQ/CQ 비동기 I/O, checkpoint
  begin/attach/commit/restore, speculative branch + commit gate를 사용한다.
- **완료 기준:** 프로세스 재시작 후 동일 flow_id와 canonical Node binding으로
  continuation을 재개하고 stale Cell/Node generation을 거부한다.

## 3-D. 브라우저 콘솔·자체 실행 엔진 축 (W1~W5) — `PLANNED` (2026-07-25)

브라우저 접근과 장기 자체 런타임은 실행 M축을 대체하지 않는 별도 제품 표면이다.
상세 구조, 보안 경계와 완료 조건의 정본은
`docs/os/browser_console_and_runtime_engine_roadmap_ko.md`다.

- **W1 Web Console v0:** 현재 COM1 shell과 `state` 계약을 host WebSocket
  gateway에 연결한다. 커널 변경 없이 독립 착수할 수 있지만 아직 구현되지 않았다.
- **W2 Host Session Runtime:** 사용자별 QEMU 격리, 수명주기, budget,
  provenance와 artifact를 관리한다.
- **W3 Browser-local Engine Pilot:** WebAssembly x86 실행 엔진에서 AIOS ISO를
  부팅하는 선택적 `RESEARCH` 트랙이다. QEMU verdict와 같은 증거를 통과하기
  전에는 지원 플랫폼으로 선언하지 않는다.
- **W4 AIOS Native Runtime Engine:** K1~K5 관리 binding/authorize와 M3 다중
  프로세스, M4 storage, M5 disk ELF, 실제 네트워크 기반 위에서 AIOS 유저스페이스가
  gateway, session, model, flow를 소유한다.
- **W5 Self-hosted Continuity:** C1/C2 저널과 AI Flow를 이용해 재부팅 뒤
  세션·작업을 명시적으로 복구한다.

여기서 "자체 엔진"은 ring0 LLM이나 단기 QEMU 대체 에뮬레이터가 아니다.
모델 실행·웹 서비스·세션 지속성은 유저스페이스에 두고, 커널은 bounded syscall,
SQ/CQ, 자원, authorize, rollback 경계를 제공한다.

## 3-E. Linux-hosted substrate 축 (H0~H5) — 기본 delivery 구현축

세부 기준선과 source/import 경계의 정본은
[Linux-hosted substrate와 upstream resource 정책](../os/linux_hosted_substrate_and_resource_policy_ko.md)이다.
Linux-hosted userspace service는 의도된 기본 delivery 방향이지만 실행 backend의
구현 성숙도는 아직 `PLANNED`다. Linux PID, cgroup, pidfd, PSI, namespace는
canonical Cell/Node/NodeBit가 아니라 `source_only` 입력이다. K1/K2 의미를 Linux
object에 맞춰 바꾸지 않는다.

| 단계 | 상태 | 관계 |
|---|---|---|
| H0 upstream resource manifest/guard | `CURRENT` | 13개 source row와 `code_import=0`을 검증한다. runtime backend 증거가 아니다. |
| H1 OS-neutral trace/replay | `PLANNED` | K2 substrate-neutral contract와 같은 lifecycle·generation·reject reason을 고정하고 native/hosted producer가 함께 소비한다. |
| H2 Linux observe-only adapter | `PLANNED` | K2/H1 공통 계약, negative fixture와 bounded native semantic oracle가 고정된 뒤 userspace service로 source-only 관측을 시작한다. 광범위한 native process/storage 확장은 선행조건이 아니다. |
| H3 binding reconciliation/parity | `PLANNED` | exit/PID reuse/cgroup recreate/host restart를 구분하고 native와 같은 semantic verdict를 요구한다. |
| H4 proposal/validation parity | `PLANNED` | K5 action/principal 계약 뒤에만 validate-only로 열며 초기 capability는 전부 `UNSUPPORTED`다. |
| H5 bounded apply/rollback | `PLANNED` | K5 authorize와 별도 승인 뒤 한 action만 before/after/rollback 증거로 연다. |

### 4~8주 통합 우선순위

1. **SEMANTIC SAFETY 40% — K2/H1:** semantic kind가 맞는 source의 typed
   namespace, producer-owned instance/generation, copied read API와 stable reject
   reason을 먼저 고정한다. 작은 native K2 adapter는 Linux object가 canonical 의미를
   정의하지 못하게 하는 semantic oracle/conformance proof다.
2. **HOSTED DELIVERY 50% — H2/H3:** 공통 contract와 negative fixture가 고정되면
   한 Linux userspace service의 observe-only 수직 조각과
   exit/reuse/recreate/restart/reboot reconciliation을 구현한다.
3. **H0 PROVENANCE + NATIVE CONFORMANCE 10%:** resource guard와 primary artifact
   provenance를 유지하고 native/hosted 동일 verdict를 검증한다. Secondary Linux
   비교는 이 범위 안의 non-blocking `RESEARCH`다.

어느 단계든 종료 증거가 실패하면 다음 단계나 maturity 승격을 중단하고 해당
contract로 되돌아간다. 이번 주기의 hard stop은 H4/H5, resource apply,
quota/throttle, scheduler migration, Axis Gate enforcement다. Memory Fabric domain은
Cell/resource source 후보이고 bootstrap process는 execution-instance source 후보다.
Hosted에서는 실제 userspace service가 producer-owned service instance/generation을
제공해야 하며 PID/cgroup을 `AI_SERVICE` Node 101로 재사용하지 않는다. K1 1024B ABI는
그대로 보존한다.

## 4. 작업 규약 (모든 단계 공통 — 이번 세션에서 확립)

1. **셀프테스트 우선:** 새 경로는 부팅 셀프테스트로 왕복 검증하고 `[XXX] ... PASS` 마커를 남긴다.
2. **스모크 필수화:** shared manifest 도입 전에는 마커를 `tools/testkit/lib/kernel_lane.py`와 `build-windows.ps1` 필수 패턴에 함께 추가한다.
3. **관측 연결:** 런타임 상태는 `state <topic>` 셸 토픽(한 줄 key=value) + 필요 시 시스콜 미러로 노출하고, shell 레인 `DEFAULT_EXCHANGES`에 교환을 등록한다.
4. **고정밀 계측:** 시간이 걸리는 경로는 TSC 모노토닉 ns로 계측해 관측에 포함한다.
5. **정적 분석 클린 유지:** cppcheck exit 0.
6. **검증 세트:** host unit test를 먼저 통과시키고 스모크 3종(full/minimal/storage-only) + shell 레인 + (구조 변경 시) boot-inventory를 실행한다. normal verdict는 전체 로그 fatal, stable health, terminal 순서·중복을 fail-closed로 판정한다.
7. **ABI 불변식:** 시스콜 번호는 추가만, 재번호 금지. Kernel Room 게이트 수 = enum 크기.
8. **관리 계층 불변식:** canonical child는 정확히 하나의 parent와 generation을 가진다. 독립 namespace의 같은 숫자를 binding으로 해석하지 않는다.
9. **관리와 집행 분리:** K1~K4는 `management_only`/`observation_only`다. principal/ownership와 stale-token 증거 전에는 scheduler, quota, dispatcher, actuator에 apply edge를 만들지 않는다.
10. **Orbit 상태:** bounded 연구 artifact와 K축 연결 증거 전에는 항상 `RESEARCH`로 표기한다.

## Sources
- [Implementing a virtio-blk driver in my own operating system — Stephen Brennan](https://brennan.io/2020/03/22/sos-block-device/)
- [How to emulate block devices with QEMU — Oracle Linux Blog](https://blogs.oracle.com/linux/how-to-emulate-block-devices-with-qemu)
- [Virtual I/O Device (VIRTIO) Version 1.4 CS01 — OASIS](https://docs.oasis-open.org/virtio/virtio/v1.4/cs01/virtio-v1.4-cs01.html)
- [Virtual I/O Device (VIRTIO) Version 1.2 CS01 — OASIS](https://docs.oasis-open.org/virtio/virtio/v1.2/cs01/virtio-v1.2-cs01.html)
- [What is io_uring? — Lord of the io_uring](https://unixism.net/loti/what_is_io_uring.html)
- [Why you should use io_uring for network I/O — Red Hat Developer](https://developers.redhat.com/articles/2023/04/12/why-you-should-use-iouring-network-io)
