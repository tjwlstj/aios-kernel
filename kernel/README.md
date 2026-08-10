# kernel/ — 베어메탈 커널 도메인

AIOS의 x86_64 베어메탈 커널. 클럭·메모리 보호·인터럽트·디바이스 중재·AI 시스콜 표면을 담당한다.
외부(유저스페이스/모델/스토어)와의 유일한 접점은 **AI 시스콜 ABI**다. (도메인 경계는 [../PROJECT.md](../PROJECT.md) 참고)

## 빌드

빌드 시스템(`kernel/Makefile`)은 이 디렉토리 기준으로 동작한다.
보통은 저장소 루트의 위임 `Makefile`을 쓴다.

```bash
# 저장소 루트에서
make all        # → kernel/build/aios-kernel.bin
make iso        # → kernel/build/aios-kernel.iso
make test       # 빌드 + QEMU 스모크

# 또는 이 디렉토리에서 직접
make -C kernel all
```

산출물은 모두 `kernel/build/`에 생성된다(.gitignore 처리됨).
새 .c 파일은 `kernel/Makefile`의 `C_SOURCES`에 등록해야 빌드에 포함된다.

## 서브디렉토리

| 경로 | 내용 |
|---|---|
| `boot/` | Multiboot2 엔트리, GDT, 페이징, SSE/AVX, long mode (`boot.asm`) |
| `core/` | `main.c`, health, acpi, time, shell, `kernel_room.c`, `kernel_room_management.c`, user_mode/user_access, selftest, `linker.ld` |
| `interrupt/` | IDT + ISR 스텁 (32 예외 + legacy PIC IRQ0 PIT timer) |
| `mm/` | `tensor_mm.c`(64B 정렬 텐서 할당), `memory_fabric.c`, `heap.c` |
| `sched/` | `ai_sched.c` — MLFQ + CFS, 256 태스크 슬롯 |
| `hal/` | `accel_hal.c` — PCI 열거 + 가속기 추상화 |
| `runtime/` | `ai_syscall.c`(디스패처), `autonomy.c`, `slm_orchestrator.c`, `nodebit.c` |
| `drivers/` | VGA, serial, PS/2 keyboard, PCI core, e1000, xHCI, storage, driver model |
| `lib/` | freestanding 문자열 유틸 |
| `include/` | 공개 헤더 (서브시스템별 정리) |

## 불변식 (절대 깨지면 안 됨)

- 텐서 할당은 **64바이트 정렬** (AVX-512). `mm/tensor_mm.c`.
- `CURRENT` K1 Kernel Room hierarchy snapshot은 schema 1/1024B, capacity Cell 2·Node 4·NodeBit 8이며 `observation_only=1 management_only=1`을 유지한다. `core/kernel_room_management.c`.
- Kernel Room 게이트 수 = 게이트 enum 크기. `core/kernel_room.c`.
- AI 시스콜 번호 범위는 ABI-stable — 재번호/중첩 금지.
- 헬스 스냅샷 ABI 안정 (SLM 오케스트레이터가 소비).
- C 소스는 `-mno-sse -mno-mmx -mno-red-zone -mcmodel=kernel -fno-pic -fno-pie`로 컴파일.
  SIMD는 CPU init 이후 커널이 수동으로 켠다 — `-msse`를 추가하지 말 것.

## 커널 내부 설계 문서

`docs/kernel/`, `docs/kernel-room/` 참고. 색인은 [../docs/README.md](../docs/README.md).
