# 커널 하드닝 베이스라인 심층 점검 및 적용 보고서 (2026-07-02)

**대상:** `kernel/` 도메인 (v0.2.0-beta.6 "Genesis")
**방법:** 코드 심층 리뷰 + 최신 커널 하드닝 표준(Linux/OpenBSD 계열 프로덕션 커널의 기본 완화 기법) 대비 갭 분석 → 즉시 적용 가능한 항목 구현 → QEMU 스모크 3개 프로파일(full/minimal/storage-only) 검증 완료.

---

## 1. 점검 결과: 최신 기준 대비 갭

| # | 항목 | 최신 커널 표준 | 점검 당시 상태 | 조치 |
|---|---|---|---|---|
| 1 | 스택 카나리 | `-fstack-protector-strong`은 모든 주요 커널의 기본값 | `-fno-stack-protector`로 명시적 비활성 | ✅ 적용 |
| 2 | NX / W^X | 데이터 페이지 실행 금지(EFER.NXE + PTE NX)는 20년 된 표준 | EFER.NXE 미설정, 전체 4GB identity map이 RWX | ✅ 적용 |
| 3 | SMEP/UMIP | 커널의 유저 페이지 실행 금지, CPL3 디스크립터 노출 차단 | CR4 비트 미설정 | ✅ 적용 |
| 4 | 예외 진단/봉쇄 | #PF는 CR2 필수 덤프, 미처리 예외는 oops/panic, #DF는 전용 IST 스택 | CR2 미출력, 예외 28종이 폴트 루프 가능, #DF가 손상된 스택 위에서 실행 | ✅ 적용 |
| 5 | 정적 분석 CI | cppcheck/clang-tidy CI 게이트 (4월 보고서 권고 미반영분) | 없음 | ✅ 적용 |
| 6 | SMAP | 커널의 유저 페이지 데이터 접근 차단 | 미적용 | ⏳ 보류 (아래 로드맵) |
| 7 | uaccess 주소 경계 | 유저 포인터의 커널 범위 침범 거부 | `access_ok`가 범위 오버플로만 검사 | ⏳ 보류 |
| 8 | KASLR / 4K W^X / UBSan | 상위 하드닝 | 미적용 | ⏳ 로드맵 |

## 2. 적용 내역

### 2.1 스택 스매싱 보호 (`kernel/core/stack_guard.c` 신설)
- CFLAGS: `-fstack-protector-strong -mstack-protector-guard=global` (TLS 없는 프리스탠딩 환경이므로 전역 가드 심볼 강제 — CI의 linux-gnu gcc에서도 동일 동작 보장).
- `__stack_chk_guard`는 부팅 초기에 TSC 시드로 재무장. Linux 관례대로 최하위 바이트를 NUL로 유지해 문자열 오버플로가 카나리를 복제하지 못하게 함.
- 재시드는 절대 리턴하지 않는 `kernel_main`에서만 호출 (살아있는 계측 프레임 파손 방지, `no_stack_protector` 속성으로 자체 계측 제외).
- 위반 시 `__stack_chk_fail` → `kernel_panic`.

### 2.2 NX / W^X (boot.asm)
- CPUID 0x80000001 EDX[20]으로 NX 지원 확인 → EFER.NXE(bit 11) 활성화.
- 2MB 페이지 단위로 커널 `.text`를 벗어나는 모든 identity map 페이지에 NX(bit 63) 마킹. 스택·힙·텐서 풀·BSS·MMIO 전 영역이 실행 불가가 됨.
- 잔여 한계: `.text`와 저메모리(BIOS/VGA)가 같은 2MB 페이지를 공유하므로 그 한 페이지는 W+X로 남음. 4K 리매핑 시 해소 (로드맵).

### 2.3 SMEP / UMIP (`kernel/core/cpu_sec.c` 신설)
- CPUID leaf 7 기반 감지 후 CR4.SMEP(20), CR4.UMIP(11) 활성화. 부트 로그 `[SEC] nx=... smep=... umip=...`로 상태 보고.
- QEMU 기본 CPU 모델(qemu64)은 SMEP/UMIP CPUID를 노출하지 않아 스모크에서는 `smep=0`이 정상. 실기기/`-cpu max`에서 활성화됨.
- SMAP은 감지·보고만 하고 비활성 유지 — `copy_*_user`에 stac/clac 브래킷이 먼저 필요.

### 2.4 예외 처리 강화 (idt.c, boot.asm)
- #PF에서 CR2(폴트 주소)를 VGA+시리얼에 덤프.
- 복구 경로(시그널/픽스업 테이블)가 없는 현재 구조에서 미처리 예외 복귀는 동일 명령 재실행 → 무한 폴트 루프였음. #BP(브레이크포인트)만 로그 후 재개, 나머지 31종은 전부 `kernel_panic`으로 봉쇄.
- #DF를 TSS IST1의 전용 16KB 스택에서 실행 — 커널 스택 손상이 트리플 폴트(무증상 리셋)로 번지는 것을 차단.

### 2.5 정적 분석 CI (`.github/workflows/linux-boot-check.yml`)
- `static-analysis` 잡 추가: cppcheck `--enable=warning,performance,portability --error-exitcode=1`.
- 사전 로컬 실행으로 기존 지적 3건 수정:
  - `hal/accel_hal.c` — `(1 << 31)` 부호 있는 시프트 UB → `(1U << 31)`.
  - `runtime/ai_syscall.c` 2건 — 셀프테스트가 미초기화 지역 구조체를 출력 버퍼로 전달 → `{0}` 초기화.
- 로컬 재현 명령: `cppcheck --std=c11 --platform=unix64 --enable=warning,performance,portability --inline-suppr --suppress=missingIncludeSystem --error-exitcode=1 -Ikernel/include kernel/`

## 3. 검증

- Windows 크로스 빌드(`build-windows.ps1 -Target test`) + QEMU 스모크 **full / minimal / storage-only 3개 프로파일 전부 PASS**.
- 부트 로그 확인: `[SEC] Stack canary armed`, `[SEC] nx=1 ...`, 기존 체크포인트(`[TIMER]`, `[SELFTEST]`, `[UACCESS]`, `[USER]`, `[ROOM]`, `[HEALTH] stability=stable ok=19`) 전부 유지.
- cppcheck exit 0 (클린).
- 커널 크기 146,800 → 150,896 bytes (+2.8%, 카나리 계측 비용).

## 4. 잔여 로드맵 (우선순위순)

1. **SMAP + stac/clac** — `copy_from_user`/`copy_to_user`/`copy_string_from_user`를 `stac`/`clac`으로 감싼 뒤 CR4.SMAP 활성화. 실제 유저 페이지(U/S=1)가 생기는 시점과 함께 도입.
2. **uaccess 주소 경계 분리** — 유저 가상주소 윈도우를 정의하고 `access_ok`가 커널 이미지/커널 힙 범위와의 겹침을 거부하도록 확장. 유저스페이스 ELF 로더(갭 리포트 Phase 1)와 동시 진행이 자연스러움.
3. **4K 페이지 W^X 정밀화** — 커널 이미지 구간을 4K로 리매핑해 `.text`=RX, `.rodata`=RO, `.data/.bss`=RW-NX 분리. 페이지 폴트 핸들러의 CR2 덤프가 디버깅 기반이 됨.
4. **UBSan 디버그 레인** — `-fsanitize=undefined` + 프리스탠딩 최소 런타임 핸들러를 별도 빌드 프로파일(`make UBSAN=1`)로 추가, boot-matrix에 디버그 레인 편성.
5. **힙 스핀락** — `mm/heap.c`는 아직 락 없음(주석으로 명시됨). 커널 스레드/선점 도입 전 `spinlock_irqsave` 적용.
6. **KASLR** — 유저스페이스 실행 ABI 안정화 이후 검토.

## References
- 이전 점검: [inspection_report_2026_04_15.md](inspection_report_2026_04_15.md)
- 커널 갭 리포트: [current_kernel_gap_report_ko.md](current_kernel_gap_report_ko.md)
