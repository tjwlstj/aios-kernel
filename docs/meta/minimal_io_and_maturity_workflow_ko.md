# 최소 I/O 점검과 성숙도 우선 작업흐름 가이드 (2026-07-03)

**결정 배경:** ring3 첫 실행 슬라이스가 반영된 시점에서 "하드웨어 드라이버 확장 vs 기술 성숙도·정밀화" 중 **성숙도 우선**으로 결정했다. 이 문서는 그 결정의 근거가 되는 최소 I/O 실태 점검 결과, 2026-07 시점의 최신 기술 조사, 그리고 이후 작업흐름을 단계별 가이드로 고정한다.

---

## 1. 최소 I/O 실태 점검 (2026-07-03 기준)

| 서브시스템 | 있는 것 | 없는 것 | 판정 |
|---|---|---|---|
| **storage_host** (`kernel/drivers/storage_host.c`) | PCI 열거·분류(IDE/SCSI/AHCI/NVMe), BAR 매핑, PCI enable, IDE 채널 상태 프로브 | **데이터 경로 전부** — IDENTIFY 없음, 섹터 읽기 없음, DMA 없음 | 부트스트랩 only. **최소 I/O의 최대 공백** |
| **e1000** (`kernel/drivers/e1000.c`) | RX 링(8 descriptor) + 단일 프레임 폴(`e1000_driver_rx_poll`), TX smoke, MAC/EEPROM 읽기 | 연속 수신 경로, 인터럽트 구동 RX, 버퍼 수명 관리 | **유일하게 실제 데이터 경로 보유**. 최소 수준 |
| **usb_host (xHCI)** | 컨트롤러 선택, caps 레지스터 읽기 | transfer ring, 디바이스 열거 | 부트스트랩 only |
| **ai_ring** (`kernel/include/runtime/ai_ring.h`) | SQ/CQ 공유 링 등록·notify·wait 시스콜 표면 | 실제 소비자(모델 런타임) | **구조는 이미 io_uring 동형** — §3 참조 |

**시사점:** "부팅 후 디스크에서 무언가를 읽는다"가 현재 커널이 못 하는 가장 기초적인 I/O다. 이것이 ELF 로더(디스크에서 유저 프로그램 적재)와 store/ 카탈로그(온라인 다운로드 후 저장) 비전의 공통 전제 조건이므로, 드라이버 작업의 올바른 재진입점은 **storage read 단 하나**다.

## 2. 최신 기술 조사 요약 (2026-07)

### 2.1 스토리지 데이터 경로: virtio-blk 우선 (권장)

QEMU 우선 개발 커널의 2026년 컨센서스는 레거시 IDE PIO가 아니라 **virtio-blk**다.

- virtio는 하이퍼바이저-게스트 간 표준 반가상화 인터페이스로, 실제 하드웨어의 quirk 에뮬레이션 없이 단순한 virtqueue(descriptor ring)로 블록 I/O를 처리한다. 취미/연구 OS에서 가장 구현이 단순한 블록 경로로 통용된다.
- 스펙 상태: OASIS VIRTIO **1.2가 최종 Committee Specification**, **1.3은 2023-10 공개 draft** 라인. 구현 기준은 1.2로 잡으면 안전하다.
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

각 단계는 이번에 확립한 작업 규약(§4)을 따른다. 순서는 의존 관계로 고정되어 있다.

### M1. uaccess 유저/커널 주소 경계 + SMAP  ✅ 완료 (2026-07-03)
- **왜 지금:** ring3 유저 페이지(64MB 고정 영역)가 생겨 "유저 포인터는 유저 영역만"이 처음으로 정의 가능해짐.
- **반영:** `user_access.c`에 유저 주소 윈도우(ring3 실행 중에만 활성 — 커널 내부 uaccess는 윈도우 미설정이라 무영향) + `OUT_OF_WINDOW` 거부. `copy_*_user`와 `user_access_fence_begin/end`(프로그램 스테이징 등 직접 유저 페이지 접근용)에 SMAP 조건부 `stac`/`clac`. `cpu_sec.c`가 SMAP 지원 시 CR4.SMAP 활성화 후 uaccess에 통지.
- **검증 완료:** `[UACCESS] selftest ... window=1`, ring3 데모가 커널 주소로 시도한 시스콜이 거부됨(`[USER] ring3 exec PASS ... boundary_ok=1`), `-cpu max`에서 `[SEC] ... smap=1`로 SMAP 경로까지 통과. 기본 CPU(smap=0)/`-cpu max`(smap=1) + 스모크 3종 + shell 레인 + cppcheck 클린.
- **교훈:** SMAP을 켜자 `user_exec`의 스테이징 memcpy가 즉시 #PF — SMAP이 브래킷 밖 유저 페이지 접근을 실제로 잡는다는 증거. 앞으로 커널이 유저 페이지를 직접 만지는 모든 경로는 fence 필수.

### M2. static ELF64 로더 (Phase 1 핵심)  ✅ 완료 (2026-07-03)
- **반영:** `kernel/core/elf_loader.c` — Elf64_Ehdr/Phdr 검증(magic/class/machine=x86_64/type=EXEC), PT_LOAD 세그먼트를 `p_vaddr`로 복사 + `.bss`(memsz-filesz) 제로화 + image/region 경계 검사. 데모 프로그램을 `user_entry.asm`에 손수 조립한 **유효 ELF64 이미지**(`user_elf_image_start/end`)로 교체 — 별도 링크 단계 없이 make/PS1 동일 동작. `user_exec`가 blob memcpy 대신 `elf_load` 사용, `e_entry`로 ring3 진입.
- **검증 완료:** `[ELF] loaded entry=0x4000078 segments=1 filesz=44 memsz=108`(.bss 제로화 포함), `[USER] ring3 exec PASS elf_entry=... boundary_ok=1 exit_code=42`. 기본/`-cpu max`(SMAP) + 스모크 3종 + shell 레인 + cppcheck 클린.
- **스코프 조정 근거:** "프로세스별 CR3 전용 주소공간 복제"는 동시 유저 태스크가 2개 이상 필요한 **M3(선점)와 병합**한다 — 태스크 하나뿐이면 별도 주소공간이 불필요하고, CR3 스위칭은 문맥전환 로직과 함께 만드는 게 자연스럽다. M2는 세그먼트 기반 ELF 로더 + 유저 매핑(공유 페이지 테이블)까지로 완결.
- **잔여:** per-segment 4K W^X(M6), 디스크에서 ELF 적재(M5).

### M3. 타이머 선점 + 문맥전환 + 프로세스별 주소공간 (+ 힙 스핀락)
- **M3-a 힙 스핀락 ✅ 완료 (2026-07-04):** `mm/heap.c`의 kmalloc/kfree/get_stats를 `spinlock_irqsave`로 보호(향후 IRQ 컨텍스트 할당 대비 IRQ 마스킹), 락 획득 카운터 + `heap_lock_selftest`(락 유휴·정확히 1회 획득·해제 불변식 검증) + `[HEAP] lock selftest PASS acquires=4` 마커, `state mem`에 `lock_acquires` 노출. 선점의 독립 선행 조각으로 먼저 반영.
- **M3-b-1 협력적 컨텍스트 스위치 ✅ 완료 (2026-07-04):** `sched/kthread_switch.asm`(callee-saved+rsp 저장/복원) + `sched/kthread.c`(초기 스택 프레임 조립, 핑퐁 2스레드 셀프테스트). AI 워크로드 스케줄러(vruntime 장부질)와 분리된 진짜 CPU 컨텍스트 스위치. 각 스레드 스택의 루프변수가 전환을 넘어 보존됨을 검증 → `[SCHED] context switch selftest PASS switches=8 ping=3 pong=3`, `state sched`에 `kthread_switches` 노출. **함정 발견:** 셀프테스트에서 무조건 `sti` 시 PIC 리매핑 이전이라 IRQ0가 벡터 8(#DF)로 진입 → 이전 IF 보존 방식으로 수정(#PF/#DF 봉쇄가 즉시 원인 지목).
- **M3-b-2 타이머 선점 ✅ 완료 (2026-07-14):** 타이머 IRQ 핸들러가 EOI 후 `kthread_preempt_tick`을 호출해 러너블 커널 스레드를 라운드로빈 `kthread_switch`. 자발적 양보를 절대 안 하는 두 워커가 둘 다 진행함을 검증(= 타이머 강제 전환 증명) → `[SCHED] preempt selftest PASS ticks=2 switches=3`, `state sched`에 `preempt_ticks` 노출. **핵심 불변식(설계에 선반영해 #DF 없이 1트 통과):** ① 선점 스위치 전 EOI 전송, ② 갓 스위치된 스레드는 IF=0 상속이라 진입점에서 `sti` 필수(안 하면 선점 불가 무한루프), ③ PIC 리매핑(subsystem 7) 전 `sti` 금지(IRQ0=벡터8=#DF). tick 안전 상한으로 행 방지.
- **M3-b-3a 주소공간 전환 primitive ✅ 완료 (2026-07-14):** 부트 PML4의 정렬된 top-level 복제본을 만들고, IRQ 상태를 보존한 bounded 구간에서 `boot CR3 → clone CR3 → boot CR3` 왕복. 복제 주소공간에서도 실행 중 커널 스택/전역 매핑이 유지되고 원본 CR3로 복귀했음을 `[MM] address space selftest PASS`로 검증하며 `state sched`에 전환 수/준비 상태를 노출. 이 조각은 lower-level 커널 매핑을 공유하며 아직 프로세스 격리를 의미하지 않는다.
- **잔여 작업(M3-b-3b):** 프로세스별 유저 page-table leaf와 커널 스택/context 소유 구조, ring3 프로세스 2개 선점 실행, `ai_sched_tick()`을 실제 전환에 연결.
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

## 3-B. AI 지속성·보안 축 (M6~M9) — 외부 평가 반영 (2026-07-14)

`docs/meta/`의 외부 평가(체시)가 우리 실행-경로 로드맵(M1~M5)과 근거리에서 일치함을 확인했고, 우리 가이드가 느슨히 남겨둔 장기 축을 아래로 구체화한다. **핵심 원칙: 신원·정책·영속은 실행 기반(M3-b-2 멀티프로세스 + M5 디스크 실행) 위에서만 의미가 생긴다** — principal_id를 프로세스 이전에 넣는 것은 M1의 SMAP 교훈("진짜 유저 페이지가 있어야 SMAP이 의미")처럼 허공에 짓는 것이다. 그래서 이 축은 M5 이후에 배치한다.

교차검증된 판단(우리 코드/최근 결정과 평가가 독립적으로 일치):
- **Kernel Room은 현재 "계기판/관측실"이지 불가피한 단일 집행점이 아니다** (체크포인트 드리프트 수정 때 CLAUDE.md에 이미 명시). M6에서 진짜 게이트로 승격.
- **두 NodeBit 체계(런타임 `nodebit.c` ↔ SLM `slm_nodebit`)가 독립 발전 중** (SLM 관측 작업 때 "distinct"로 확인, 억지 결합 회피). M7에서 단일 원본으로 통합.

### M6. principal/신원 모델 + Kernel Room 단일 authorize
- **작업:** 프로세스에 `principal_id`/`security_domain` 도입(M3-b-2 프로세스 구조에 필드 추가), `kernel_room_authorize(principal, syscall, target_node, caps, policy_gen)` 공통 게이트 신설, 위험 시스콜(드라이버 재초기화/DMA/MMIO/저장 쓰기/네트워크 송신/모델 변경/정책 변경)이 반드시 이 게이트를 통과하도록 배선. 일반 AI 프로세스는 lookup/evaluate만, 정책 제안은 Policy Broker, commit은 Trusted Init/Guardian로 권한 분리.
- **완료 기준:** 위험 시스콜이 authorize를 우회할 수 없음을 셀프테스트로 증명(`[ROOM] authorize selftest PASS`), `state room`에 authorize 통계.

### M7. NodeBit 정책 원본 통합 (TOCTOU 방지 포함)
- **작업:** 런타임 NodeBit과 SLM NodeBit을 `aios_policy_node`(node_id/parent/owner/caps/observe/apply/risky/required_health/generation/flags) 단일 테이블로 합치고 Kernel Room·SLM·Pipeline은 이를 서로 다른 뷰로 읽게 한다. 결정-실행 사이 정책 generation 확인(decision token + generation)으로 TOCTOU 차단.
- **완료 기준:** 두 체계 판정 불일치 불가, generation 불일치 시 재평가 셀프테스트.

### M8. 영속 정책 저널 (append-only)
- **작업:** M4 virtio-blk 위에 append-only 저널(POLICY_PROPOSED→VALIDATED→ACTION_PREPARED→APPLIED→VERIFIED→COMMITTED/ROLLED_BACK). 재부팅 시 `APPLIED`인데 `COMMITTED` 없는 액션을 찾아 rollback 또는 commit 재개.
- **완료 기준:** 재부팅 경계를 넘어 미완료 액션 복원 셀프테스트.

### M9. AI Flow 지속 실행 컨텍스트
- **작업:** `process`(주소공간/레지스터/커널스택/capabilities)와 `ai_flow`(flow_id/agent_id/state/continuation/compute·io queue/memory handles/deadline)를 분리. 프로세스가 죽어도 flow_id로 동일 AI 작업 재개. 공통 SQ/CQ 비동기 I/O(ai_ring 확장), checkpoint begin/attach/commit/restore, speculative branch + commit gate.
- **완료 기준:** 프로세스 재시작 후 동일 flow_id로 continuation 재개 셀프테스트. (이 단계가 "AI 지속성을 OS 차원에서 보증"의 핵심.)

> 상세 근거와 성숙도 스코어카드(종합 ~4.5/10)는 외부 평가 원문 참조. 이 축은 실행 기반이 선 뒤 착수하되, 규약(§4)은 동일하게 적용한다.

## 4. 작업 규약 (모든 단계 공통 — 이번 세션에서 확립)

1. **셀프테스트 우선:** 새 경로는 부팅 셀프테스트로 왕복 검증하고 `[XXX] ... PASS` 마커를 남긴다.
2. **스모크 필수화:** 마커를 `tools/testkit/lib/kernel_lane.py`와 `build-windows.ps1` 필수 패턴에 추가한다.
3. **관측 연결:** 런타임 상태는 `state <topic>` 셸 토픽(한 줄 key=value) + 필요 시 시스콜 미러로 노출하고, shell 레인 `DEFAULT_EXCHANGES`에 교환을 등록한다.
4. **고정밀 계측:** 시간이 걸리는 경로는 TSC 모노토닉 ns로 계측해 관측에 포함한다.
5. **정적 분석 클린 유지:** cppcheck exit 0.
6. **검증 세트:** 스모크 3종(full/minimal/storage-only) + shell 레인 + (구조 변경 시) boot-inventory.
7. **ABI 불변식:** 시스콜 번호는 추가만, 재번호 금지. Kernel Room 게이트 수 = enum 크기.

## Sources
- [Implementing a virtio-blk driver in my own operating system — Stephen Brennan](https://brennan.io/2020/03/22/sos-block-device/)
- [How to emulate block devices with QEMU — Oracle Linux Blog](https://blogs.oracle.com/linux/how-to-emulate-block-devices-with-qemu)
- [Virtual I/O Device (VIRTIO) Version 1.3 — OASIS](https://docs.oasis-open.org/virtio/virtio/v1.3/virtio-v1.3.html)
- [Virtual I/O Device (VIRTIO) Version 1.2 — OASIS](https://docs.oasis-open.org/virtio/virtio/v1.2/csd01/virtio-v1.2-csd01.html)
- [What is io_uring? — Lord of the io_uring](https://unixism.net/loti/what_is_io_uring.html)
- [Why you should use io_uring for network I/O — Red Hat Developer](https://developers.redhat.com/articles/2023/04/12/why-you-should-use-iouring-network-io)
