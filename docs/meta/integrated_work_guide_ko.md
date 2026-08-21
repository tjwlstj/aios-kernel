# AIOS 통합 작업 진입 가이드

> 문서 역할: 저장소 전체의 작업 진입·라우팅 및 문서 관리 가이드
>
> 권위 범위: 작업 시작 절차, 요청·방향 분류, 읽을 정본과 로컬 스킬 선택,
> 검증 경로 선택, 문서 수명주기, 완료·게시 체크리스트
>
> 비권위 범위: 구현 성숙도 자체, 도메인 상세 설계, 검증기의 세부 판정 계약,
> 날짜별 완료 이력
>
> 최종 갱신: 2026-08-21

## 1. 통합 방식과 목적

AIOS에는 이미 역할이 다른 작업 가이드와 정본이 있다. 이 문서는 그 내용을 한 파일에
복사해 대체하지 않는다. 대신 새 작업자가 다음 네 질문에 한 경로로 답하도록 한다.

1. 지금 요청은 조사, 진단, 변경, 검증, 게시 중 무엇인가?
2. 어느 도메인과 로컬 스킬이 이 요청을 소유하는가?
3. 어떤 문서가 해당 사실의 정본이며 어떤 문서는 참고 또는 역사 기록인가?
4. 변경 위험에 맞춰 어디까지 검증하고 무엇을 동기화해야 하는가?

세부 명령, 현재 부트 마커 전문, ABI 숫자 목록, 날짜별 마일스톤은 각 정본에 남긴다.
한 사실을 이 문서에 다시 복제하면 정본과 쉽게 어긋나므로, 이 문서는 **경로와 선택
규칙만 소유**한다.

## 2. 작업 시작 체크리스트

모든 작업은 아래 순서로 시작한다.

1. 실제 저장소 루트에서 현재 브랜치, upstream, 작업 트리, 최근 커밋을 확인한다.
2. 기존 tracked/untracked 변경의 소유와 요청 범위를 구분해 사용자 작업을 보존한다.
3. 요청을 §4의 작업 권한 유형으로 분류한다.
4. [AGENTS.md](../../AGENTS.md)와
   [프로젝트 스킬 색인](../../.agents/README.md)에서 필요한 최소 로컬 스킬을 고르고
   해당 `SKILL.md` 전체를 읽는다.
5. [PROJECT.md](../../PROJECT.md)에서 대상 도메인, 파일 위치, 허용 의존 방향을 정한다.
6. [CLAUDE.md](../../CLAUDE.md), 현재 구현, public interface, verifier와 최근 artifact로
   실제 성숙도 경계를 확인한다.
7. §3과 §5에서 해당 주제의 정본·운영 가이드·작업 준비서를 고른다.
8. 변경 전에 가장 작은 수직 조각, 보존할 불변식, rollback 경계와 검증 lane을 정한다.
9. 조사·검토 전용 요청이면 파일, Git, 원격 상태를 바꾸지 않고 결과만 보고한다.

작업 위치가 열리지 않거나 Git 상태가 불명확하면 구현이나 원격 작업보다
[`aios-workspace-recovery`](../../.agents/skills/aios-workspace-recovery/SKILL.md)를
먼저 적용한다.

## 3. 질문별 사실 소유자

모든 문서를 한 줄 서열로 세우지 않는다. 질문의 종류에 따라 다음 소유자를 우선한다.

| 질문 | 우선 권위 | 역할 경계 |
|---|---|---|
| 사용자가 허용한 작업과 mutation 범위 | 현재 사용자 요청 → [AGENTS.md](../../AGENTS.md) | 조사 요청은 변경 권한이 아니다. |
| 저장소 작업 방식, 스킬, Git 게시 | [AGENTS.md](../../AGENTS.md), [`.agents/README.md`](../../.agents/README.md) | `beta` 우선, `main`은 별도 명시 승인과 같은 검증 SHA만 허용한다. |
| 실제 구현 여부와 정확한 범위 | 현재 코드 + public header + verifier + 정규 artifact | 문서 선언이나 probe 한 줄만으로 `CURRENT`가 되지 않는다. |
| 현재 빌드·테스트 진입점과 저수준 불변식 | [CLAUDE.md](../../CLAUDE.md) | 긴 명령과 현재 구현 mirror를 소유한다. |
| 파일 배치와 도메인 의존 방향 | [PROJECT.md](../../PROJECT.md) | 전역 우선순위나 작업 절차를 소유하지 않는다. |
| 제품 축과 전역 작업 우선순위 | [성숙도 우선 작업흐름](minimal_io_and_maturity_workflow_ko.md) | K/M/C/W/H축의 현재 순서를 소유한다. |
| Kernel Room 의미·ID·binding·구현 순서 | [Kernel Room 관리 모델](../kernel-room/kernel_room_management_model_ko.md) | 같은 디렉터리의 개념·개발 문서보다 우선한다. |
| Kernel Room 구현 runbook | [Kernel Room 개발 가이드](../kernel-room/development_guide_ko.md) | 관리 모델 정본을 변경할 권위는 없다. |
| Linux source/import/hosted 경계 | [Linux-hosted substrate 정책](../os/linux_hosted_substrate_and_resource_policy_ko.md) + [resource manifest](../../tools/platform/resources/linux_substrate_resources.json) | source 등록은 code import나 runtime support가 아니다. |
| H1의 bounded 구현 계약 | [H1 trace/replay 작업 준비서](../os/h1_binding_trace_replay_workplan_ko.md) | 상위 Kernel Room·Linux 정본을 따른다. 문서만으로 구현 성숙도가 오르지 않는다. |
| verifier가 PASS/FAIL로 판정하는 의미 | [검증 도구 진화 설계](../tools/verification_tooling_evolution_design_ko.md) | evidence, verdict, 종료, artifact 계약의 정본이다. |
| 실제 testkit 명령과 산출물 | [Testkit 가이드](../tools/testkit_guide_ko.md), [`tools/testkit/README.md`](../../tools/testkit/README.md) | 검증 아키텍처를 다시 설계하지 않는다. |
| 실전 지뢰와 디버깅 이유 | [Codex 핸드오프 팁](codex_handoff_tips_ko.md) | 보충 runbook이며 위 정본을 덮지 않는다. |
| 문서 목록과 현재 수명주기 | [문서 인덱스](../README.md) | `docs/`의 활성·`REVIEW`·`OLD` 탐색 원장이다. |
| 과거 문서 감사의 근거 | [OLD/REVIEW 문서 감사 기록](old_docs_check_2026_07_03_ko.md) | 현재 구현·명령의 정본으로 사용하지 않는다. |

충돌을 발견하면 임의의 문장 하나를 선택하지 않는다.

- 구현 성숙도는 현재 코드와 정규 검증 증거로 다시 확인한다.
- 도메인 의미는 위 표의 주제별 정본에서 고친다.
- 상위 mirror와 인덱스는 같은 변경에서 동기화한다.
- 작업 준비서나 참고 문서가 상위 정본과 충돌하면 상위 정본을 우선하고 충돌을
  명시적으로 정리한다.
- `OLD` 또는 `REVIEW` 문서 안의 “다음 작업”을 현재 전역 큐로 사용하지 않는다.

## 4. 요청 권한과 방향 분류

### 4.1 요청 권한

| 유형 | 허용되는 기본 행동 | 금지되는 확대 해석 |
|---|---|---|
| 조사·검토·생각만 | 읽기, 비교, 분류, 계획, 검증 가능한 진단 | 파일 편집, stage, commit, push |
| 진단 | 원인과 증거 확인, 재현 가능한 read-only 점검 | 요청 없는 구현 또는 외부 상태 변경 |
| 문서 정비 | 요청 범위의 문서·인덱스·링크 수정과 문서 검증 | 구현 성숙도 근거 없는 승격 |
| 구현·변경 | 가장 작은 되돌릴 수 있는 수직 조각 구현과 위험 비례 검증 | 관련 없는 리팩터링, baseline 자동 갱신 |
| 검증 | 지정된 lane 실행, artifact와 종료 의미 확인 | 실패를 숨기는 기준선 쓰기, timeout을 PASS로 해석 |
| checkpoint·게시 | 검증된 변경의 의도적 stage/commit과 `beta` 게시 | 사용자 승인 없는 `main` 이동 |

새 메시지가 작업 범위를 넓히지 않는 한, 기존 허용 범위를 넘어서는 변경은 하지 않는다.

### 4.2 Kernel Room 방향

Kernel Room과 관련된 후보 작업은 다음 중 하나로 분류한다.

- `DIRECT`: Room, Cell, Node, NodeBit 또는 그 binding을 직접 생성·검증한다.
- `SUPPORTING`: 이름이 정해진 관리 마일스톤을 여는 실행 substrate를 보강한다.
- `ORTHOGONAL`: 유용하지만 관리 계층 진척을 직접 만들지 않는 유지보수다.
- `RESEARCH`: 선택적 탐색이며 구현 성숙도 의미가 없다.

`SUPPORTING` 커널·드라이버 작업을 그 자체로 제품의 다음 마일스톤이라고 표현하지
않는다. 현재 전역 우선순위는 항상 [성숙도 작업흐름](minimal_io_and_maturity_workflow_ko.md)에서
새로 확인한다.

## 5. 작업 유형별 라우팅

| 작업 유형 | 먼저 읽을 정본·가이드 | 최소 로컬 스킬 | 대표 검증 경계 |
|---|---|---|---|
| 넓은 방향, 다음 작업 선정 | `PROJECT.md`, `CLAUDE.md`, 현재 roadmap, 관련 정본 | [`aios-repo-triage-planner`](../../.agents/skills/aios-repo-triage-planner/SKILL.md) | 구현·public surface·verifier 증거를 분리하고 조사 전용이면 무변경 |
| Kernel Room, Cell, Node, NodeBit, binding, attribution | 관리 모델 정본, 개발 가이드, 현재 roadmap | [`aios-kernel-room-architecture`](../../.agents/skills/aios-kernel-room-architecture/SKILL.md) | exact record, structured summary, shell mirror, negative rejection; 관리-only 조각은 apply 없음 |
| Linux substrate, hosted, upstream, code reuse | Linux 정책, resource manifest, 필요 시 H1 workplan | [`aios-linux-substrate-curator`](../../.agents/skills/aios-linux-substrate-curator/SKILL.md) | resource guard + platform tests; `source_only`, `code_import=0` 경계 유지 |
| 커널, runtime, MM, scheduler, HAL, public header | `CLAUDE.md`, handoff, 관련 설계 | [`aios-kernel-change-guardian`](../../.agents/skills/aios-kernel-change-guardian/SKILL.md) | compile/static → host test → narrow QEMU → 영향 profile; 복원 불확실 시 fail-stop |
| enum, syscall, 숫자 ID, reason, ABI | public header와 모든 producer/consumer/mirror | [`aios-enum-abi-integrity`](../../.agents/skills/aios-enum-abi-integrity/SKILL.md) | append-only·layout·table 경계, unknown/out-of-range 반례, 영향 lane |
| driver, PCI, storage, USB, e1000, QEMU | 관련 driver 설계, `CLAUDE.md`, testkit guide | [`aios-driver-bringup-qemu`](../../.agents/skills/aios-driver-bringup-qemu/SKILL.md) | discovery/init/data path를 분리하고 한 observable rung만 증명 |
| SLM, autonomy, policy, action, apply | agent contract, 관련 autonomy 계획, Kernel Room 정본 | [`aios-slm-policy-designer`](../../.agents/skills/aios-slm-policy-designer/SKILL.md) | bounded schema, support matrix, reject, stale-state, rollback; observe-only를 apply로 연결하지 않음 |
| testkit, marker, state, CI, baseline, artifact | 검증 정본, Testkit 가이드 | [`aios-verification-tooling-guardian`](../../.agents/skills/aios-verification-tooling-guardian/SKILL.md) | host negative test 우선, fail-closed verdict, 종료와 artifact provenance 분리 |
| README, 설계, roadmap, handoff, 문서화된 surface | 이 문서, 문서 인덱스, 관련 정본 | [`aios-doc-impl-sync`](../../.agents/skills/aios-doc-impl-sync/SKILL.md) | 링크, 역할·수명주기, 이전 문구 검색, mirror sweep, `git diff --check` |
| 작업공간·경로·Git 상태 복구 | recovery skill 자체 | [`aios-workspace-recovery`](../../.agents/skills/aios-workspace-recovery/SKILL.md) | 실제 repo/branch/SHA/dirty/remote 상태를 확인하기 전 변경 금지 |
| commit, push, release | 관련 변경의 정본과 검증 결과 | [`aios-beta-checkpoint-release`](../../.agents/skills/aios-beta-checkpoint-release/SKILL.md) | 의도적 stage, 검증 SHA의 `beta` 게시, terminal CI; `main`은 별도 승인 |

한 요청이 여러 행에 걸치면 필요한 최소 조합만 사용한다. 예를 들어 syscall과 그 부트
증거를 함께 바꾸면 kernel + enum/ABI + verification + documentation 스킬이 필요하다.
반대로 prose-only 링크 정비에 kernel guardian이나 QEMU 실행을 붙이지 않는다.

## 6. 증거 순서의 작업 사이클

```text
사용자 요청 경계
  -> 저장소·사용자 변경 확인
  -> 도메인·방향·성숙도 분류
  -> 현재 구현·public surface·verifier 증거
  -> 최소 수직 조각과 rollback 경계
  -> 구현 또는 문서 정비
  -> host-side 반례·정적 검사
  -> 위험도에 맞는 실행 lane
  -> 관련 정본·mirror·인덱스 동기화
  -> diff·링크·stale wording 검사
  -> 요청된 경우 beta checkpoint와 terminal CI 확인
```

검증은 “명령을 많이 실행했다”가 아니라 해당 주장을 판정할 증거가 있는지로 고른다.
실행하지 않은 lane은 숨기지 않고 완료 보고에 남긴다.

## 7. 변경 표면별 검증 선택

### 문서 탐색·역할·링크만 바꿀 때

- 변경한 모든 저장소 상대 링크의 대상 존재 여부를 검사한다.
- `OLD`/`REVIEW` 문서가 활성 정본이나 현재 작업 계획처럼 노출되지 않는지 확인한다.
- 기존 권위 문구와 이전 상태 표현을 `rg`로 찾아 상충 mirror를 정리한다.
- `git diff --check`를 실행한다.
- 구현·verifier·marker를 바꾸지 않았다면 QEMU 검증을 주장하거나 요구하지 않는다.

### 구현 성숙도 문구를 바꿀 때

- 구현 파일, public header, host verifier, 정규 artifact를 함께 확인한다.
- 해당 주장을 실제로 판정하는 가장 좁은 lane을 재실행한다.
- `README.md`, `CLAUDE.md`, `PROJECT.md`, 현재 roadmap과 도메인 허브의 같은 사실을
  한 변경에서 맞춘다.

### boot marker, selftest, shell `state`, syscall, baseline을 바꿀 때

- [검증 정본](../tools/verification_tooling_evolution_design_ko.md)의 evidence/verdict
  분리를 따른다.
- marker는 Python과 PowerShell 소비자를 함께 갱신한다.
- shell topic은 같은-record 판정과 exchange를 함께 갱신한다.
- 정상·malformed·누락·중복·역순 등 최소 한 개 이상의 host 반례를 먼저 고정한다.
- 영향받는 strict QEMU, shell, inventory/security lane을 실행한다.
- baseline 쓰기는 일반 구현 변경과 별도의 명시적 승인 동작으로 유지한다.

### Linux resource 또는 H1 host-only 계약을 바꿀 때

- resource 변경은 `py -3 tools/platform/linux_resource_guard.py`와 platform unit test를
  실행한다.
- H1은 [작업 준비서](../os/h1_binding_trace_replay_workplan_ko.md)의 host acceptance
  gate를 따른다.
- kernel/K2 producer가 그대로인 host-only 변경에서는 QEMU baseline을 갱신하지 않는다.
- H0/H1 통과를 hosted runtime, license 승인, code import 또는 apply 지원으로 표현하지
  않는다.

## 8. 문서 관리 규칙

### 8.1 문서 역할과 구현 성숙도를 분리한다

문서에는 다음 역할 중 하나를 부여한다.

| 문서 역할 | 소유 범위 |
|---|---|
| 진입 가이드 | 정본·가이드·검증 경로를 선택하는 방법 |
| 정본 | 특정 의미, 계약, 우선순위의 단일 사실 소유자 |
| 운영 가이드 | 명령, 산출물, 재현 절차 |
| 작업 준비서 | 한 bounded slice의 구현 계약, 비목표, acceptance gate |
| 참고 | 배경, 경험, 외부 조사, 설계 후보 |
| 역사 기록 | 특정 시점의 점검·릴리스·과거 판단 |

문서 수명주기는 `활성`, `REVIEW`, `OLD`로 관리한다.

- `활성`: 현재 작업에서 지정된 역할로 사용할 수 있다.
- `REVIEW`: 일부 내용은 유효하지만 현재 정본과 대조해야 한다.
- `OLD`: 역사적 맥락에만 사용하며 현재 구현·우선순위의 근거로 사용하지 않는다.

구현 성숙도는 별도 축이다.

- `CURRENT`: 현재 코드와 명시된 정규 검증 경로가 함께 증명한다.
- `PARTIAL`: 구현은 있으나 범위·플랫폼·판정·실패 경로가 제한된다.
- `SCAFFOLD`: interface 또는 수동 경로만 있고 정규 계약이 없다.
- `PLANNED`: 구현이 없다.
- `RESEARCH`: 선택적 연구 트랙이며 구현 성숙도 승격이 아니다.

예를 들어 역사적 Orbit 검토 문서는 문서 수명주기가 `REVIEW`이고, Orbit 기능 방향은
`RESEARCH`다. 두 상태를 하나로 합치지 않는다.

### 8.2 활성 문서의 권장 머리말

새 문서와 적극 갱신하는 문서는 필요한 항목만 다음 형식으로 명시한다.

```text
문서 역할: 정본 | 운영 가이드 | 작업 준비서 | 참고
권위 범위: 이 문서가 소유하는 사실
상위 정본: 하위 문서일 때
구현 성숙도: 기능 상태를 직접 다룰 때만
최종 갱신: YYYY-MM-DD
```

`OLD`/`REVIEW` 문서는 제목 바로 아래에 수명주기, 분류일, 현재 대체 정본 링크를 둔다.

### 8.3 한 사실에는 한 소유자만 둔다

- 상위 문서는 세부 설계를 복사하지 않고 역할·상태 한 줄과 정본 링크만 둔다.
- ABI, marker, maturity처럼 의도적으로 반복되는 mirror는 구현과 같은 patch에서 모두
  갱신한다.
- 전역 우선순위는 성숙도 작업흐름이 소유한다. 분야별 계획의 “다음”은 해당 도메인
  내부 순서일 뿐 전역 큐가 아니다.
- 실제 testkit 명령은 `CLAUDE.md`, Testkit 가이드와 가까운 README가 소유한다.
  역사 감사 문서는 과거 명령을 현재 명령처럼 유지하지 않는다.
- 이 통합 가이드에는 현재 SHA, marker 전문, 세부 버전 pin, 날짜별 완료 목록을
  기록하지 않는다.

### 8.4 추가·갱신·노후화 절차

새 문서를 추가할 때:

1. 기존 정본이나 작업 준비서로 해결할 수 없는 독립 소유 범위인지 확인한다.
2. 문서 역할, 권위 범위, 상위 정본, 필요한 구현 성숙도를 적는다.
3. [문서 인덱스](../README.md)에 역할과 수명주기를 함께 등록한다.
4. 상위 README나 도메인 허브에는 한 줄 링크만 추가한다.
5. 저장소 상대 링크와 `git diff --check`를 검증한다.

문서를 갱신할 때:

1. 변경한 사실의 정본과 모든 mirror를 찾는다.
2. “최종 갱신”은 의미·계약이 바뀐 경우에만 바꾼다.
3. 구현 성숙도를 바꾸면 코드·verifier·artifact 근거를 같은 변경에서 확인한다.
4. 이전 문구를 검색해 상충 표현이 남지 않게 한다.

문서가 낡았을 때:

1. 링크 안정성과 역사 맥락을 위해 곧바로 삭제·이동하지 않는다.
2. 상단에 `OLD` 또는 `REVIEW` 배너, 분류일, 대체 정본을 적는다.
3. [문서 인덱스](../README.md)의 수명주기를 같은 변경에서 갱신한다.
4. 작업 준비서가 완료되면 삭제하지 말고 결과 정본·구현·검증으로 연결하거나
   명시적인 역사 기록으로 전환한다.

파일명을 바꾸거나 이동할 때는 모든 저장소 링크를 같은 patch에서 바꾸고, 역사 문서의
경로 안정성을 우선한다.

### 8.5 자주 바뀌는 mirror 표면

| 바뀐 사실 | 반드시 함께 찾을 표면 |
|---|---|
| shell `state` topic | kernel producer, `CLAUDE.md`, shell exchanges, Testkit 가이드 |
| syscall 존재·성숙도 | public header, dispatcher/implementation, `CLAUDE.md`, `PROJECT.md`, `README.md`, 관련 설계 |
| boot marker·selftest | kernel producer, Python/PowerShell exact consumer, `CLAUDE.md`, verification 문서 |
| Kernel Room 계층·방향 | 관리 모델, 개발 가이드, `README.md`, `CLAUDE.md`, `PROJECT.md`, handoff, roadmap |
| Linux resource·hosted 상태 | Linux 정본, manifest/guard, hosted README, roadmap, 상위 mirror |
| 전역 다음 작업 | 성숙도 작업흐름, 필요한 bounded workplan, 상위 한 줄 mirror |
| 문서 수명주기 | 문서 자체 배너, `docs/README.md`, 필요 시 OLD/REVIEW 감사 기록 |

## 9. 완료와 게시

완료 전에 다음을 확인한다.

- 요청 범위 밖 파일이나 사용자 변경이 포함되지 않았는가?
- 관련 정본과 mirror가 같은 변경에서 맞춰졌는가?
- `CURRENT`, `PARTIAL`, `SCAFFOLD`, `PLANNED`, `RESEARCH` 경계가 증거와 일치하는가?
- `OLD`/`REVIEW` 문서를 현재 정본처럼 노출하지 않았는가?
- 링크, 이전 문구 검색, `git diff --check`가 통과했는가?
- 실행하지 않은 검증 lane과 남은 위험을 기록했는가?

checkpoint나 게시가 요청된 경우
[`aios-beta-checkpoint-release`](../../.agents/skills/aios-beta-checkpoint-release/SKILL.md)를
적용한다.

1. 검토한 정확한 파일만 stage한다.
2. 변경 위험에 맞는 검증 결과와 cached diff를 확인한다.
3. 검증된 commit을 `beta`에 먼저 게시하고 같은 SHA의 terminal CI 결과를 확인한다.
4. `main`은 사용자의 명시적 승인과 그 검증된 동일 SHA가 있을 때만 fast-forward한다.
5. force push, 별도 release SHA를 만드는 squash/cherry-pick/merge commit을 사용하지 않는다.

완료 보고에는 변경한 사실, 검증 명령과 결과, 남은 `PARTIAL`/`PLANNED` 경계,
실행하지 않은 lane, 최종 branch/upstream/clean 상태를 분리해 적는다.

## 10. 이 가이드의 유지 규칙

이 문서는 다음 경우에만 갱신한다.

- 새 저장소 전용 스킬이나 작업 유형이 생겨 라우팅이 바뀐 경우
- 사실 소유 정본이 새로 생기거나 역할이 이동한 경우
- 문서 수명주기 또는 beta 게시 절차가 바뀐 경우
- 변경 표면과 필수 mirror의 관계가 바뀐 경우

현재 구현 세부, 마일스톤 완료일, upstream exact 버전, 마커 토큰만 바뀐 경우에는 해당
정본과 mirror만 갱신하고 이 문서를 건드리지 않는다.
