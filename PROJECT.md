# AIOS 프로젝트 가이드 (도메인 맵 정본)

이 문서는 저장소를 **하나의 큰 트리가 아니라 7개의 독립 도메인**으로 보고 관리하기 위한
도메인 맵 정본이다. "이 코드는 어디에 두나?", "무엇이 무엇에 의존해도 되나?"를 여기서 정한다.
작업 요청 분류, 정본 선택, 검증과 문서 관리 절차는
[통합 작업 진입 가이드](docs/meta/integrated_work_guide_ko.md)를 따른다.

제품 개요/기능 설명은 [README.md](README.md), 세부 설계는 [docs/README.md](docs/README.md),
저장소 AI 작업·게시 규칙은 [AGENTS.md](AGENTS.md)와 [.agents/README.md](.agents/README.md),
현재 빌드·구현 불변식은 [CLAUDE.md](CLAUDE.md)를 본다. 이 문서는 전역 작업 우선순위나
세부 구현 성숙도를 단독으로 결정하지 않는다.

제품 목적은 **AI가 자신의 작업 공간과 상태를 이해하고 로컬에서 효율적으로 활동하며
사용자와 지속적으로 상호작용하는 운영 환경**이다.
[제품 목적 정본](docs/meta/aios_product_direction_ko.md)이 경험과 성공 기준을 소유한다.

**Room → Cell → Node → NodeBit**는 이 목적을 위한 관리 구조이며 디렉터리 트리와
구별한다. ID·부모 관계·세대·source 결속은 [관리 모델](docs/kernel-room/kernel_room_management_model_ko.md)을
따른다. K1/native K2-a의 bounded proof와 H1 host-only replay는 `CURRENT`, 전체
관리 topology와 bounded hosted MAIN·Cell 수명은 `PARTIAL`이다. 버전·실행 ID·검증
수는 해당 계약/운영 가이드에서 관리한다.

문서 내용 검토: 2026-09-13, 이 체크포인트의 개발 소스 v0.10과 보존된 버전별 계약·실행 증거 대조.
보존된 v0.8 실제 소비의 수정 검증기 재생은 PASS이며, v0.10의 실제 모델 TaskSmoke는 한정 흐름에서 PASS다. source 39개 운영 이미지 acceptance는 미완료다.
재검토 조건과 세부 근거는 [문서 신선도 원장](docs/meta/document_freshness_registry_ko.md)을 따른다.

이 체크포인트의 개발 소스 v0.10은 환경 문맥 전달과 UUID 질문의 접수·조회·결과·취소를 제공하는 `PARTIAL`이다.
CLI 0.10.0/session 10/source 35개, MAIN protocol/run 6/source 28개, receipt 3과
전체 hosted Python source 39개의 계약은 [환경 문맥 가이드](docs/os/aios_space_context_guide_ko.md)가 소유한다.
제품 runtime은 `hosted/`, 한 CLI의 Task smoke 진행과 독립 시나리오 검증은 `tools/` 책임이다.
보존된 v0.9 개발·source 사본·기존 beta·운영 이미지의 실행 기록과 이 체크포인트의 검증 범위를 구분한다.
이전 대화·범용 작업 수정·CLI 소실/재부팅 뒤 자동 재개는 아직 없다. 기존 source 35개 이미지와
과거 실제 모델의 PASS를 새 개발 소스에 승계하지 않으며 source 39개 운영 이미지는 아직 생성·검증하지 않았다.


---

## 1. 도메인 맵

| 도메인 | 책임 | 대표 산출물 | 빌드/실행 |
|---|---|---|---|
| **`kernel/`** | 베어메탈 x86_64 커널. Kernel Room 관리축, 클럭·메모리 보호·인터럽트·드라이버·AI 시스콜 표면 | `kernel/build/aios-kernel.bin` | `make` (→ `kernel/Makefile`) |
| **`os/`** | ring3 유저스페이스 런타임 + 전용 프로그램(`os/apps/`) | 파이썬 도구, (예정) ELF 앱 | `python os/tools/*.py` |
| **`hosted/`** | 의도된 기본 delivery 경로인 Linux-hosted userspace service와 backend-neutral contract | H1 contract/replay `CURRENT`; H2-a startup/inventory/CLI/인터넷·service lifecycle·MAIN binding·자원 관측·Cell 1 관리 전이·backend 수명/실행 결속 `PARTIAL` | `hosted/linux/aios-boot.py`, `aios-console.py`, `aios-agent.py`, `aios-backend.py`; `tools/hosted/Start-AiosConsole.ps1`; 전체 H2/H3는 후속 |
| **`models/`** | AI/SLM 모델 매니페스트(가중치는 비추적) | `models/manifests/*.json` | — (데이터) |
| **`store/`** | 부팅 후 온라인 드라이버/프로그램/모델 다운로드 카탈로그 | `store/catalog/*.json` | (예정) 런타임 클라이언트 |
| **`tools/`** | host 준비·실행·이미지 선택, 테스트·빌드와 외부 source 정책 검증 | `tools/testkit/`, `tools/platform/` | testkit + platform guard |
| **`docs/`** | 설계 문서 (도메인별 하위 폴더) | `docs/<domain>/*.md` | — (문서) |

---

## 2. 의존 방향 규칙 (중요)

화살표는 "참조해도 된다"는 방향이다. **역방향 의존은 금지.**

```text
tools/ ──host lifecycle / build / verify──> kernel/ · os/ · hosted/ · models/ · store/

os/ ──consume public ABI──> kernel/
os/ ──read manifests──────> models/ · store/

hosted/linux/ ──consume──> hosted/contracts/
kernel/ · os/ ──X───────> hosted/
```

- **`kernel/`** 은 다른 어떤 도메인도 import 하지 않는다. 외부와의 접점은 **AI 시스콜 ABI**뿐이다.
- **`os/`** 는 `kernel/`의 시스콜 ABI를 소비하고, `models/`·`store/`의 매니페스트를 읽는다.
- **`hosted/`** 는 Linux-hosted product runtime과 backend-neutral contract를
  소유한다. `kernel/` private header 또는 `os/` ring3 구현을 import하지 않으며,
  `kernel/`과 `os/`도 `hosted/`에 의존하지 않는다.
- **`models/` / `store/`** 는 데이터/카탈로그 도메인이다. 코드 의존성을 만들지 않는다.
- **`tools/`** 는 host의 준비·실행·이미지 선택과 빌드·독립 검증, 외부 source
  manifest 검사를 소유한다. guest의 제품 runtime/backend는 `hosted/`가 소유하며
  guest runtime이 host 도구를 import하거나 실행 의존성으로 삼지 않는다.
- **`docs/`** 는 무엇에도 의존하지 않는다.

---

## 3. "이건 어디에 두나?" 결정 가이드

| 만들려는 것 | 위치 |
|---|---|
| 새 드라이버 / 커널 서브시스템 / 시스콜 | `kernel/<subsystem>/` (+ `kernel/include/`) |
| 커널 빌드에 새 .c 추가 | `kernel/Makefile`의 `C_SOURCES`에 등록 |
| 유저스페이스에서 도는 도구/서비스 | `os/tools/` 또는 `os/runtime/` |
| 사용자가 실행하는 전용 앱 | `os/apps/<app-id>/` |
| 모델(가중치) 추가 | 매니페스트는 `models/manifests/`, 실파일은 `models/weights/`(비추적) |
| 부팅 후 받아올 항목 | `store/catalog/<id>.catalog.json` |
| 테스트/빌드 자동화 | `tools/testkit/` |
| Linux/QEMU/VirtIO upstream source manifest·guard | `tools/platform/` |
| Linux-hosted 실행 서비스와 backend-neutral binding contract | `hosted/` (`hosted/linux/`, `hosted/contracts/`는 해당 구현·verifier와 함께 생성). `tools/platform/`에 runtime 코드를 넣지 않음 |
| Linux 위 공간 상태 조회·대화·작업 진행 인터페이스 | guest 제품 runtime은 `hosted/`; `tools/`는 host 준비·실행·선택·독립 검증을 소유. 새 consumer는 기존 공개 계약을 소비하고 새 protocol은 별도 검증 |
| 제품 목적·전역 큐·문서 관리 | `docs/meta/`의 각 정본; 같은 사실의 새 병렬 roadmap을 만들지 않음 |
| 설계 노트 | `docs/<domain>/` + `docs/README.md` 인덱스 갱신 |
| Room/Cell/Node/NodeBit 관리 설계 | `docs/kernel-room/` + 상위 성숙도 문서 동기화 |

---

## 4. 빌드 & 테스트 진입점

빌드는 루트 `Makefile`이 `kernel/`로 위임한다. **항상 저장소 루트에서 실행한다.**

```bash
make all            # 커널 빌드 (→ kernel/build/aios-kernel.bin)
make test           # 커널 빌드 + QEMU 스모크
make os-smoke       # OS 도구 스모크 (tools/testkit)
make help           # 전체 타깃

# 세분화 테스트 (tools/testkit)
python tools/testkit/aios-testkit.py all --strict
```

Windows: `pwsh -File .\tools\testkit\kernel\build-windows.ps1 -Target test`
(자세한 설치는 [docs/tools/windows_build.md](docs/tools/windows_build.md))

---

## 5. 도메인 간 계약 (불변식)

도메인을 나눠도 아래 계약은 깨지면 안 된다 (CLAUDE.md의 Key Invariants와 동일).

- **AI 시스콜 번호 범위는 ABI-stable** — 재번호/중첩 금지. `kernel/`↔`os/`의 유일한 접점.
- **텐서 64바이트 정렬** — AVX-512 불변식 (`kernel/mm/tensor_mm.c`).
- **Kernel Room 정본 계층은 Room→Cell→Node→NodeBit** — K1 bounded bootstrap registry의 Cell/Node/NodeBit parent-child 관계는 명시적 binding과 generation을 가진다. 이 fixture를 external subsystem binding, live lifecycle 또는 전체 topology로 확장 해석하지 않는다.
- **Kernel Room 게이트 수 = enum 크기** (`kernel/core/kernel_room.c`). 현재 9개 gate descriptor는 syscall-range **분류 메타데이터**이며 dispatcher-level Axis Gate enforcement가 아니다.
- **헬스 스냅샷 ABI 안정** — SLM 오케스트레이터가 소비.
- **Node 네임스페이스 분리** — Memory Fabric `domain_id`, SLM `agent_tree.node_id`, SLM `slm_nodebit_id`, runtime NodeBit `node_id`, pipeline `owner_node`, task/PID/ring ID는 독립이다. 명시적 adapter 없이 교차 비교하지 않는다.
- **NodeBit 시스콜 분리** — `SYS_SLM_NODEBIT_LOOKUP`는 `slm_orchestrator.c`의 SLM policy catalog를 조회한다. `runtime/nodebit.c`는 `SYS_NODEBIT_REGISTER/UPDATE/STATS`와 현재 pipeline capability gate를 제공하는 별도 체계다.
- **Axis Gate enforcement는 `PLANNED`** — canonical parent binding, principal, ownership, generation이 먼저다. `store/` 다운로드와 위험 autonomy action의 공통 Kernel Room authorize 경로도 아직 구현되지 않았다.
- **AI resource ledger는 aggregate 관측 전용** — kind/unit/owner-validity ID는 append-only이고 validity flag가 없는 수치는 지원된 값으로 해석하지 않는다. `SYS_INFO_RESOURCE=0x706`과 `state resource`는 read-only CURRENT이며 owner attribution과 quota/reserve/apply는 아직 없다.
- **AI pressure는 관측 전용** — plane ID는 append-only이며, pressure ranking과 gate eligibility bitmap을 섞지 않는다. 별도 apply 검증 전에는 scheduler migration/budget 변경에 연결하지 않는다.
- **Linux substrate identity는 source-only** — PID/cgroup/pidfd/PSI/path를 canonical
  Cell/Node/NodeBit ID로 재사용하지 않는다. H0 manifest/guard `CURRENT`는 hosted runtime,
  license compatibility 또는 code import 승인이 아니며 schema v1은 `code_import=0`이다.
- **K2/H1과 hosted 실행은 다른 증거 범위** — native K2-a의 boot-local immutable
  결속, H1 host-only replay, hosted MAIN·Cell·backend 수명을 합쳐 전체 지원으로
  표현하지 않는다. [관리 모델](docs/kernel-room/kernel_room_management_model_ko.md)이
  의미를, [hosted 도메인](hosted/README.md)과 분야별 가이드가 실행 범위를 소유한다.
- **상호작용 기록과 관리 identity를 구분한다** — 작업·세션·결과물·사용자 선택을
  canonical Cell/Node/NodeBit로 자동 승격하지 않는다. UI와 에이전트의 상태 인터페이스는
  같은 유효성·결과를 소비하고, 표시를 위한 추론으로 관리 원본을 덮지 않는다.
- **영속성의 대상과 경계를 명시한다** — 설정/history 보존, 모델 결과, 작업 연속성,
  부팅 간 canonical 상태 복원은 별개다. 이미지·source snapshot·실행 증거의 사실은
  [운영 이미지 가이드](docs/os/aios_operating_image_guide_ko.md)가 소유한다.
- **ring3 entry AC 계약은 saved/live를 분리한다** — 현재 QEMU bootstrap pair의 CPL3 `#BP` 2회와 `int 0x80` 6회에서 saved user RFLAGS는 불변이고 entry live AC는 항상 0이어야 한다. SMAP active는 `clac`, 비활성·미지원은 `pushfq/btr/popfq` fallback을 사용하며 `default`/`max-smap` CPU profile과 exact marker/`state sec` mirror가 분기를 고정한다. 이 계약을 future ring3 IRQ/NMI/IST, 실기기, resumable context 또는 process switch 증거로 확장 해석하지 않는다.

---

## 6. 세부 트리

```
aios-kernel/
├── PROJECT.md            # ← 이 문서 (도메인 맵)
├── README.md  CLAUDE.md  Makefile (루트 위임)
│
├── kernel/               # ① 베어메탈 커널
│   ├── Makefile  README.md
│   ├── boot/             # Multiboot2 엔트리, GDT/페이징/long mode
│   ├── core/             # main, health, shell, kernel_room aggregate/management, user_*, linker.ld
│   ├── interrupt/  mm/  sched/  hal/  runtime/  drivers/  lib/
│   └── include/          # 커널 공개 헤더
│
├── os/                   # ② AIOS native ring3 유저스페이스
│   ├── runtime/  main_ai/  compat/  examples/  tools/
│   └── apps/             # 전용 프로그램 (스캐폴드)
│
├── hosted/               # ③ Linux-hosted 기본 delivery 도메인
│   ├── contracts/        # H1-a/b/c CURRENT; 별도 boot-inventory-v1 계약
│   └── linux/            # H2-a/CLI/Internet/MAIN binding PARTIAL; full H2/H3 PLANNED
├── models/               # ④ 모델 매니페스트 (가중치 비추적)
│   └── manifests/
├── store/                # ⑤ 온라인 배포 카탈로그
│   └── catalog/
├── tools/                # ⑥ 테스트·빌드 + 외부 source 정책 검증
│   ├── testkit/
│   └── platform/         # manifest/guard only; hosted runtime 아님
└── docs/                 # ⑦ 설계 문서 (kernel/ autonomy/ os/ models/ tools/ meta/ kernel-room/)
```
