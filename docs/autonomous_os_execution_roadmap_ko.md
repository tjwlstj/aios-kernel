# AI 에이전트 자율 OS 실행 로드맵

갱신일: 2026-04-11

## 1. 현재 고정 목표

현 단계의 초기 설계 목표는 크게 하나다.

- 부팅한 커널이 정상적으로 하드웨어를 탐지하고
- health 기준으로 안정/강등 상태를 설명할 수 있으며
- SLM 오케스트레이터가 최소 하드웨어 계획을 시드하고 실행 가능한 상태로 올라오는 것

즉, 지금의 1차 목표는 `범용 OS 완성`이 아니라
`hardware-aware bootstrap kernel + minimal SLM bring-up`이다.

이 목표가 만족되면 다음 단계에서 user-space runtime, shell, loader, 실제 I/O 경로를 올릴 수 있다.

---

## 2. 현재 구현 상태

### 구현된 것

- Multiboot2 기반 커널 부팅
- VGA + serial 로그 출력
- QEMU 기반 부팅 smoke test
- boot-time memory selftest와 성능 tier 추정
- ACPI RSDP/XSDT/RSDT/MCFG 최소 파싱
- PCI core / platform probe 기반 장치 inventory
- e1000 최소 bootstrap + TX smoke
- USB/xHCI capability probe 수준 bootstrap
- storage host bootstrap 수준 장치 준비
- health registry와 stability gate
- optional subsystem 실패 시 degraded로 계속 부팅하는 정책
- SLM hardware orchestrator 초기화
- SLM 기본 plan seed
  - `inventory-refresh`
  - `core-audit`
  - `io-audit`
  - `ethernet-bootstrap`
  - `ethernet-tx-smoke`
  - `ethernet-rx-poll`
  - `usb-bootstrap`
  - `storage-bootstrap`

### 부분 구현

- AI syscall surface
  - 인터페이스와 일부 dispatcher는 있음
  - 실제 user-space 진입 경로는 아직 없음
- infer ring ABI
  - 등록/notify/status는 있음
  - completion wait와 실제 데이터 평면 처리는 아직 stub
- autonomy control plane
  - bounded scheduler action/rollback 기초는 있음
  - verifier 고도화와 multi-target actuator는 아직 제한적

### 아직 없는 것

- ring3 user-space handoff
- TSS / syscall entry / process address space
- static ELF loader / `aios-init`
- shell / TTY / command execution loop
- timer IRQ 기반 진짜 preemption
- 실제 NIC RX / storage read-write / xHCI transfer ring

---

## 3. 현재 완료 기준

지금 단계에서 "초기 목표 충족"은 다음을 의미한다.

1. 커널이 QEMU에서 안정적으로 부팅한다.
2. ACPI/PCI/driver bootstrap 로그가 serial에서 확인된다.
3. health summary가 `stable` 또는 설명 가능한 `degraded`로 출력된다.
4. SLM orchestrator가 올라오고 최소 plan을 seed한다.
5. `AIOS Kernel Ready`까지 도달한다.

현재 저장소는 이 기준을 QEMU Windows smoke 기준으로 이미 만족한다.

---

## 4. 다음 우선순위

### Priority 1. 부팅 기준선 고정

목표:
- 지금 확보한 부팅/탐지/SLM bring-up 기준을 회귀 없이 유지한다.

필수 작업:
- degraded boot 시나리오를 smoke에 추가
- optional 장치 부재 시 health와 boot banner가 일관되게 출력되는지 검증
- serial log 기준 시그니처를 명문화

완료 기준:
- NIC/USB/storage 일부가 빠져도 커널이 panic 없이 ready까지 감
- health/stability/boot banner가 기대한 상태를 설명

### Priority 2. 장치 bootstrap을 실제 I/O로 확장

목표:
- 현재 "보임 + ready 로그" 수준의 장치를 최소 실사용 경로로 확장한다.

필수 작업:
- e1000 일반 RX 경로
- storage read 경로
- xHCI transfer ring의 최소 bring-up

완료 기준:
- 네트워크 수신, 저장장치 읽기, USB 전송 중 하나 이상이 실제 데이터 경로로 동작

### Priority 3. SLM 실행 조건 강화

목표:
- SLM이 단순 seed 로그를 넘어서 hardware-aware decision loop의 기반이 되게 한다.

필수 작업:
- plan status/verify/log 정리
- hardware snapshot과 health summary 결합 강화
- I/O plan apply 전 verifier 조건 명확화

완료 기준:
- SLM plan이 "제안만 하는 상태"가 아니라 상태 조회와 결과 판정까지 갖춤

### Priority 4. user-space 경계 준비

목표:
- 다음 단계의 `aios-init`와 shell을 올릴 수 있는 최소 커널 경계를 마련한다.

필수 작업:
- ring3 진입
- TSS + kernel/user stack 경계
- static ELF loader
- serial 기반 TTY 초안

완료 기준:
- 첫 user-space 프로그램 하나를 serial 경로로 실행 가능

---

## 5. 단계별 계획

### Phase A. Bootstrap Kernel Baseline

목표:
- 부팅, 하드웨어 탐지, health, SLM seed까지를 안정적인 기준선으로 만든다.

현재 상태:
- 대부분 구현됨

남은 작업:
- degraded boot smoke
- 회귀 시그니처 정리

### Phase B. Device-Useful Kernel

목표:
- bootstrap driver를 실제 데이터 경로가 있는 최소 드라이버로 끌어올린다.

현재 상태:
- e1000 TX smoke / USB caps / storage bootstrap까지 부분 구현

남은 작업:
- e1000 RX
- storage read
- xHCI transfer

### Phase C. Safe SLM Kernel

목표:
- SLM이 hardware-aware plan을 더 안전하게 제안/검증/적용하도록 만든다.

현재 상태:
- snapshot, seed, bounded plan surface는 있음

남은 작업:
- verify/apply 결과 정리
- rollback 기준 보강
- I/O 관련 verifier 확장

### Phase D. Agent Substrate

목표:
- user-space runtime과 shell이 올라올 수 있는 기반을 만든다.

현재 상태:
- syscall/UAPI와 ring ABI만 있음

남은 작업:
- ring3
- ELF loader
- `aios-init`
- serial shell

---

## 6. 당장 실행할 Next 3

1. degraded boot smoke를 추가한다.
- optional device가 빠질 때도 ready까지 가는지 테스트한다.

2. e1000 RX 또는 storage read 중 하나를 실제 데이터 경로로 만든다.
- bootstrap driver를 실사용 driver로 넘기는 첫 단계다.

3. serial 기반 user-space 진입 계획을 코드 기준으로 고정한다.
- 첫 대상은 `aios-init`보다 더 작은 `serial shell bootstrap`이어도 된다.

---

## 7. 하지 말아야 할 것

- user-space가 없는데 상위 AI 런타임 구조를 과장해서 구현 완료처럼 쓰기
- 모든 장치를 동시에 실사용 수준으로 파고들기
- verifier 없이 SLM action surface를 크게 넓히기
- 부팅 기준선이 불안정한데 shell/OCI/WASI부터 올리기

---

## 8. Definition of Done

현 단계 기능이 완료로 간주되려면 최소한 다음이 필요하다.

- 부팅 테스트 통과
- serial/QEMU 로그로 확인 가능
- health/stability 상태가 일관되게 남음
- SLM/driver 관련 결과가 snapshot 또는 log에 반영됨
- failure handling 또는 degraded handling 경로 존재

---

## 9. 결론

지금 AIOS의 첫 기준선은
`정상 부팅 -> 하드웨어 탐지 -> health 판단 -> SLM 최소 실행`
이다.

이 기준선을 흔들지 않는 선에서,
다음 확장은 `실제 I/O 경로`와 `user-space handoff` 순으로 가는 것이 맞다.
