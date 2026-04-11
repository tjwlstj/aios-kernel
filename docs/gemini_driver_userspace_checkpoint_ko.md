# Gemini CLI 드라이버/유저스페이스 추가 점검과 다음 실행 조각

작성일: 2026-04-12

## 목적

이 문서는 `Gemini CLI`를 읽기 전용 보조 검토자로 사용해, 현재 `AIOS`의

- 드라이버 bring-up 상태
- `boot -> userspace` 전이 가능성

을 다시 점검하고, 그 결과를 바로 다음 구현 조각으로 연결하기 위해 정리한 기록이다.

이번 메모는 새 아이디어를 과장하기보다, 현재 저장소 코드에 실제로 보이는 구현 범위와
그 위에서 안전하게 다음 단계를 정하는 데 초점을 둔다.

## 이번에 확인한 코드 범위

- `kernel/main.c`
- `drivers/pci_core.c`
- `drivers/platform_probe.c`
- `drivers/e1000.c`
- `drivers/storage_host.c`
- `drivers/usb_host.c`
- `runtime/ai_syscall.c`
- `runtime/autonomy.c`
- `runtime/slm_orchestrator.c`

## 코드에서 직접 확인한 현재 상태

### 1. 부팅과 초기화 순서는 이미 제법 정리돼 있다

`kernel/main.c`는 다음 순서로 커널을 올린다.

1. 콘솔 / 시리얼 / health 초기화
2. IDT
3. time source
4. ACPI
5. PCI core
6. Tensor MM
7. AI scheduler
8. selftest / profile
9. accelerator HAL
10. platform probe
11. memory fabric
12. e1000 / USB / storage bootstrap
13. AI syscall / autonomy / SLM orchestrator

즉, "부팅만 되는 데모"를 넘어서 부팅 후 점검과 장치 스냅샷까지는 이미 올라와 있다.

### 2. 드라이버는 bootstrap 중심으로 잘려 있다

현재 드라이버 쪽은 역할이 비교적 선명하다.

- `pci_core.c`
  - ECAM / legacy dual path
  - BAR, capability, command register 접근
- `platform_probe.c`
  - PCI class 기반 장치 분류
  - boot perf tier에 따른 초기화 priority 산출
- `e1000.c`
  - 장치 식별
  - BAR / MMIO / I/O 접근
  - EEPROM / MAC 읽기
  - RX/TX ring 준비
  - bounded TX smoke / RX poll 경로
- `storage_host.c`
  - storage host 식별
  - IDE/AHCI/NVMe/기타 분류
  - BAR 확보
  - IDE channel live/status 확인
- `usb_host.c`
  - USB host 식별
  - UHCI/OHCI/EHCI/XHCI 분류
  - BAR 확보
  - xHCI capability 읽기

여기서 중요한 현실 체크는 다음이다.

- `e1000`은 "초기 TX smoke가 있는 최소 NIC bootstrap"까지 와 있다
- `storage`는 "host / IDE channel 상태 확인"까지다
- `usb`는 "host capability probe"까지다

즉, 네트워크/저장장치/USB 모두 "호스트 bring-up"은 시작됐지만, 아직 일반 data path나
실제 장치 서비스 계층이라고 부를 단계는 아니다.

### 3. userspace 전이는 아직 시작 전 단계다

`runtime/ai_syscall.c`에는 AI 전용 syscall 디스패처와 ring 상태 통계가 있고,
`runtime/autonomy.c`, `runtime/slm_orchestrator.c`는 커널 내부 control plane과
boot-time 계획 추천을 이미 제공한다.

하지만 현재 저장소에는 아직 다음이 없다.

- ring3 handoff
- user-mode execution context
- TSS 기반 stack switch
- static ELF loader
- `init` 프로세스를 띄우는 경로

그리고 `kernel_main()`은 부팅 완료 후 idle `hlt` loop로 들어간다.
즉 지금의 `ai_syscall`은 "유저 공간에서 실제로 호출되는 ABI"라기보다,
커널이 먼저 준비해둔 syscall surface에 더 가깝다.

또한 inference ring API는 부분 구현 상태다.

- `setup`
- `notify`
- `status`

는 있으나, `wait_cq`는 로그상으로도 아직 pending implementation 성격이 강하다.

## Gemini CLI 추가 점검 요약

### A. 드라이버 / bring-up 관점

Gemini는 현재 드라이버 축을 다음처럼 봤다.

- 강점
  - boot/health/pci/probe 흐름이 비교적 일관됨
  - `e1000`은 smoke 가능한 최소 bring-up 단계까지 진전
  - `platform_probe`가 장치 우선순위를 정해 SLM/driver plan과 이어지기 좋음
- 위험
  - polling과 spin 기반 경로가 많아 hang/지연에 취약
  - storage / USB는 여전히 host bootstrap 수준
  - interrupt 기반 completion이 아직 없다

이 평가는 현재 코드와 크게 어긋나지 않는다.

### B. boot-to-userspace 관점

Gemini는 이 축의 핵심 blocker를 다음처럼 정리했다.

- `kernel_main()`이 idle loop로 끝남
- ring3 진입 뼈대가 없음
- 프로세스/주소공간/ELF loader 부재
- `musl`이나 POSIX-lite를 얹을 최소 UAPI가 아직 없다

즉, 지금 AIOS는 "커널 내부 control plane이 있는 부팅 가능한 커널"이지,
"유저 공간 OS가 올라오는 운영체제"는 아직 아니다.

## 병행 추진안

이번 점검 결과를 기준으로 하면, 다음 단계는 한 축으로 몰기보다 두 축을 병행하는 편이 맞다.

### 1. 테스트/검증 축: `boot-fault` MVP

목적:

- "장치가 아예 없음"이 아니라
- "장치가 보였지만 초기화가 실패하거나 degraded가 됨"

을 QEMU + testkit 기준으로 재현하는 것

가장 작은 현실적 형태:

1. test-only fault knob를 도입한다.
2. 대상은 우선 `e1000`, `usb_host`, `storage_host` 세 축으로 한정한다.
3. production 경로와 섞이지 않게 compile-time 또는 test-only define으로 감싼다.
4. 실패 지점은 "probe 후 ready 전"의 좁은 지점만 강제한다.
5. serial log에 `[FAULT]` marker와 기존 `[HEALTH]` summary를 함께 남긴다.

첫 번째 대상 fault 예시:

- `e1000`: BAR 확인 후 forced init failure
- `usb_host`: capability probe 직전 forced invalid capability
- `storage_host`: IDE channel probe 이후 forced degraded

검증 경로:

1. `kernel --target test --strict --smoke-profile full`
2. `boot-summary`에 `controller_states`와 `health_summary` 반영 확인
3. `boot-inventory`에서 baseline과 비교해 expected degraded가 잡히는지 확인

이 축의 의미:

- 드라이버가 "준비됨/부재"만 아니라 "고장/실패"도 다룰 수 있는지 확인하게 해준다
- 나중에 실제 userspace supervisor가 올라왔을 때 fault handling 시나리오를 더 정직하게 만들 수 있다

### 2. 커널/OS 축: `boot -> ring3` 최소 전이 체크리스트

가장 작은 userspace 전이는 거대한 POSIX 호환층보다 훨씬 작게 잡아야 한다.

#### Phase 0. 현재 커널-only 경로 유지

- 지금의 boot summary / matrix / inventory / perf를 계속 green으로 유지
- ring3 작업 중에도 기존 부팅 회귀를 바로 볼 수 있게 한다

#### Phase 1. ring3 진입 scaffold

- user code/data segment를 포함한 GDT 확장
- TSS 준비
- user stack 확보
- 정적 `init` 바이너리 또는 flat stub로 `IRETQ` handoff

검증:

- serial에 `ring3-enter` marker 출력
- user stub에서 trap/syscall로 커널에 다시 진입

#### Phase 2. 최소 user execution context

- task/process 구조체에 user RIP/RSP 저장
- kernel/user stack 분리
- page permission 최소 분리

검증:

- user task fault 시 kernel이 panic 대신 fault report를 남김

#### Phase 3. static ELF loader

- ELF64 header / program header 파싱
- text/data/bss 매핑
- entry point jump

검증:

- 정적 링크된 `aios-init`를 로드하고 첫 userspace log를 남김

#### Phase 4. POSIX-lite / AIOS UAPI 연결

- `write`
- `clock`
- `mmap-lite`
- `wait`
- AI ring submit/wait

검증:

- `aios-init`가 health snapshot과 SLM snapshot을 읽고 정책 데몬을 기동

이 축의 의미:

- 메인 AI와 하위 노드를 실제로 유저 공간으로 올릴 기반을 만든다
- 현재의 `autonomy` / `SLM` / `ai_syscall`을 진짜 OS 경계로 확장한다

## 지금 가장 추천하는 순서

둘을 병행하되, 실제 구현 순서는 다음이 가장 안전하다.

1. `boot-fault` MVP 설계와 test-only hook 범위 확정
2. 동시에 ring3 scaffold 설계 문서와 ABI 체크리스트 확정
3. 그 다음 실제 코드는 `ring3 stub`부터 시작
4. `boot-fault`는 ring3 작업 중 회귀 감시용으로 붙인다

즉, "testkit을 더 정직하게 만들기"와 "userspace 진입점을 열기"를 같이 가되,
실제 코드 첫 조각은 `ring3 stub`가 더 중요하다.

## 한 줄 결론

이번 추가 점검 기준으로, 현재 AIOS의 가장 좋은 병행 전략은 다음이다.

- testkit 쪽에서는 `boot-fault`로 실패 경로를 구조화하고
- 커널/OS 쪽에서는 `ring3 + static init`으로 userspace 진입을 여는 것

이 둘이 붙어야 AIOS가 "부팅되는 AI 커널"에서 "AI용 OS 초기형"으로 넘어갈 수 있다.
