# AIOS User-Space Bootstrap Lane

> 문서 역할: native userspace bootstrap 디렉터리 진입 문서
>
> bounded ring3/static ELF proof: `CURRENT`
>
> 전체 process runtime: `PARTIAL`
>
> 실제 `aios-init`, disk-backed ELF, live process runtime: `PLANNED`

이 디렉토리는 `AIOS`가 커널에서 유저공간 OS로 넘어가기 위한
가장 작은 bootstrap 조각을 설명하는 기준 위치다. 현재 이 디렉터리에는
userspace binary 구현이 없고, 동작 증거는 `kernel/`의 bounded native
reference/proof 경로가 소유한다.

## 범위

- bounded ring3 handoff와 static ELF64 loader 증거
- process별 private CR3/backing과 ring0 entry stack
- `int 0x80`, `exit(42)`, invalid userspace pointer 거부
- 향후 `aios-init`, disk-backed ELF, live process runtime과의 경계

## 현재 상태

### `CURRENT`: bounded native proof

- `kernel/core/user_entry.asm`, `kernel/core/user_exec.c`,
  `kernel/core/elf_loader.c`가 커널 내장 static ELF64 데모를 적재하고
  `iretq`로 CPL3에 진입한다.
- 두 정적 bootstrap process descriptor가 각자 private CR3/backing과 16KiB
  ring0 entry stack을 소유하고 PID 1 다음 PID 2를 순차 실행한다.
- 두 실행은 `int 0x80` 관측 시스콜, invalid pointer 거부, `exit(42)`,
  CR3/IF/TSS `rsp0` 복원을 검증한다.
- process-owned trap evidence snapshot과 process event journal v1은 증거 소유권과
  순서를 검증한다. 둘 다 `evidence_only=1 switch_events=0 resume_ready=0`이며
  재개 가능한 process state나 CPU switch가 아니다.

### `PARTIAL`: native process model

- bounded static descriptor와 순차 synchronous runner는 있지만 일반 process
  생성·수명주기, 동적 주소공간, 재개 가능한 saved context는 없다.
- 실제 A→B→A 전환, 두 ring3 process의 timer preemption과 live continuation은
  아직 증명하지 않았다.

### `PLANNED`: 제품 bootstrap/runtime

- 첫 `aios-init` 바이너리와 장기 실행 PID1
- 디스크·파일시스템에서 읽은 ELF 적재
- 동적 PMM/VMM 기반 process 주소공간과 fault teardown
- runnable-state 결속, live switch와 timer preemption
- userspace service·memory·policy runtime

## 현재 작업 선택과 검증

이 `PLANNED` 목록은 자동으로 프로젝트의 다음 작업 순서가 되지 않는다. 현재
작업은 [통합 작업 가이드](../../../docs/meta/integrated_work_guide_ko.md)와
[성숙도 우선 작업흐름](../../../docs/meta/minimal_io_and_maturity_workflow_ko.md)에서
선정한다. bounded proof의 정확한 증거와 판정 계약은
[검증 툴링 정본](../../../docs/tools/verification_tooling_evolution_design_ko.md)과
[Testkit 가이드](../../../docs/tools/testkit_guide_ko.md)를 따른다.
