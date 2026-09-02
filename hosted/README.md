# AIOS Hosted Runtime Domain

> 제품 방향과 책임 경계: 2026-08-12 결정
>
> Linux-hosted runtime 구현 성숙도: `PLANNED`
>
> 선행 native K2-a semantic oracle: `CURRENT` (2026-08-15)
>
> OS-neutral H1 trace/replay 성숙도: `PARTIAL` (H1-a/H1-b/H1-c 로컬 구현 및
> CI 구성 완료 2026-08-31, type-strict 재검증 2026-09-02; exact SHA 원격
> Linux/Windows/parity 결과 확인 전)

`hosted/`는 AIOS의 의도된 기본 delivery substrate인 Linux-hosted userspace
service를 소유하는 제품 도메인이다. 이 디렉터리가 생겼다는 사실은 daemon,
collector, reconciler 또는 runtime verdict가 구현됐다는 뜻이 아니다.

## 책임

- backend-neutral lifecycle/binding trace와 wire contract
- Linux userspace collector와 binding reconciler
- service 시작·재시작·종료 및 host provenance artifact
- native reference producer와 비교하는 cross-backend conformance 입력

계획 구조는 다음과 같다.

```text
hosted/
├── contracts/    # H1 backend-neutral trace/wire contract
│   │             #   H1-a transport + H1-b semantic replay + H1-c artifact/parity
│   └── fixtures/ #   12개 checked-in valid/invalid trace와 exact sidecar manifest
└── linux/        # H2 Linux userspace service (PLANNED, 디렉터리 미생성)
```

실제 하위 디렉터리는 해당 수직 조각과 verifier가 함께 생길 때 추가한다. 빈 scaffold를
구현 진척으로 계산하지 않는다. `contracts/binding-trace-v1.contract.json`, 12개 fixture,
`tools/hosted/binding_trace_replay.py`와 host tests는 H1-a/H1-b/H1-c의 로컬 구현이다.
다만 동일 exact SHA의 원격 Linux/Windows bundle과 fail-closed parity terminal 결과가
아직 없으므로 H1 전체는 계속 `PARTIAL`이다.

## 의존 경계

- `hosted/linux/`는 `hosted/contracts/`의 versioned public contract만 소비한다.
- `kernel/`과 `os/`는 `hosted/`에 의존하지 않는다.
- hosted 코드는 AIOS kernel private header나 Linux kernel internal API를 import하지
  않는다.
- `tools/`는 hosted artifact를 빌드·검증할 수 있지만 product runtime을 소유하지 않는다.
- Linux PID, pidfd, cgroup, namespace, PSI, path는 canonical Cell/Node/NodeBit가 아니라
  `source_only` evidence다.

## 첫 구현 순서

1. bounded native K2-a semantic oracle를 고정했다. 이 조각은 boot-local immutable
   source binding만 증명하며 전체 lifecycle 계약 완료가 아니다.
2. H1-a transport, H1-b substrate-neutral lifecycle replay와 12개 fixture, H1-c
   self-contained bundle/parity CLI와 전용 양 OS CI를 구현했다. 필드·state·acceptance는
   [`H1 작업 준비서`](../docs/os/h1_binding_trace_replay_workplan_ko.md)를 따른다.
3. exact SHA의 원격 Linux/Windows fixture bundle과 parity artifact를 확인한 뒤에만 H1
   승격 여부를 판단한다.
4. H2는 Linux kernel module이 아닌 observe-only userspace service 한 개로 시작한다.
5. H3에서 exit, PID reuse, cgroup recreation, collector restart, host reboot를 구분한다.

H1은 로컬 구현과 CI wiring이 존재하지만 원격 cross-OS terminal 증거 전까지
`PARTIAL`이다. fixture의 native K2-a projection도 checked-in source tuple의
self-contained 투영이지 live lifecycle producer가 아니다. H2/H3는 실행 코드와 정규
runtime verdict가 생기기 전까지 `PLANNED`다. H4/H5, quota,
throttle, scheduler migration, privileged actuator와 apply는 K5 principal/ownership/
authorize 및 별도 승인 전까지 이 도메인의 범위 밖이다.

Upstream 기준선과 `code_import=0` 경계는
[`docs/os/linux_hosted_substrate_and_resource_policy_ko.md`](../docs/os/linux_hosted_substrate_and_resource_policy_ko.md)를
따른다.
