# AIOS Hosted Runtime Domain

> 제품 방향과 책임 경계: 2026-08-12 결정
>
> H2-a userspace startup·하드웨어 inventory·CLI/DNS/HTTP(S): `PARTIAL`
>
> bounded MAIN `AI_SERVICE`·hosted 결속·재결속·자원 관측·Cell 1 관리 전이·backend 수명/실행 결속: `PARTIAL`; 전체 H2/H3 acceptance: `PLANNED`
>
> 기본 설치 이미지의 반복 부팅·설정/history 보존·비특권 CLI: `SUPPORTING/PARTIAL` (image-07 실제 검증)
>
> 별도 모델 이미지: `SUPPORTING/PARTIAL` (현재 v0.7 model-image-05의 실제 모델·두 cold boot·재결속·정상 종료와 전용 사용자 사본 검증; v0.6 model-image-04 보존)
>
> 선행 native K2-a semantic oracle: `CURRENT` (2026-08-15)
>
> OS-neutral H1 trace/replay 성숙도: `CURRENT` (H1-a/H1-b/H1-c 로컬 구현 및
> CI 구성 완료 2026-08-31, type-strict 재검증 2026-09-02; 동일 run·exact SHA의 원격
> Linux/Windows/parity 및 세 artifact acceptance 완료 2026-09-03)

게시 시점의 source 경계: 아래와 연결된 가이드의 현재 소스 일치는 각 로컬 재검증 당시의
결과다. 이후 베타 형식 정리로 제품 35개 중 31개 bytes는 유지하고 4개는 줄바꿈·마지막 빈 줄만
달라졌으며 행·열을 포함한 AST는 같다. 기존 이미지 acceptance는 당시 보존 bytes로 재생한다.
형식 정리 뒤 이미지 부팅은 재실행하지 않았으며 [운영 이미지 가이드 §11](../docs/os/aios_operating_image_guide_ko.md)이
정확한 차이·검사와 host 도구 변경의 범위를 소유한다.

`hosted/`는 AIOS의 의도된 기본 delivery substrate인 Linux-hosted userspace
service를 소유하는 제품 도메인이다. 현재 `linux/aios-boot.py`는 실제 Linux에서
초기화 로그와 source-only CPU·메모리·PCI·USB·block·network inventory를 내는
제한된 실행체다. 독자적인 AIOS 관리 계약과 `kernel/`의 자체 x86_64 커널은 유지하며,
Linux는 이 hosted 경로의 하드웨어 특권 커널·드라이버 실행 기반이다.
`linux/aios-console.py`는 고유 `aios>` 화면에서 반복 명령과 사용자 요청
DNS·HTTP(S) GET을 제공한다. 이 네트워크 I/O는 resource apply capability와 구분한다.

## 책임

- backend-neutral lifecycle/binding trace와 wire contract
- Linux userspace collector와 binding reconciler
- service 시작·재시작·종료 및 host provenance artifact
- native reference producer와 비교하는 cross-backend conformance 입력

현재 도메인 구조는 다음과 같다.

```text
hosted/
├── contracts/    # H1 backend-neutral trace/wire contract
│   │             #   H1-a transport + H1-b semantic replay + H1-c artifact/parity
│   └── fixtures/ #   12개 checked-in valid/invalid trace와 exact sidecar manifest
└── linux/        # H2-a/CLI/Internet/MAIN/자원 관측/Cell 1/backend PARTIAL; full H2/H3 PLANNED
```

실제 하위 디렉터리는 해당 수직 조각과 verifier가 함께 생길 때 추가한다. 빈 scaffold를
구현 진척으로 계산하지 않는다. `contracts/binding-trace-v1.contract.json`, 12개 fixture,
`tools/hosted/binding_trace_replay.py`와 host tests는 H1-a/H1-b/H1-c의 구현이다.
동일 run·exact SHA의 원격 Linux/Windows bundle과 fail-closed parity terminal 및 세
artifact 검증을 통과했으며, 이 host-only contract/replay만 `CURRENT`다.
근거는 [H1 원격 acceptance 증거 (§13.2)](../docs/os/h1_binding_trace_replay_workplan_ko.md#132-2026-09-03-원격-acceptance-완료)를 따른다.

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
3. 동일 run·exact SHA의 원격 Linux/Windows fixture bundle과 parity artifact를 확인해
   H1 contract/replay를 `CURRENT`로 승격했다(2026-09-03).
4. H2-a 시작·하드웨어 관측 실행체와 별도 boot-inventory-v1 계약/외부 verifier를
   추가했다. 관측은 `UNBOUND`, 관리 action은 모두 `UNSUPPORTED`다.
   고유 CLI와 사용자 요청 DNS·HTTP(S) I/O 및 별도 MAIN producer·hosted 결속을 이어 구현했다.
5. H3에서 exit, PID reuse, cgroup recreation, collector restart, host reboot를 구분한다.

H1은 원격 cross-OS terminal과 artifact 증거를 가진 `CURRENT` host-only 계약이다.
fixture의 native K2-a projection은 checked-in source tuple의
self-contained 투영이지 live lifecycle producer가 아니다. H2-a bootstrap은 `PARTIAL`이고
bounded MAIN 결속·재결속도 `PARTIAL`이며 전체 H2/H3 acceptance는 `PLANNED`다. H4/H5, quota,
throttle, scheduler migration, privileged actuator와 apply는 K5 principal/ownership/
authorize 및 별도 승인 전까지 이 도메인의 범위 밖이다.

Upstream 기준선과 `code_import=0` 경계는
[`docs/os/linux_hosted_substrate_and_resource_policy_ko.md`](../docs/os/linux_hosted_substrate_and_resource_policy_ko.md)를
따른다.

## 시작·하드웨어 관측 실행

Linux와 Python 3.11 이상에서 저장소 루트 기준:

```bash
python3 hosted/linux/aios-boot.py --artifact-dir build/hosted-boot/manual-01
python3 tools/hosted/verify_boot.py build/hosted-boot/manual-01 --require-live
python3 tools/hosted/boot_smoke.py --artifact-dir build/hosted-boot/smoke-01
```

매번 새 디렉터리를 사용한다. `READY/0`, `FAILED/1`, `DEGRADED/2`, `UNSUPPORTED/3`을
구분하며 Windows 직접 실행은 `UNSUPPORTED`다. fixture는 live 증거가 되지 않는다.
Windows의 설치된 QEMU를 이용하는 개발 데모는 `tools/hosted/Start-AiosBootDemo.ps1`이다.
데모 Linux 이미지는 개발 호스트이고 AIOS 배포물이나 native AIOS 부팅이 아니다.
실행·artifact·공식 근거·남은 단계는
[유저스페이스 가이드](../docs/os/aios_userspace_boot_hardware_guide_ko.md)를 따른다.

## 대화형 CLI와 인터넷

Windows에서는 `tools/hosted/Start-AiosConsole.cmd`를 실행한다. Linux에서는
`python3 hosted/linux/aios-console.py --artifact-dir <new-directory>`를 사용한다.
`help`, `about`, `status`, `hardware`, `net status`, `resolve HOST`, `fetch URL`,
`clear`, `exit`를 지원하며 오류 뒤에도 다음 명령을 받는다.
HTTPS는 시스템 CA·호스트명을 검증하고 요청 시간·수신량을 제한한다.
이 개발 VM 경로는 임시 환경이며 별도 운영 이미지와 구분한다. 자동 검증과 남은 경계는
[CLI·인터넷 가이드](../docs/os/aios_cli_internet_guide_ko.md)를 따른다.

현재 CLI는 `service status/start/stop/restart`를 제공한다. Linux에서 콘솔을 닫아도
private `CONSOLE_RUNTIME`은 계속 실행된다. Windows VM 실행 도구는 콘솔 종료 후
서비스를 별도로 중지하고 VM을 종료한다. 서비스 identity/generation은 source 영역이며
정식 Cell/Node binding이 아니다. 실행·실패·검증은
[서비스 운영 가이드](../docs/os/aios_service_lifecycle_guide_ko.md)를 따른다.

## MAIN 서비스와 명시적 결속

별도 `aios-agent.py`와 `aios_agent/`, `aios_management/`는 실제 모델 요청을 처리하는
MAIN producer 및 hosted authority를 구현한다(`PARTIAL`). `Start-AiosConsole.ps1 -Agent`로
모델을 준비하고 `agent start`, `room discover`, `room bind`, `ask ...`를 사용한다.
재시작 뒤에는 새 source의 발견과 `room reconcile`이 필요하다. 현재 CLI v0.7/session
schema 7은 runtime source 31개를 기록하고 과거 schema 1/2/3/4/5/6의 보존 소스 재생을
유지한다. MAIN protocol/run schema 4는 source 24개를 기록하며 이전 run schema 1/2/3도 재생한다.

`-AgentSmoke -GuestTests`와 `verify_agent.py --workflow`가 실제 모델 bytes/provenance,
warmup·질문 원문, 두 CLI의 동일 producer, 재시작·stale·재결속, 서비스/backend/VM의
종료를 검증한다. 현재 결과와 실패 이력은
[MAIN 서비스 가이드](../docs/os/aios_agent_binding_guide_ko.md)를 따른다. native K1/K2-a와
H1 host-only 계약을 대신하지 않으며 전체 H2/H3와 자원 귀속·apply는 후속이다.

v0.4에서 도입한 `resources link/status/sample`은 MAIN과 별도 모델 backend 사이의 명시적
관계 아래 CPU·RSS를 각각 관측한다. system PSI는 unattributed이며 ownership·quota
권한을 만들지 않는다. `Start-AiosConsole.ps1 -ResourceSmoke -GuestTests`가 실제
모델 요청·관측·인터넷·정상 종료를 검사한다. 구현과 실제 검증 결과는
[자원 관측 가이드](../docs/os/aios_resource_observation_guide_ko.md)를 따른다.

v0.5의 `cell status/activate/deactivate`는 기존 Cell 1의 관리 활성 상태와 세대를 다룬다
(`DIRECT/PARTIAL`). 비활성화는 MAIN/backend를 중지하지 않으며 기존 결속의 신뢰를
무효화한다. 재활성화 뒤 `room discover`, `room reconcile`, `resources link`로 명시적으로
연결을 복구한다. `Start-AiosConsole.ps1 -CellSmoke -GuestTests`와
`verify_agent.py <run-directory> --cells`의 로컬 Linux·실제 모델 검증은 v0.5 `cell-01`에서 당시 보존 소스로 통과했다.
[Cell 수명 가이드](../docs/os/aios_cell_lifecycle_guide_ko.md)가 계약과 최종 증거를 소유한다.
이 Cell 실행은 당시 v0.5 source snapshot으로 재생한다. 다중 Cell·native Cell lifecycle은 후속이다.
모델 포함 운영 이미지의 별도 실제 acceptance와 한계는 아래 운영 이미지 가이드를 따른다.

v0.6에서 도입한 `backend status/start/stop/restart`는 AIOS 제품 코드가 별도 모델 supervisor와
자식 프로세스의 수명을 관리한다. MAIN 시작 시 실행 대상을 고정하고 receipt schema 2의
`backend_execution`으로 실제 요청 전송 대상과 응답 후 연속성을 검증한다. backend 교체를
발견하면 MAIN readiness와 binding trust를 무효화하며 명시적 MAIN 재시작·재결속이 필요하다.
이 확장은 `backend-02`에서 로컬 Linux·실제 모델 검증을 완료했으며 `PARTIAL`을 유지한다.
같은 endpoint에서 backend 교체 후 요청을 거부하고 명시적 복구 뒤 모델 응답·자원 관측·
DNS·HTTPS·정상 종료를 확인했다. 당시 소스 대조와 독립 재검증도 통과했다.
`backend-02`는 보존된 소스의 역사적 증거이며 이후 모델 이미지 기록 경로 수정이 있는 현재 소스는 별도 검증한다.
[backend 수명 가이드](../docs/os/aios_backend_lifecycle_guide_ko.md)의 `-BackendSmoke -GuestTests`를 따른다.
현재 v0.7의 `backend recover`는 동일 CLI가 생존 중 확보한 child pidfd를 사용한 supervisor
소실 뒤 명시적 정리를 구현한다(`PARTIAL`, 실제 Linux·모델 기록의 별도 독립 재검증 PASS; 원본 FAIL 보존). `RECOVERED`는
원본 실패 run과 별도이며 MAIN 재시작·재결속은 자동화하지 않는다. 세부 계약은 위 backend
수명 가이드를 따른다. CLI 소실·재부팅 이후 복구와 범용 설치·업데이트는 남아 있다.

별도 [Start-AiosImage.cmd](../tools/hosted/Start-AiosImage.cmd)는 설치를 마친 기본 디스크로
AIOS CLI에 직접 진입한다. `image-07`은 같은 4 GiB 디스크의 온라인 두 번·오프라인 한 번
cold boot, 설정·history 보존, 서비스 cleanup과 정상 종료를 통과했다. 검증 원본에서
한 번 만든 사용자 복사본을 재사용하며, 운영 boot 중 host 설치·source 공유를 요구하지 않는다.
보존된 image-07 source 35개는 당시 CLI v0.6/schema 6의 31개에 별도 boot module 4개를 더한 것이다.
모델 없는 기본 이미지의 `SUPPORTING/PARTIAL` 증거이며 MAIN/Cell/native 성숙도를 승격하지 않는다.
정확한 사용법·증거·잔여 범위는 [운영 이미지 가이드](../docs/os/aios_operating_image_guide_ko.md)를 따른다.

별도 `Start-AiosImage.cmd -Agent` 모델 profile도 `SUPPORTING/PARTIAL`이다.
`model-image-04`의 Linux 이미지 검사 51개, 같은 디스크의 online·offline 두 cold boot,
실제 warmup·질문 각 2회, 명시적 재결속·인터넷·정상 종료와 당시 v0.6 source 35개의 독립
재검증을 통과했다. 원본을 보존하는 `local-model` 사용자 사본의 상태 조회·정상 종료도
실제 `.cmd -Agent` 경로에서 확인했다. basic과 실패한 모델 이미지의 원본은 별도로 보존한다.

현재 v0.7/session 7/source 35개의 `model-image-05`는 online·offline 두 부팅의 45명령,
실제 warmup·질문 각 2회·명시적 재결속·인터넷·정상 종료와 현재 소스 독립 재검증을 통과했다.
전용 0.7 실행기 (`build/hosted-image-v07/Start-Aios-0.7.cmd`)는 `model-image-05-user` 사본을
사용하며 실제 세 명령·정상 종료도 PASS다. 기존 v0.6 사용자 디스크와 포인터는 유지한다.
별도 장애 복사본 02도 같은 worker의 recover·즉시 exit·정상 종료를 실제 검증했고,
첫 복사본 01의 원본 FAIL은 보존한다. 정상·장애 이미지의 성숙도는 `SUPPORTING/PARTIAL`을 유지한다.
새 `Selection`·`Select`·`Rollback`은 기본 사용자 사본을 선택하는 host 도구다.
선택 기록이 있으면 기본 Run이 따르고, 없으면 기존 경로를 유지한다. 디스크·history는
사본별로 보존한다. Windows 로컬에서 0.7·0.6의 실제 기본 부팅과 최종 0.7 재선택을
검증했으며 `SUPPORTING/PARTIAL`이다(운영 가이드 §9.8).
