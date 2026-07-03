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
- **잔여 작업(M3-b-2):** 타이머 IRQ 구동 선점(IRQ 컨텍스트에서 kthread_switch 호출), **프로세스별 PML4 복제 + CR3 스위칭**(M2에서 이월 — 동시 유저 태스크가 생기는 이 시점에 필요), `ai_sched_tick()`을 실제 전환에 연결.
- **완료 기준:** 선점으로 태스크가 자동 교대되는 마커, 각 태스크 별도 주소공간, 힙 동시성 셀프테스트(M3-a로 충족).

### M4. storage read — virtio-blk 최소 데이터 경로
- **작업:** virtio-pci 트랜스포트(modern, 1.2 기준) + virtqueue 1개 + 동기 섹터 읽기. `storage_host`에 `STORAGE_HOST_CONTROLLER_VIRTIO` 분기. testkit에 디스크 이미지 생성 + `-device virtio-blk-pci` 스모크 프로파일(`storage-virtio`) 추가.
- **완료 기준:** 부팅 중 알려진 패턴이 담긴 섹터를 읽어 검증하는 `[STO] read selftest PASS`, `state storage` 토픽(읽기 카운트/latency ns — 고정밀 관측 규약 유지).
- **이후 확장:** NVMe 백엔드(실기기), ai_ring 패턴의 비동기 블록 링.

### M5. 로더-스토리지 연결: 디스크에서 유저 프로그램 적재
- **작업:** 디스크 이미지의 고정 오프셋(파일시스템 이전 단계)에서 ELF를 읽어 M2 로더로 실행. `os/apps/` 첫 실물 앱.
- **완료 기준:** "디스크의 프로그램이 ring3에서 돌고 시스콜로 관측 데이터를 읽는다" — 이 시점에 커널→OS 계층 연결 고리가 완성된다.

### M6+. 후순위 (순서 유연)
- e1000 일반 RX 경로(연속 수신), 4K 단위 W^X 정밀화, UBSan 디버그 레인, xHCI transfer ring, KASLR, 간단한 파일시스템(또는 tar 아카이브 파싱).

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
