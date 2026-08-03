# AIOS 빌드 참고 프로젝트 최신 조사

> 확인일: 2026-08-03
> 상태: `RESEARCH` — 외부 프로젝트에서 가져올 설계 힌트와 AIOS 적용 순서를
> 정리한 문서다. 외부 프로젝트의 기능을 AIOS의 `CURRENT` 구현으로 간주하지 않는다.
>
> 구현 상태의 정본은 [CLAUDE.md](../../CLAUDE.md),
> [최소 I/O·성숙도 워크플로](minimal_io_and_maturity_workflow_ko.md),
> [검증 툴링 진화 설계](../tools/verification_tooling_evolution_design_ko.md)다.

## 1. 조사 목적과 판정 원칙

이번 조사는 이름에 `AIOS`가 들어가는 프로젝트만 찾는 작업이 아니다. 현재 AIOS가
다음 실행 조각을 안전하게 만들 때 실제로 참고할 수 있는 공개 프로젝트를 계층별로
나누고, 어느 시점에 무엇만 가져올지 결정하는 작업이다.

- bare-metal/kernel 프로젝트와 host OS 위 agent runtime을 같은 계층으로 비교하지 않는다.
- README의 성능·안전성 주장은 독립 검증 전까지 외부 프로젝트의 주장으로만 취급한다.
- 구조를 참고해도 AIOS의 exact marker, fail-closed verdict, rollback 불변식은 유지한다.
- 큰 프레임워크를 먼저 이식하지 않고 현재 가장 가까운 검증 조각에 필요한 개념만 가져온다.
- 조사 결과는 `RESEARCH`; 코드·테스트·아티팩트가 함께 생기기 전에는 `CURRENT`로 올리지 않는다.

## 2. AIOS의 현재 출발점

조사 시작 기준 체크포인트는 `5b345f3`의 trapframe 계약 조각이었다.
아래 표는 2026-08-03 process-owned trap evidence snapshot v0 반영 후의
현재 경계를 사용한다.

| 상태 | 현재 범위 |
|---|---|
| `CURRENT` | x86_64 부팅, 정적 ELF64, 두 bootstrap process의 private CR3·16KiB ring0 entry stack, PID 1→PID 2 순차 ring3 실행, 176B C/NASM trapframe exact 계약, CPL0/CPL3 `from_user` 실경로 증거 |
| `CURRENT` | ISR 시점 owner/current/private CR3/TSS `rsp0`/IF=0을 검증한 descriptor-owned trap evidence snapshot v0: full 176B frame 복사, per-boot sequence 1,2, finish 뒤 보존과 final pair 경계 양쪽 재조회, `resume_ready=0` |
| `CURRENT` | 커널 kthread의 타이머 선점, strict boot/shell verdict, 세 프로파일 inventory, pressure/resource aggregate 관측 |
| `PARTIAL` | process 모델은 정적 두 슬롯이며 실행은 순차 동기 호출이다. descriptor snapshot은 증거 소유권만 가지며 scheduler runnable state나 재개 가능한 continuation으로 결속되지 않았다. next-prepare reset/run generation은 구현됐지만 live reuse/re-prepare와 stale-generation 거부의 부팅 증거는 없다 |
| `PARTIAL` | pressure/resource는 `observation_only=1`이다. owner attribution, quota, reserve/apply, migration 입력은 없다 |
| `PLANNED` | 두 ring3 process의 타이머 선점 교대, A→B→A 순서 이벤트, process fault teardown, bounded execution budget, 동적 PMM/VMM |
| `PLANNED` | virtio-blk 읽기(M4), 디스크 ELF(M5), principal authorize(M6), 브라우저 콘솔·AIOS native runtime(W1~W5) |

따라서 지금 필요한 외부 참고점은 “완성형 AI agent platform”보다 **saved-state 소유권,
전환 증거, bounded execution, 작은 신뢰 경계**에 가깝다.

## 3. 가까운 커널 작업에 직접 참고할 프로젝트

### 3.1 Theseus OS — task가 saved state를 소유하는 경계

- 공식 자료: [Theseus task management](https://www.theseus-os.com/Theseus/book/subsystems/task.html),
  [GitHub](https://github.com/theseus-os/Theseus)
- 관찰: task 전환은 저장할 레지스터 상태의 소유자와 assembly 전환 경계를 명확히
  나눈다. SIMD/FPU 상태의 포함 여부도 별도 정책 문제로 다룬다.
- AIOS에 가져올 것: 기존 global capture를 곧바로 scheduler continuation으로 부르지 않고,
  각 bootstrap process가 자신의 검증된 trapframe snapshot과 validity를 소유하게 한다.
- 지금 가져오지 않을 것: Theseus의 언어·object model 전체나 범용 동적 task 모델.
  AIOS는 현재 `-mno-sse` 경계를 유지하므로 먼저 GPR trapframe만 다룬다.

### 3.2 seL4 MCS — budget와 fault를 전환의 일부로 다루기

- 공식 자료: [seL4 검증 범위](https://sel4.org/Verification/),
  [MCS tutorial](https://docs.sel4.systems/Tutorials/mcs.html)
- 관찰: MCS는 scheduling context에 시간 budget을 결속하고 timeout/fault를 명시적인
  결과로 만든다. seL4의 검증 설명도 assembly, boot, machine/hardware 같은 가정을 따로 적는다.
- AIOS에 가져올 것: timer-preemptive ring3 전환 완료 조건에 bounded budget과 process
  fault teardown reason을 처음부터 포함하고, timeout을 단순 host hang과 구분한다.
- 지금 가져오지 않을 것: capability/MCS 전체 모델이나 “형식 검증됨”이라는 표현.
  AIOS의 assembly·부트 경로는 자체 실경로 증거로만 주장한다.

### 3.3 Asterinas — 작은 privileged/unsafe frame과 재현 가능한 도구 표면

- 공식 자료: [Asterinas GitHub](https://github.com/asterinas/asterinas)
- 관찰: framekernel 구조는 privileged/unsafe 저수준 경계를 작은 OSTD 영역으로 모으고,
  OSDK를 통해 build/run/test 표면을 일관되게 제공한다.
- AIOS에 가져올 것: interrupt entry, CR3/TSS 교대, scheduler policy를 한 덩어리로
  키우지 않고 entry-frame primitive와 policy 결정을 분리한다. 장기적으로 testkit 명령도
  하나의 manifest/provenance 표면으로 묶는다.
- 지금 가져오지 않을 것: Rust 재작성이나 Linux ABI 호환 범위.

### 3.4 Redox OS — userspace 서비스 분해의 장기 참고선

- 공식 자료: [Redox OS GitHub 조직](https://github.com/redox-os)
- 관찰: 작은 커널과 분리된 userspace 구성요소를 통해 서비스 경계를 만든다.
- AIOS에 가져올 것: M5 이후 `aios-init`, storage/network service, agent runtime을
  ring0에 밀어 넣지 않고 process/principal 경계로 분리하는 방향.
- 지금 가져오지 않을 것: M3-b-3b2c보다 앞선 대규모 userspace 재구성.

## 4. 빌드·실행·배포 경로에 참고할 프로젝트

### 4.1 Unikraft — 작은 기능 프로필과 최소 이미지

- 공식 자료: [Unikraft GitHub](https://github.com/unikraft/unikraft)
- 참고점: 필요한 구성요소만 선택하는 빌드와 작고 빠른 부팅 이미지를 지향한다.
- AIOS 적용 후보: 현재 `full/minimal/storage-only`를 기능 의존성이 명시된 profile
  manifest로 발전시키고, profile별 kernel/ISO hash와 활성 subsystem을 artifact에 남긴다.
- 경계: unikernel의 단일 응용 이미지 모델은 두 process 격리의 대체물이 아니다.

### 4.2 Firecracker — host session snapshot과 provenance

- 공식 자료: [Firecracker snapshot 지원 문서](https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/snapshot-support.md)
- 참고점: snapshot을 만들 때 VM을 멈추고 VM state, memory, 외부 block state의 책임을
  분리한다. full/diff state와 dirty tracking도 명시적으로 취급한다.
- AIOS 적용 후보: W2 Host Session Runtime에서 pause 시점, ISO/kernel hash, QEMU
  설정, memory/snapshot artifact, 외부 disk identity를 하나의 run manifest로 결속한다.
- 경계: host VM snapshot은 AIOS 내부 process saved state나 M9 AI Flow checkpoint가 아니다.

### 4.3 v86 — 현재 x86_64 비지원 기준선

- 공식 자료: [v86 GitHub](https://github.com/copy/v86)
- 확인 결과: 브라우저 안에서 x86 PC를 WebAssembly 기반으로 실행하지만 공식 README가
  **64-bit extensions 미지원**을 명시한다. 따라서 현재 x86_64 AIOS ISO의 실행 후보가 아니다.
- AIOS 적용 후보: W3 capability matrix의 명시적 `UNSUPPORTED: x86_64` 반례와
  브라우저 serial/state/snapshot UX 조사에만 사용한다.
- 이번 조사에서는 AIOS의 x86_64 full-system boot 요구를 충족한다고 공식 자료로
  확인된 브라우저 엔진을 찾지 못했다. W3는 후보 미정의 feasibility gate로 유지한다.
- 경계: 32-bit guest 부팅 화면이나 user-mode x86_64 ELF emulator를 AIOS ISO 부팅
  가능성으로 바꾸어 해석하지 않는다.

## 5. AI runtime 계층에 나중에 참고할 프로젝트

### 5.1 Wasmtime와 WASI — userspace plugin/component 경계

- 공식 자료: [Wasmtime 문서](https://docs.wasmtime.dev/),
  [WASI releases](https://wasi.dev/releases)
- 적용 시점: M5 디스크 ELF, M6 principal authorize, 안정적인 장기 userspace service 뒤.
- 가져올 것: capability가 명시된 component/plugin 실행, host call 경계, runtime 격리.
- 가져오지 않을 것: Wasm runtime을 kernel에 넣거나 W4의 선행 조건을 건너뛰는 것.

### 5.2 agiresearch/AIOS와 Cerebrum — agent runtime/SDK만 참고

- 공식 자료: [agiresearch/AIOS](https://github.com/agiresearch/AIOS),
  [Cerebrum](https://github.com/agiresearch/Cerebrum)
- 관찰: LLM/context/memory/storage/tool/scheduler를 host OS 위 runtime으로 분리하고,
  SDK·manifest를 통해 agent를 구성한다.
- AIOS 적용 후보: W4 이후 `aios-agentd`, `aios-memd`, tool manager, agent manifest.
- 경계: 이 프로젝트의 “kernel”은 agent runtime 추상화다. bare-metal AIOS의 interrupt,
  MM, driver, process 성숙도 비교 자료로 사용하지 않는다.

### 5.3 NVIDIA Dynamo와 LMCache — 분산 inference 관측 힌트

- 공식 자료: [NVIDIA Dynamo GitHub](https://github.com/ai-dynamo),
  [LMCache GitHub](https://github.com/LMCache/LMCache)
- 관찰: inference 요청 라우팅, 분리된 실행 단계, KV cache를 GPU/CPU/storage 계층에
  걸쳐 다루는 구조가 있다.
- AIOS 적용 후보: M9 AI Flow 이후 resource kind, queue depth, cache residency,
  transfer/stall을 구조화된 관측값으로 확장할 때 참고한다.
- 경계: 현재 pressure/resource snapshot을 이 자료만으로 scheduler migration, quota,
  reserve/apply에 연결하지 않는다. `observation_only=1`을 유지한다.

### 5.4 embodiOS — artifact packaging과 초기 shell UX만 비교

- 공식 자료: [embodiOS GitHub](https://github.com/dddimcha/embodiOS)
- 참고점: GGUF model을 boot artifact에 포함하는 흐름, QEMU/USB boot와 초기 AI shell UX.
- 경계: 외부 README의 성능·모델 지원 주장은 독립 검증 전까지 참고 정보다. AIOS는
  모델 실행을 ring0 기본 경로로 만들지 않고 mediated I/O와 userspace runtime 원칙을 유지한다.

## 6. 현재 성숙도에 맞춘 다음 작업 순서

### 6.1 완료된 첫 조각: process-owned trap evidence snapshot v0 (`CURRENT`)

목표는 **선점 구현이 아니라 trap evidence 소유권을 먼저 증명하는 것**이었다.

1. 두 static process descriptor에 각자의 full `interrupt_frame_t` snapshot,
   validity, per-boot capture sequence, run generation, owner PID/slot을 결속했다.
2. 기존 ring3 `int3` ISR 실경로에서 current owner, private CR3, BSP TSS `rsp0`,
   IF=0, exact frame address, CPL3 RFLAGS 경계를 확인한 뒤 full 176B frame을
   해당 descriptor에 복사한다.
3. PID 1→PID 2의 sequence 1,2, distinct snapshot storage, 각 finish 뒤 보존과
   두 번째 실행 뒤 양쪽 descriptor의 최종 재조회,
   최종 `current_pid=0`을 exact
   `[PROC] trap evidence snapshot PASS ... stale_owner=0 resume_ready=0`과
   `state user` `saved_*`로 노출한다.

prepare 코드 경로는 이전 snapshot을 지우고 run generation을 올리지만,
같은 slot의 live reuse/re-prepare와 stale-generation 거부 증거는 후속 검증으로 남아 있다.
4. snapshot 조각은 `CURRENT`지만 전체 process 모델은 `PARTIAL`이다.
   `resume_ready=0`이므로 continuation, context switch, preemption으로 부르지 않는다.

이 조각은 Theseus의 saved-state 소유권 원칙을 작게 적용하면서도 현재 순차 runner와
rollback 구조를 유지한다. 이후 순서는 **evidence snapshot → append-only
transition event → live continuation/switch**로 고정한다.

### 6.2 그 다음: process transition event v1

- append-only numeric schema와 monotonic sequence를 먼저 정의한다.
- 최소 필드: `seq`, `reason`, from/to PID, from/to CR3, from/to BSP `rsp0`,
  current owner, frame validity, outcome.
- Python/PowerShell verifier에 missing, duplicate, truncation, 역순, stale owner,
  aggregate-only 위장 반례를 먼저 추가한다.
- capture 이벤트와 실제 switch 이벤트를 다른 kind/reason으로 구분한다.

### 6.3 이후: bounded cooperative proof → timer preemption

1. IF=0 구간에서 current process, CR3, BSP TSS `rsp0`, saved frame,
   `g_active_user_run_state`를 함께 교대한다.
2. 먼저 bounded A→B→A 순서를 기계 판독 이벤트로 증명한다.
3. 그 다음 `ai_sched_tick()` 요청과 CPL3 timer IRQ entry-stack 귀속을 연결한다.
4. full GPR canary, 동일 VA 격리, syscall 왕복, execution budget, process fault
   teardown과 첫 실패 trace를 완료 조건으로 둔다.
5. 더 풍부한 ring3 IRQ 경로나 ISR 내 유저-page 접근 전에 공통 스텁/
   `int 0x80` entry의 RFLAGS.AC 제거(`clac`) 하드닝을 SMAP feature-gated 경로로 별도 검증한다.

이 단계가 성공한 뒤에만 M3-b-3b2c를 선점 가능한 두 userspace process로
`CURRENT` 승격한다. 이후 기본 실행축은 M4 virtio-blk → M5 disk ELF 순서를 유지한다.

## 7. 검증·릴리스 가이드

다음 커널 조각은 기존 strict lane을 줄이지 않는다.

- 변경 전 marker/event schema와 negative host test를 먼저 고정한다.
- `py -3` host unit tests와 PowerShell verdict selftest를 모두 통과한다.
- Windows kernel build, `git diff --check`, cppcheck를 통과한다.
- QEMU full/minimal/storage-only, interactive shell, strict inventory를 통과한다.
- 기본 CPU와 `-cpu max`에서 trapframe/process 증거와 SMAP 경계를 확인한다.
- beta의 exact SHA가 terminal CI success일 때만 같은 SHA를 main으로 fast-forward한다.
- baseline은 구현을 통과시키기 위한 수단으로 갱신하지 않고, 의도된 계약 변화와
  새 trusted artifact가 함께 있을 때만 별도 검토한다.

## 8. 채택 결론

가장 가까운 세 참고선은 Theseus의 saved-state 소유권, seL4 MCS의 bounded
budget/fault, Asterinas의 작은 privileged frame 경계다. Redox·Unikraft·Firecracker는
각각 userspace 분해, profile build, host session provenance에 유용하지만 현재 trapframe
작업을 대신하지 않는다.

v86는 W3의 x86_64 비지원 기준선이며 실제 browser-local engine 후보는 아직 미정이다.
Wasmtime/WASI와 agiresearch 계열은 W4 이후 userspace runtime, Dynamo/LMCache는
M9 이후 분산 AI flow/resource 관측에 배치한다. 이 순서를 지키면
외부 프로젝트의 규모에 끌려가지 않고 AIOS가 이미 가진 exact evidence와 rollback
중심의 강점을 유지한 채 확장할 수 있다.
