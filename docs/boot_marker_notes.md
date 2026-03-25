# Boot Marker Notes

이 문서는 초기 부팅 디버깅을 위해 [boot/boot.asm](../boot/boot.asm)에 추가한 `debugcon` 마커의 의미와,
이번 점검에서 확인된 해석 결과를 기록합니다.

## 출력 포트

- I/O port: `0xE9`
- 수집 방식 예시:

```text
qemu-system-x86_64 ... -debugcon file:build/debugcon.log -global isa-debugcon.iobase=0xe9
```

## 마커 의미

- `A`: `_start` 진입
- `B`: Multiboot2 magic 확인 통과
- `1`: Multiboot1 magic 감지
- `N`: Multiboot2 magic 불일치
- `P`: `check_cpuid()` 통과
- `L`: `check_long_mode()` 통과
- `T`: `setup_page_tables()` 통과
- `C`: `enable_paging()` 통과
- `D`: 64-bit long mode 엔트리 진입
- `S`: long mode 진입 후 세그먼트/스택 재설정 완료
- `V`: VGA 배너 출력 완료
- `Z`: BSS 초기화 이후 지점
- `E`: `enable_sse()` 이후 지점

## 이번 점검에서 확인한 흐름

초기에는 `A` 또는 `ANPLTCDSV`까지만 출력되고 이후 진행되지 않았습니다.

원인:

1. Multiboot2 framebuffer tag가 8바이트 정렬을 지키지 않아 GRUB에서 `unsupported tag: 0x8` 오류 발생
2. long mode 진입 후 `.bss`를 지우면서 page table과 부트 스택을 함께 덮어씀

수정 후:

- Windows QEMU smoke test에서 `AIOS Kernel Ready`까지 도달 확인
- serial log 기준 커널 준비 메시지 정상 출력 확인

## 메모

- 현재 마커는 부팅 경로 점검에 유용하므로 일단 유지합니다.
- 안정화가 끝나면 제거하거나 `DEBUG_BOOT_TRACE` 같은 조건부 방식으로 바꾸는 것을 권장합니다.
