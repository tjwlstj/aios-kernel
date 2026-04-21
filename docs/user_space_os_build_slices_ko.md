# AIOS 유저공간 OS 세분화 빌드 계획

작성일: 2026-04-19

## 목적

이 문서는 `AIOS`의 유저공간 OS 방향을
"실제로 어떤 순서로 구현할지" 기준으로 더 잘게 나눈 빌드 계획이다.

상위 방향 문서가 `무엇을 목표로 할지`를 설명한다면,
이 문서는 `무엇부터 손대고 어떤 기준으로 완료를 볼지`를 정리한다.

관련 문서:

- `docs/user_space_os_direction_ko.md`
- `docs/user_space_compat_architecture_ko.md`
- `docs/autonomous_os_execution_roadmap_ko.md`
- `docs/kernel-room/development_guide_ko.md`

## 현재 상태 요약

현재 저장소 기준으로 이미 있는 것:

- 부팅 가능한 커널과 하드웨어 bootstrap
- health / stability summary
- `Kernel Room snapshot`
- `memory_fabric` domain / window
- AI syscall surface와 일부 shared ring 상태
- SLM seed plan과 userspace AI용 hardware-access / clock-distribution snapshot 힌트
- `ring3 scaffold ready=1` marker
- `user_access` 구조적 검증 계층과 `copy_from_user` / `copy_to_user` / 길이 제한 문자열 복사 helper

현재 저장소 기준으로 아직 없는 것:

- 실제 ring3 handoff
- userspace task 실행 경로
- static ELF loader
- `aios-init`
- page table / 권한 기반의 완전한 userspace safety boundary
- userspace service별 디렉토리/책임 분리

즉, 지금은
`OS 위에 올릴 토대는 제법 있음`
하지만
`실제 userspace OS가 올라오는 문은 아직 열리지 않음`
상태다.

## 빌드 원칙

1. 부팅 기준선은 항상 먼저 지킨다.
2. userspace 진입은 가장 작은 실행 단위부터 연다.
3. 커널은 메커니즘, userspace는 정책을 맡는다.
4. SLM 관련 새 구조는 처음부터 "완전 학습"이 아니라 "후보 운영"으로 시작한다.
5. 각 slice는 반드시 관측 가능한 완료 기준을 가진다.

## Slice 0. 부팅 기준선 고정

### 현재 상태

- `boot-matrix`
- `boot-inventory`
- `boot-perf`
- `Kernel Room` snapshot marker

가 이미 있다.

### 목표

userspace 작업을 시작해도 기존 커널 부팅 기준선이 흔들리지 않게 유지한다.

### 최소 패치

- userspace 관련 marker를 추가하더라도 기존 ready marker를 유지
- testkit strict 기준은 kernel-ready와 userspace-enter를 분리

### 완료 기준

- 기존 `full/minimal/storage-only` smoke가 그대로 통과
- userspace 준비 중이라도 boot summary 구조가 깨지지 않음

### 검증 경로

- `python .\\testkit\\aios-testkit.py boot-matrix --profiles full minimal storage-only --strict`

## Slice 1. Ring3 진입 문 열기

### 현재 상태

- GDT / TSS / user selector scaffold는 있다
- 실제 `iretq` handoff는 없다

### 목표

첫 userspace instruction을 안전하게 실행할 수 있는 최소 전이 경로를 만든다.

### 최소 패치

- 고정 userspace entry RIP/RSP 준비
- test-only 또는 bootstrap용 user stub 연결
- user -> kernel 복귀용 최소 trap 또는 syscall probe 하나 연결

### 완료 기준

- serial log에 `userspace-enter` marker가 남음
- user stub가 한 줄 로그를 남기고 커널로 복귀

### 검증 경로

- `kernel --target test --strict --smoke-profile full --export-boot-summary`
- boot summary에 userspace-enter checkpoint 추가

### 위험

- privilege return frame 오류
- TSS / stack / gate misconfiguration

## Slice 2. Static ELF Loader와 `aios-init`

### 현재 상태

- userspace 실행 파일을 적재하는 경로가 없다

### 목표

첫 번째 userspace binary인 `aios-init`를 정적으로 적재한다.

### 최소 패치

- ELF64 header / program header parser
- text/data/bss 매핑
- 정적 링크된 `aios-init` entry jump

### 완료 기준

- `aios-init`가 serial로 첫 로그를 남김
- 커널이 단순 user stub이 아니라 실제 ELF를 실행

### 검증 경로

- QEMU serial log에 `[INITD]` 또는 동등 marker 확인
- 잘못된 ELF 입력 시 안전하게 거부

### 위험

- 잘못된 header 검증
- userspace pointer safety 부재

## Slice 3. Userspace Safety Boundary

### 현재 상태

- `include/kernel/user_access.h`와 `kernel/user_access.c`가 있다
- `access_ok`, `copy_from_user`, `copy_to_user`, `copy_string_from_user`는 구조적 포인터 검증, range overflow 방지, zero-length no-op copy, reason-to-status 매핑을 제공한다
- boot smoke에서 `[UACCESS] selftest PASS` marker로 null / zero-size / overflow / copy / bounded string path를 확인한다
- `runtime/ai_syscall.c`의 tensor create, model load/info, inference ring, train forward, autonomy control, SLM plan status/list/submit 일부 경로는 request staging copy와 output staging copy를 사용한다
- `SYS_INFO_BOOTSTRAP`과 `SYS_SLM_HW_SNAPSHOT`은 커널 local snapshot을 만든 뒤 `copy_to_user`로 반환한다
- 아직 page table 기반의 userspace 소유권 / 권한 검사는 없다

### 목표

userspace가 커널에 진입해도 커널이 포인터와 요청을 안전하게 다룰 수 있게 만든다.

### 최소 패치

- page table / fault recovery가 들어온 뒤 큰 snapshot 계열의 실제 page-backed userspace fault 경계를 재검증
- 아직 남은 request struct 입력 경로에 `copy_from_user` 적용
- page table / user range가 생기면 `access_ok` 내부에 권한 검사를 추가
- 기본 request struct 검증

### 완료 기준

- null / overflow / unsupported flag가 panic이 아니라 에러 코드로 돌아감
- page-backed userspace 주소가 도입된 뒤 잘못된 userspace 주소가 panic이 아니라 에러 코드로 돌아감
- 첫 `aios-init`와 이후 service가 같은 경계 위에서 동작

### 검증 경로

- invalid pointer negative test
- good path request smoke

### 위험

- page permission 분리가 완전하지 않은 단계에서 오판 가능성

## Slice 4. Kernel Mechanism -> Userspace Bootstrap Service

### 현재 상태

- control plane의 상당 부분이 아직 커널 내부에 있다

### 목표

최소 userspace bootstrap service를 띄우고,
커널은 관측과 primitive 제공에 더 집중하게 만든다.

### 최소 패치

- `aios-init`
- `aios-osd`
- `aios-compatd`

중 최소 하나부터 userspace binary로 분리

### 완료 기준

- userspace service 하나가 boot mode / health / room snapshot을 읽음
- 커널 로그와 userspace 로그가 서로 이어짐

### 검증 경로

- `SYS_INFO_ROOM` read path smoke
- `SYS_INFO_SYSTEM` / `SYS_INFO_MEMORY` read smoke

## Slice 5. Service Plane 분리

### 현재 상태

- `aios-agentd`, `aios-modeld`, `aios-kvcached`, `aios-memd`는 문서상 역할만 있다

### 목표

userspace OS를 "실행 가능한 서비스 집합"으로 보이게 만든다.

### 최소 패치

- 서비스별 디렉토리 / README / config sample
- service boot order와 입력/출력 명세

### 완료 기준

- 각 서비스가 맡는 책임이 겹치지 않게 문서화
- 첫 구현 대상을 바로 고를 수 있게 됨

### 검증 경로

- 문서와 디렉토리 구조가 일치
- `os/runtime/` 아래 책임 경계가 보임

## Slice 6. 후보형 SLM 운영층

### 현재 상태

- SLM seed plan과 커널 snapshot은 있다
- `slm_hw_snapshot_t`는 read-only로 userspace AI 접근성 점수, mediated I/O 여부, shared ring / zero-copy 권장 여부, 클럭 분배율을 제공한다
- 같은 snapshot에 SLM runtime state/status와 작은 API/tool/device NodeBit 카탈로그가 들어가며, NodeBit는 현재 read-only 정책 힌트와 plan submit의 1차 action gate로 쓰인다
- 모델 선택은 아직 정적/수동 해석이 강하다

### 목표

SLM 문제를 "정답 모델 하나 선택"이 아니라
"후보군 관측과 승급" 구조로 바꾼다.

### 최소 패치

- `seed SLM`
- `candidate registry`
- `observer service`
- `promotion policy`

문서와 config sample부터 시작

### 완료 기준

- 모델별 status가 `experimental / bounded / stable`처럼 구분됨
- hardware snapshot과 health summary를 보고 승급 판단 기준이 문서화됨
- 후보 운영 서비스가 direct MMIO가 아니라 mediated I/O와 클럭 분배 힌트를 읽어 초기 worker / I/O poll 예산을 잡음
- userspace policy layer가 NodeBit의 `node_id`, `kind`, `flags`, `allow_bits`, `observe_only_bits`, `risky_bits`를 읽고 API/tool/device 사용 가능성을 빠르게 판정함

### 검증 경로

- 샘플 manifest / config / state 문서 검토
- QEMU serial log의 `[SLM] Runtime state=... nodebits=...`와 `[SLM] UserAI access ... clock=...` marker 확인
- runtime 디렉토리 내 policy 위치가 정리됨

## Slice 7. Observer / Builder 확장

### 현재 상태

- 이 부분은 아직 구현보다 설계가 앞선다

### 목표

observer / builder 아이디어를 userspace policy layer에서만 점진적으로 붙인다.

### 최소 패치

- continuity trace format
- observer input set
- builder output artifact 형식
- rollback / quarantine 규칙

### 완료 기준

- base model 직접 수정 대신 adapter/distillation artifact로만 반영
- health gate와 승급 정책이 분리됨

## 지금 가장 추천하는 순서

1. Slice 0 유지
2. Slice 1 `ring3 handoff`
3. Slice 2 `static ELF loader + aios-init`
4. Slice 3 safety boundary
5. Slice 4 bootstrap service
6. Slice 5 service plane
7. Slice 6 후보형 SLM 운영층
8. Slice 7 observer / builder

즉, 지금 가장 중요한 건
`메인 AI 기능을 더 많이 넣는 것`
보다
`userspace로 넘어가는 문과 첫 서비스 기반을 여는 것`
이다.

## 하지 말아야 할 것

- shell이나 앱 런타임을 먼저 크게 상상하고 ring3 진입을 뒤로 미루기
- `LONG_TERM`을 바로 persistent memory system처럼 쓰기
- 커널에 full training loop를 넣기
- 후보형 SLM 운영층 없이 모델 하나를 정답처럼 고정하기
- userspace safety boundary 없이 service를 늘리기

## 결론

현재 AIOS의 유저공간 OS 빌드는
큰 구조를 한 번에 만들기보다,
`ring3 -> loader -> init -> service -> policy`
순서로 좁고 관측 가능한 조각을 쌓는 편이 맞다.

이 문서는 그 조각들을 흔들리지 않게 이어붙이기 위한 작업 기준선이다.
