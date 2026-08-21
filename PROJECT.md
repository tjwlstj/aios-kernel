# AIOS 프로젝트 가이드 (도메인 맵 정본)

이 문서는 저장소를 **하나의 큰 트리가 아니라 7개의 독립 도메인**으로 보고 관리하기 위한
도메인 맵 정본이다. "이 코드는 어디에 두나?", "무엇이 무엇에 의존해도 되나?"를 여기서 정한다.
작업 요청 분류, 정본 선택, 검증과 문서 관리 절차는
[통합 작업 진입 가이드](docs/meta/integrated_work_guide_ko.md)를 따른다.

제품 개요/기능 설명은 [README.md](README.md), 세부 설계는 [docs/README.md](docs/README.md),
저장소 AI 작업·게시 규칙은 [AGENTS.md](AGENTS.md)와 [.agents/README.md](.agents/README.md),
현재 빌드·구현 불변식은 [CLAUDE.md](CLAUDE.md)를 본다. 이 문서는 전역 작업 우선순위나
세부 구현 성숙도를 단독으로 결정하지 않는다.

제품 구조의 정본은 **Kernel Room → Cell → Node → NodeBit** 관리축이다. 이 계층은
저장소 디렉터리 구조와 다른 논리 구조다. aggregate substrate가 있어 전체 Kernel Room
topology 성숙도는 `PARTIAL`이다. `CURRENT` K1은 별도 1024B management-only snapshot에 Cell 1,
exact-bound Node 1, parent-bound typed NodeBit 2를 함께 둔 bounded bootstrap hierarchy를
구현했다. `CURRENT`인 bounded native K2-a oracle은 별도 256B snapshot에서 Node 101을
producer-owned SLM MAIN source에 boot-local immutable하게 결속한다. 현 코드는 Room
aggregate snapshot과 각자 `CURRENT`인 Memory Fabric domain, SLM agent
profile/policy catalog, runtime capability NodeBit, pipeline ownership을 제공한다. 새 작업은 이들을
숫자 ID가 같다는 이유로 결합하지 말고 namespace + explicit binding + generation으로 연결한다.
K1 bootstrap fixture는 Cell ID 1, Node ID 101, NodeBit ID 1001/1002를 사용하며
K2 전체 lifecycle/reconcile은 `PARTIAL`, H1/Linux-hosted source는 `PLANNED`다.
용어와 성숙도 정본은
[Kernel Room 관리 모델](docs/kernel-room/kernel_room_management_model_ko.md)이다.

---

## 1. 도메인 맵

| 도메인 | 책임 | 대표 산출물 | 빌드/실행 |
|---|---|---|---|
| **`kernel/`** | 베어메탈 x86_64 커널. Kernel Room 관리축, 클럭·메모리 보호·인터럽트·드라이버·AI 시스콜 표면 | `kernel/build/aios-kernel.bin` | `make` (→ `kernel/Makefile`) |
| **`os/`** | ring3 유저스페이스 런타임 + 전용 프로그램(`os/apps/`) | 파이썬 도구, (예정) ELF 앱 | `python os/tools/*.py` |
| **`hosted/`** | 의도된 기본 delivery 경로인 Linux-hosted userspace service와 backend-neutral contract | 책임 경계 문서, (예정) H1 trace/H2 service | 현재 실행 구현 없음 (`PLANNED`) |
| **`models/`** | AI/SLM 모델 매니페스트(가중치는 비추적) | `models/manifests/*.json` | — (데이터) |
| **`store/`** | 부팅 후 온라인 드라이버/프로그램/모델 다운로드 카탈로그 | `store/catalog/*.json` | (예정) 런타임 클라이언트 |
| **`tools/`** | 테스트·빌드 오케스트레이션과 외부 substrate source 정책 검증 | `tools/testkit/`, `tools/platform/` | testkit + platform guard |
| **`docs/`** | 설계 문서 (도메인별 하위 폴더) | `docs/<domain>/*.md` | — (문서) |

---

## 2. 의존 방향 규칙 (중요)

화살표는 "참조해도 된다"는 방향이다. **역방향 의존은 금지.**

```text
tools/ ──build/test only──> kernel/ · os/ · hosted/ · models/ · store/

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
- **`tools/`** 는 전부를 빌드/검증하고 외부 source manifest를 검사할 뿐 runtime
  backend를 제공하지 않는다. 어떤 도메인도 `tools/`에 의존하면 안 된다.
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
- **K2-a native semantic oracle은 `CURRENT`, K2 전체는 `PARTIAL`** — 별도
  256B snapshot이 Node 101을 producer-owned SLM MAIN instance/generation에 결속하고
  append-only reject reason을 고정한다. refresh/exit/recreate/rebind는 아직 없다.
  다음 H1 replay가 OS-neutral lifecycle과 negative fixture를 고정한 뒤 H2
  observe-only service를 시작한다. 광범위한 native
  process/storage 확장은 선행조건이 아니다. H4 validation과 H5 apply는 K5와 별도 승인 전까지
  `PLANNED`다.
- **process snapshot/journal은 증거 전용** — descriptor-owned 176B snapshot은 ISR 시점 owner/CR3/TSS `rsp0`/IF=0 검증과 `resume_ready=0`을 유지한다. per-boot process event journal v1의 schema/kind/reason/outcome 숫자 ID는 append-only이며, capacity 8 안에서 여섯 lifecycle/capture record를 덮어쓰지 않는다. journal은 `evidence_only=1 switch_events=0 resume_ready=0`이고 `0→1→0→2→0` 순차 bootstrap owner lifecycle만 증명한다. resumable saved context와 runnable-state 결속, live continuation/switch, 실제 A→B→A가 별도로 검증되기 전에는 snapshot이나 journal을 schedulable state 또는 CPU-switch trace로 해석하지 않는다.
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
│   └── README.md         # 책임 경계만 결정; H1/H2 실행 구현은 PLANNED
├── models/               # ④ 모델 매니페스트 (가중치 비추적)
│   └── manifests/
├── store/                # ⑤ 온라인 배포 카탈로그
│   └── catalog/
├── tools/                # ⑥ 테스트·빌드 + 외부 source 정책 검증
│   ├── testkit/
│   └── platform/         # manifest/guard only; hosted runtime 아님
└── docs/                 # ⑦ 설계 문서 (kernel/ autonomy/ os/ models/ tools/ meta/ kernel-room/)
```
