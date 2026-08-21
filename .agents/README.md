# AIOS Project Skills

이 디렉터리는 AIOS를 오래 작업하며 반복해서 발생한 불편과 안전 규칙을
저장소 자체의 Codex 스킬로 고정한다. 개인 환경의 전역 스킬과 충돌하지
않도록 모든 이름에 `aios-` 접두사를 사용한다.

## Skill index

| Skill | 사용할 때 | 막으려는 문제 |
|---|---|---|
| `aios-repo-triage-planner` | 넓은 요청, 다음 작업 선정, 조사 | 현재 구현을 보지 않은 과대 계획 |
| `aios-linux-substrate-curator` | Linux substrate 자료, upstream 리소스, hosted backend 계획, 라이선스·SPDX·provenance 검토 | source 목록이 코드 import 승인이나 구현된 backend로 과장되는 경계 붕괴 |
| `aios-kernel-room-architecture` | Kernel Room, Cell, Node, NodeBit, Axis Gate, Orbit, 귀속 설계 | 관리 계층보다 커널 메커니즘이나 enforcement가 먼저 커지는 방향 drift |
| `aios-kernel-change-guardian` | 커널, 런타임, MM, 스케줄러, HAL | 저수준 invariant와 rollback 손상 |
| `aios-enum-abi-integrity` | enum, syscall, action/reason ID | 숫자·ABI의 조용한 drift |
| `aios-driver-bringup-qemu` | e1000, storage, USB, PCI, QEMU | probe를 실제 지원으로 과장 |
| `aios-slm-policy-designer` | SLM, autonomy, policy, verifier | 무제한 AI 액션과 rollback 부재 |
| `aios-verification-tooling-guardian` | testkit, marker, CI, baseline | false PASS, stale artifact, 약한 판정 |
| `aios-doc-impl-sync` | README, 설계, 로드맵, handoff, 문서화된 표면(시스콜·state 토픽·마커)을 바꾸는 코드 변경 | 문서와 구현 성숙도의 낙관적 drift, 상위 미러 문서(CLAUDE.md·README.md·PROJECT.md) 갱신 누락 |
| `aios-workspace-recovery` | `Z:`·cwd·권한·프로필 오류 | 로컬 상태를 모른 채 원격을 변경 |
| `aios-beta-checkpoint-release` | commit, checkpoint, push | beta 미검증 main 반영과 SHA 분기 |

## 공통 흐름

1. `AGENTS.md`에서 요청에 맞는 최소 스킬을 고른다.
2. 선택한 `SKILL.md` 전체를 읽는다.
3. [통합 작업 진입 가이드](../docs/meta/integrated_work_guide_ko.md)에서 요청 유형,
   도메인 정본, 검증 경로와 문서 동기화 범위를 고른다.
4. 현재 브랜치·작업 트리·문서·구현 증거를 먼저 확인한다.
5. 최소 수직 조각을 구현하거나, 조사 전용 요청이면 변경 없이 멈춘다.
6. 변경 위험에 맞는 검증을 수행하고 실행하지 않은 lane을 기록한다.
7. 게시 요청이면 `beta`를 먼저 검증한 뒤 승인된 경우에만 같은 SHA로
   `main`을 fast-forward한다.

각 스킬의 `agents/openai.yaml`은 Codex UI에서 보이는 이름, 설명, 기본
프롬프트를 제공한다. 구조나 설명을 바꾸면 skill validator를 다시 실행한다.
