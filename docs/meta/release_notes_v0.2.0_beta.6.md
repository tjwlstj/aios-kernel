# AIOS v0.2.0-beta.6 릴리즈 노트

작성일: 2026-04-25

## 요약

`v0.2.0-beta.6`은 스케줄러 tick을 실제 QEMU 관찰 가능한 PIT IRQ0 경로에 연결하는 베타 릴리즈다.

이번 릴리즈는 `timer IRQ -> ai_sched_tick()`까지의 bootstrap을 제공한다. 실제 task context switch, userspace preemption, APIC/HPET timer source는 아직 후속 작업이다.

## 변경 사항

- `kernel/time.c`
  - PIT channel 0을 100Hz periodic mode로 설정한다.
  - legacy PIC을 IDT vector 32~47로 remap하고 IRQ0만 unmask한다.
  - 첫 timer IRQ 수신을 확인한 뒤 `[TIMER] PIT IRQ ready` 로그를 출력한다.
- `interrupt/isr_stub.asm`, `interrupt/idt.c`
  - legacy PIC IRQ 32~47 스텁을 추가한다.
  - IRQ0에서 `kernel_timer_irq_handler()`를 호출하고 PIC EOI를 보낸다.
- `sched/ai_sched.c`
  - 기존 `ai_sched_tick()` API를 timer IRQ handler에서 호출한다.
- `kernel/main.c`
  - boot banner를 `0.2.0-beta.6 "Genesis"`로 갱신한다.
  - scheduler 초기화 뒤 `Kernel Timer IRQ` bootstrap을 실행한다.
- `testkit`
  - QEMU smoke 필수 패턴에 `[TIMER] PIT IRQ ready`를 추가한다.
  - boot summary checkpoint에 `timer_irq`를 추가한다.
- 문서
  - README와 gap 문서를 현재 구현 상태에 맞춰 갱신했다.
  - timer IRQ accounting은 구현됨, 실제 선점/context switch는 미완성으로 구분했다.

## 검증

다음 명령을 Windows 환경에서 통과했다.

```powershell
python .\testkit\aios-testkit.py kernel --target all --strict
python .\testkit\aios-testkit.py kernel --target test --strict --export-boot-summary --timeout 12
python .\testkit\aios-testkit.py boot-matrix --profiles full minimal storage-only --strict --timeout 12
python .\testkit\aios-testkit.py os
git diff --check
```

QEMU serial log에서 확인한 핵심 checkpoint:

```text
[TIMER] PIT IRQ ready hz=100 vector=32 ticks=1
```

## 남은 작업

- `ai_sched_tick()` 이후 runnable task 선택과 실제 context switch 연결
- APIC/HPET 기반 timer source 선택 경로
- userspace handoff 이후 ring3 preemption
- deadline miss 및 latency 분포 통계 확장
