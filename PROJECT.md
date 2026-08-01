# AIOS 프로젝트 가이드 (도메인 맵)

이 문서는 저장소를 **하나의 큰 트리가 아니라 6개의 독립 도메인**으로 보고 관리하기 위한
마스터 가이드다. "이 코드는 어디에 두나?", "무엇이 무엇에 의존해도 되나?"를 여기서 정한다.

제품 개요/기능 설명은 [README.md](README.md), 세부 설계는 [docs/README.md](docs/README.md),
AI 에이전트(Claude Code)용 작업 규칙은 [CLAUDE.md](CLAUDE.md)를 본다.

---

## 1. 도메인 맵

| 도메인 | 책임 | 대표 산출물 | 빌드/실행 |
|---|---|---|---|
| **`kernel/`** | 베어메탈 x86_64 커널. 클럭·메모리 보호·인터럽트·드라이버·AI 시스콜 표면 | `kernel/build/aios-kernel.bin` | `make` (→ `kernel/Makefile`) |
| **`os/`** | ring3 유저스페이스 런타임 + 전용 프로그램(`os/apps/`) | 파이썬 도구, (예정) ELF 앱 | `python os/tools/*.py` |
| **`models/`** | AI/SLM 모델 매니페스트(가중치는 비추적) | `models/manifests/*.json` | — (데이터) |
| **`store/`** | 부팅 후 온라인 드라이버/프로그램/모델 다운로드 카탈로그 | `store/catalog/*.json` | (예정) 런타임 클라이언트 |
| **`tools/`** | 테스트툴 + 빌드 오케스트레이션 (testkit) | `tools/testkit/` | `python tools/testkit/aios-testkit.py` |
| **`docs/`** | 설계 문서 (도메인별 하위 폴더) | `docs/<domain>/*.md` | — (문서) |

---

## 2. 의존 방향 규칙 (중요)

화살표는 "참조해도 된다"는 방향이다. **역방향 의존은 금지.**

```
                 ┌─────────────────────────────────────────┐
   tools/  ─────▶│ (모든 도메인을 테스트/빌드만 한다)         │
                 └─────────────────────────────────────────┘

   models/ ──┐
             ├──▶  os/  ──▶  kernel/        (kernel 은 위를 모른다)
   store/  ──┘     (apps)     (ABI만 노출)
```

- **`kernel/`** 은 다른 어떤 도메인도 import 하지 않는다. 외부와의 접점은 **AI 시스콜 ABI**뿐이다.
- **`os/`** 는 `kernel/`의 시스콜 ABI를 소비하고, `models/`·`store/`의 매니페스트를 읽는다.
- **`models/` / `store/`** 는 데이터/카탈로그 도메인이다. 코드 의존성을 만들지 않는다.
- **`tools/`** 는 전부를 빌드/검증만 한다. 어떤 도메인도 `tools/`에 의존하면 안 된다.
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
| 설계 노트 | `docs/<domain>/` + `docs/README.md` 인덱스 갱신 |

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
- **Kernel Room 게이트 수 = enum 크기** (`kernel/core/kernel_room.c`).
- **헬스 스냅샷 ABI 안정** — SLM 오케스트레이터가 소비.
- **정책 게이트** — `store/` 다운로드와 자율 행위는 `SYS_SLM_NODEBIT_LOOKUP` + Kernel Room 게이트를 통과한다.
- **AI resource ledger는 aggregate 관측 전용** — kind/unit ID는 append-only이고 validity flag가 없는 수치는 지원된 값으로 해석하지 않는다. owner attribution과 quota/reserve/apply는 아직 없다.
- **AI pressure는 관측 전용** — plane ID는 append-only이며, pressure ranking과 gate eligibility bitmap을 섞지 않는다. 별도 apply 검증 전에는 scheduler migration/budget 변경에 연결하지 않는다.

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
│   ├── core/             # main, health, acpi, time, shell, kernel_room, user_*, linker.ld
│   ├── interrupt/  mm/  sched/  hal/  runtime/  drivers/  lib/
│   └── include/          # 커널 공개 헤더
│
├── os/                   # ② 유저스페이스
│   ├── runtime/  main_ai/  compat/  examples/  tools/
│   └── apps/             # 전용 프로그램 (스캐폴드)
│
├── models/               # ③ 모델 매니페스트 (가중치 비추적)
│   └── manifests/
├── store/                # ④ 온라인 배포 카탈로그
│   └── catalog/
├── tools/                # ⑤ 테스트툴 + 빌드 오케스트레이션
│   └── testkit/
└── docs/                 # ⑥ 설계 문서 (kernel/ autonomy/ os/ models/ tools/ meta/ kernel-room/)
```
