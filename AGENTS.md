# AIOS Agent Instructions

## 대화와 연속성

- 매 턴 `chesi-persona-codex` 스킬이 설치되어 있으면 사용을 권장한다.
- 새 작업 전에 현재 브랜치, 작업 트리, 최근 커밋을 확인한다.
- `CLAUDE.md`, `PROJECT.md`, `docs/meta/codex_handoff_tips_ko.md`와 관련
  설계 문서를 현재 구현과 함께 읽는다.
- 작업 유형, 질문별 정본, 검증 경로와 문서 수명주기는
  `docs/meta/integrated_work_guide_ko.md`에서 고른다. 이 통합 가이드는 세부
  정본을 대체하지 않는다.
- Kernel Room, Cell, Node, NodeBit, Axis Gate, Orbit 또는 리소스 귀속을
  다룰 때는 `docs/kernel-room/kernel_room_management_model_ko.md`를 정본으로
  먼저 읽는다.
- Linux substrate, upstream 리소스, hosted backend, 코드 재사용 또는
  provenance를 다룰 때는
  `docs/os/linux_hosted_substrate_and_resource_policy_ko.md`와
  `tools/platform/resources/linux_substrate_resources.json`을 먼저 확인한다.
- 사용자가 조사나 대기만 요청했다면 파일, Git, 원격 상태를 변경하지 않는다.
- 대화형 QEMU 디버깅이 필요하면 `docs/tools/qemu_mcp_guide_ko.md`의 qemu-mcp
  편의 도입을 쓸 수 있다. 이 경로는 진단 전용이며 PASS/FAIL 판정과 baseline은
  반드시 `tools/testkit` 정규 lane으로만 수행한다.

## 프로젝트 스킬

저장소 전용 스킬은 `.agents/skills/`에 있다. 요청과 변경 범위에 맞는 최소
스킬만 선택하고, 선택한 `SKILL.md`를 끝까지 읽은 뒤 작업한다.

- 넓거나 탐색적인 요청: `aios-repo-triage-planner`
- Linux substrate·upstream resource·hosted backend·코드 import 경계:
  `aios-linux-substrate-curator`
- Kernel Room·Cell·Node·NodeBit 구조/방향: `aios-kernel-room-architecture`
- 커널·런타임·드라이버 변경: `aios-kernel-change-guardian`
- 숫자 ID·enum·ABI 변경: `aios-enum-abi-integrity`
- 드라이버·QEMU bring-up: `aios-driver-bringup-qemu`
- SLM·자율 정책 설계: `aios-slm-policy-designer`
- testkit·CI·증거·판정 변경: `aios-verification-tooling-guardian`
- 문서·구현 성숙도 동기화(코드가 문서화된 표면을 바꿀 때 포함): `aios-doc-impl-sync`
- 로컬 경로·작업공간 복구: `aios-workspace-recovery`
- 체크포인트·배포: `aios-beta-checkpoint-release`

전체 색인과 사용 목적은 `.agents/README.md`를 따른다.

## Git 게시 정책

- 기본 작업 브랜치는 `beta`다.
- 체크포인트는 관련 검증 후 `beta`에 먼저 게시한다.
- `main` 이동은 사용자의 명시적 승인과 해당 beta SHA의 원격 검증 성공이
  모두 있을 때만 수행한다.
- `main`은 검증된 `beta`와 동일한 SHA로 fast-forward한다.
- 이 흐름에서 force push, merge commit, cherry-pick, squash로 별도 release
  SHA를 만들지 않는다.
- 작업을 마치면 가능하면 `beta`로 돌아와 clean/synced 상태를 확인한다.

## 구현과 검증 원칙

- `CURRENT`, `PARTIAL`, `SCAFFOLD`, `PLANNED`를 구현 성숙도에 사용하고,
  `RESEARCH`는 선택적 연구 트랙에만 사용한다.
- 검증 증거와 host verdict를 분리하고 fail-closed로 판정한다.
- 사용자 변경을 보존하고, 관련 없는 파일을 임의로 stage하거나 정리하지 않는다.
- 최소의 되돌릴 수 있는 수직 조각을 우선하며, 실행하지 않은 검증은 명시한다.
- 커널 실행 기반 작업은 Kernel Room 관리 모델의 `SUPPORTING` 축일 수 있으나,
  `Room → Cell → Node → NodeBit`의 직접 마일스톤을 자동으로 대신하지 않는다.
