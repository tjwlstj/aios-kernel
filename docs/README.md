# AIOS 설계 문서 인덱스

> 문서 역할: 문서 탐색 색인
> 문서 수명주기: 활성
> 마지막 내용 검토: 2026-09-13 — v0.10 Task의 정본·검증 진입 대조; 과거 실행 판정 갱신 아님

이 파일은 `docs/` 아래 문서를 찾는 **단일 탐색 색인**이다. 제품 목적은
[제품 방향 정본](meta/aios_product_direction_ko.md), 다음 작업은
[전역 작업흐름](meta/minimal_io_and_maturity_workflow_ko.md), 요청별 읽기·작업 순서는
[통합 작업 진입 가이드](meta/integrated_work_guide_ko.md)를 따른다. 문서의 검토일과
재검토 조건은 [신선도 원장](meta/document_freshness_registry_ko.md)에서 확인한다.

## 작업 시작

| 필요한 것 | 먼저 읽을 문서 | 문서 역할 |
|---|---|---|
| 제품 목적, AI의 작업 공간·사용자 상호작용, 성공 기준 | [제품 방향 정본](meta/aios_product_direction_ko.md) | 제품 방향 정본 |
| 요청 분류, 정본·스킬·검증 선택 | [통합 작업 진입 가이드](meta/integrated_work_guide_ko.md) | 진입 가이드 |
| 저장소 AI 작업·게시 규칙 | [AGENTS.md](../AGENTS.md), [프로젝트 스킬 색인](../.agents/README.md) | 작업 규칙·스킬 라우터 |
| 현재 구현, 빌드 명령, 저수준 불변식 | [CLAUDE.md](../CLAUDE.md) | 현재 구현 mirror·운영 기준 |
| 파일 위치와 의존 방향 | [PROJECT.md](../PROJECT.md) | 도메인 맵 정본 |
| 현재 전역 우선순위와 다음 작은 작업 | [성숙도 우선 작업흐름](meta/minimal_io_and_maturity_workflow_ko.md#agent-consumer-next) | 전역 작업 큐·성숙도 정본 |
| AI가 읽는 상태·가능한 행동·거부 이유 | [에이전트 운용 계약](autonomy/agent_operating_contract_ko.md) | 계약 요구와 현재 구현 경계 |
| 관리 의미와 source 결속 | [Kernel Room 관리 모델](kernel-room/kernel_room_management_model_ko.md), [H1 trace/replay](os/h1_binding_trace_replay_workplan_ko.md) | 관리 정본·분야별 계약 |
| 부팅, 고유 CLI, 하드웨어·인터넷 | [운영 이미지](os/aios_operating_image_guide_ko.md), [CLI·인터넷](os/aios_cli_internet_guide_ko.md) | 실행·검증 가이드; Linux-hosted `PARTIAL` |
| 실제 모델·서비스·자원 관계 | [MAIN](os/aios_agent_binding_guide_ko.md), [backend](os/aios_backend_lifecycle_guide_ko.md), [Cell](os/aios_cell_lifecycle_guide_ko.md), [자원](os/aios_resource_observation_guide_ko.md) | 각 계약·증거의 소유 가이드 |
| MAIN 환경 문맥과 UUID Task의 접수·조회·결과·취소 | [환경 문맥](os/aios_space_context_guide_ko.md) | v0.10 `PARTIAL`; Windows 전체·Linux fixture 결과와 한정 실제 모델 TaskSmoke PASS·미완료 image39를 구분 |
| 검증 판정과 실제 명령 | [검증 도구 진화 설계](tools/verification_tooling_evolution_design_ko.md), [Testkit 가이드](tools/testkit_guide_ko.md) | 검증 정본·운영 가이드 |
| 문서 검토 범위와 다시 확인할 조건 | [문서 신선도 원장](meta/document_freshness_registry_ko.md) | 사람용 검토 메타데이터 |

## 문서 역할과 수명주기

- 문서 역할은 `진입 가이드`, `정본`, `운영 가이드`, `작업 준비서`, `참고`,
  `역사 기록`으로 구분한다.
- 문서 수명주기는 `활성`, `REVIEW`, `OLD`로 구분한다. `REVIEW`는 현재 정본과
  대조해서 사용하고, `OLD`는 역사적 맥락에만 사용한다.
- 구현 성숙도인 `CURRENT`, `PARTIAL`, `SCAFFOLD`, `PLANNED`는 문서 수명주기와
  별개다. `RESEARCH`는 선택적 연구 트랙이며 구현 완료를 뜻하지 않는다.
- 새 문서와 상태 변경은 문서 상단·이 인덱스·신선도 원장을 함께 맞춘다. 자세한 생성,
  mirror, 노후화 규칙은 [통합 작업 진입 가이드 §8](meta/integrated_work_guide_ko.md#8-문서-관리-규칙)을 따른다.
- 아래 목록에서 `정본`, `운영 가이드`, `작업 준비서`로 명시하지 않은 기존 문서는
  기본적으로 분야별 `참고`로 취급한다. 제목에 “계획”, “방향”, “다음”이 있어도
  현재 구현 성숙도나 전역 작업 큐를 단독으로 결정하지 않는다.

## 문서를 현재 작업에 쓰는 기준

- 최신 구현 여부는 [CLAUDE.md](../CLAUDE.md)와 현재 코드·public header·verifier·정규
  artifact를 함께 확인한다. 이 인덱스의 한 줄 설명은 구현 증거를 대체하지 않는다.
- 검토일 갱신은 runtime·이미지·외부 자료의 재검증이 아니다. 버전·테스트 수·원본 경로와
  source별 실행 결과는 해당 가이드에서 확인한다. 로컬 `build/` 증거는 Git 배포물이 아니다.
- M1 uaccess/SMAP, M2 static ELF64 loader 이전 상태를 전제로 한 문서는
  [OLD/REVIEW 문서 감사 기록](meta/old_docs_check_2026_07_03_ko.md)의 분류 근거를
  참고한다. 역할·수명주기가 서로 다르면 문서 상단과 이 인덱스·원장을 함께 정정한다.
- `OLD`/`REVIEW` 문서 안의 “다음”은 현재 전역 작업 큐가 아니다. 변경 영향과 외부 자료의
  30일·작업 착수 재검토 조건은 [신선도 원장](meta/document_freshness_registry_ko.md)이 관리한다.

## 전체 아키텍처
- [design.md](design.md) — `REVIEW`; native reference/proof 설계 개요. 현재 제품 정본 링크를 상단에 명시
- [architecture.mmd](architecture.mmd) / [architecture.png](architecture.png) —
  `REVIEW`; native reference/proof substrate의 역사적 상세도. Linux-hosted 기본
  delivery 구조는 아래 정책 정본의 Mermaid를 우선

## kernel/ — 커널 내부
- [hardware_core_foundation_ko.md](kernel/hardware_core_foundation_ko.md) — `REVIEW`; 초기 hardware bootstrap 기록, 현재 우선순위 아님
- [driver_model_foundation_ko.md](kernel/driver_model_foundation_ko.md)
- [memory_parallel_optimization_ko.md](kernel/memory_parallel_optimization_ko.md)
- [multi_agent_memory_fabric_foundation_ko.md](kernel/multi_agent_memory_fabric_foundation_ko.md)
- [kernel_entropy_noise_sources_ko.md](kernel/kernel_entropy_noise_sources_ko.md)
- [organic_kernel_structure_ko.md](kernel/organic_kernel_structure_ko.md)
- [code_boundary_and_structure_tree_ko.md](kernel/code_boundary_and_structure_tree_ko.md) — `REVIEW`; 초기 코드 경계 가이드, 현재 배치는 `PROJECT.md` 우선
- [kernel_user_boundary_optimization_ko.md](kernel/kernel_user_boundary_optimization_ko.md)
- [enum_and_lowlevel_slm_alignment_ko.md](kernel/enum_and_lowlevel_slm_alignment_ko.md)
- [boot_marker_notes.md](kernel/boot_marker_notes.md)

### kernel-room (관리 계층 정본)
- [kernel-room/README.md](kernel-room/README.md) — 활성 색인; 관리 계층 문서와 현재 경계
- [kernel-room/kernel_room_management_model_ko.md](kernel-room/kernel_room_management_model_ko.md) — 활성 정본; 관리 의미·권위·불변식·분야별 의존 순서
- [kernel-room/kernel_room_topology_ko.md](kernel-room/kernel_room_topology_ko.md) — 관리 정본에 종속된 hierarchy·identity·binding 개념도
- [kernel-room/development_guide_ko.md](kernel-room/development_guide_ko.md) — 활성 운영 가이드; 작은 관리 변경과 검증 규약
- [kernel-room/orbit_cell_node_feasibility_ko.md](kernel-room/orbit_cell_node_feasibility_ko.md) — 문서 `REVIEW`; Orbit 기능 방향은 `RESEARCH`, 지원 기능으로 해석하지 않음

## autonomy/ — 자율 제어 · SLM · 정책
- [agent_operating_contract_ko.md](autonomy/agent_operating_contract_ko.md) — 활성 계약; 에이전트 상태·행동·결과 요구와 구현 경계
- [autonomous_os_execution_roadmap_ko.md](autonomy/autonomous_os_execution_roadmap_ko.md) — `OLD`; ring3/K1 이전의 2026-04 실행 로드맵
- [slm_autonomous_kernel_plan.md](autonomy/slm_autonomous_kernel_plan.md)
- [slm_hardware_onboarding_ko.md](autonomy/slm_hardware_onboarding_ko.md)
- [slm_learning_optimization_ko.md](autonomy/slm_learning_optimization_ko.md)
- [static_chaos_agent_architecture_ko.md](autonomy/static_chaos_agent_architecture_ko.md)
- [ai_resource_management_development_plan_ko.md](autonomy/ai_resource_management_development_plan_ko.md) — 활성 분야별 작업 계획; 전역 순서는 성숙도 작업흐름이 소유

## os/ — 유저스페이스 OS 계층
- [linux_hosted_substrate_and_resource_policy_ko.md](os/linux_hosted_substrate_and_resource_policy_ko.md) — 활성 정본; 실행 기반 선택·source 반입 경계와 독자 관리 의미
- [h1_binding_trace_replay_workplan_ko.md](os/h1_binding_trace_replay_workplan_ko.md) — 활성 계약·작업 준비서; H1 field/lifecycle/reason, host-only replay와 보존된 acceptance 증거
- [../hosted/README.md](../hosted/README.md) — 활성 도메인 색인; Linux-hosted runtime의 책임·의존 경계와 실행 진입
- [aios_userspace_boot_hardware_guide_ko.md](os/aios_userspace_boot_hardware_guide_ko.md) — 활성 운영 가이드; 독자 AIOS·Linux 드라이버 경계와 시작 로그·하드웨어 관측
- [aios_service_lifecycle_guide_ko.md](os/aios_service_lifecycle_guide_ko.md) — 활성 운영 가이드; CLI와 분리된 CONSOLE_RUNTIME의 시작·중지·재시작·세대 증거, SUPPORTING/PARTIAL
- [aios_agent_binding_guide_ko.md](os/aios_agent_binding_guide_ko.md) — 활성 운영 가이드; 실제 MAIN 모델 요청·hosted authority 결속·재결속과 실행 증거, DIRECT/PARTIAL
- [aios_space_context_guide_ko.md](os/aios_space_context_guide_ko.md) — 활성 개발 계약·운영 가이드; MAIN의 명시적 환경 갱신·질문 문맥·신선도·실제 모델 소비 검증. v0.10 PARTIAL, 한정 실제 모델 TaskSmoke PASS와 source별 과거 기록·미완료 image39 구분
- [aios_resource_observation_guide_ko.md](os/aios_resource_observation_guide_ko.md) — MAIN/backend 명시적 관계·CPU/RSS 및 system PSI 관측의 계약과 검증, DIRECT/PARTIAL
- [aios_cell_lifecycle_guide_ko.md](os/aios_cell_lifecycle_guide_ko.md) — 활성 계약·운영 가이드; Cell 1 활성/비활성·관리 세대·명시적 재결속, DIRECT/PARTIAL
- [aios_backend_lifecycle_guide_ko.md](os/aios_backend_lifecycle_guide_ko.md) — 활성 계약·운영 가이드; backend 수명·실제 요청 대상 결속·동일 CLI recover, DIRECT/PARTIAL
- [aios_cli_internet_guide_ko.md](os/aios_cli_internet_guide_ko.md) — 활성 운영 가이드; 고유 대화형 CLI·DNS/HTTP(S)와 Linux-visible 증거, PARTIAL
- [aios_operating_image_guide_ko.md](os/aios_operating_image_guide_ko.md) — 활성 설계·acceptance 정본 및 운영 가이드; 반복 부팅·모델 사용·기본 이미지 선택/rollback·한정 장애 검증, SUPPORTING/PARTIAL. 실행 명령, 원본 FAIL 보존과 source별 증거는 본문이 소유
- [browser_console_and_runtime_engine_roadmap_ko.md](os/browser_console_and_runtime_engine_roadmap_ko.md) — 활성 분야별 설계 가이드; W축의 브라우저 전달 방식, 전역 작업 큐 아님
- [user_space_os_direction_ko.md](os/user_space_os_direction_ko.md) — `OLD`; ring3/static ELF 이전 방향 기록
- [user_space_os_build_slices_ko.md](os/user_space_os_build_slices_ko.md) — `OLD`; M1/M2 이전 빌드 계획
- [user_space_compat_architecture_ko.md](os/user_space_compat_architecture_ko.md) — `OLD`; ring3 caller 이전 compatibility 설계
- [ai_agent_autonomous_os_requirements_ko.md](os/ai_agent_autonomous_os_requirements_ko.md) — `REVIEW`; 초기 native 요구 참고, 현재 제품·운용 계약과 대조 필요

## models/ — AI 모델 스택
- [agent_model_stack_recommendations_ko.md](models/agent_model_stack_recommendations_ko.md) — `REVIEW`; 과거 모델·학습 전략 참고, 선택 전 공식 자료 재확인

## tools/ — 테스트툴 · 빌드 · 보조 도구
- [test_tooling_ko.md](tools/test_tooling_ko.md) — `OLD/REVIEW`; 초기 testkit 구조 기록
- [testkit_guide_ko.md](tools/testkit_guide_ko.md)
- [qemu_mcp_guide_ko.md](tools/qemu_mcp_guide_ko.md) — 에이전트용 QEMU MCP(qemu-mcp) 편의 도입 운영 가이드; 진단 전용 경계와 정규 lane 분리 규정
- [verification_tooling_evolution_design_ko.md](tools/verification_tooling_evolution_design_ko.md) — fail-closed verdict와 artifact의 V0~V5 정본; H1 replay 보조 레인은 generic `[EVT]` 계획과 분리
- [boot_kernel_testkit_expansion_plan_ko.md](tools/boot_kernel_testkit_expansion_plan_ko.md) — `OLD/REVIEW`; 2026-04 초기 확장 기록
- [windows_build.md](tools/windows_build.md)
- [gemini_cli_usage_strategy_ko.md](tools/gemini_cli_usage_strategy_ko.md)
- [gemini_cli_first_review_ko.md](tools/gemini_cli_first_review_ko.md) — `OLD`; 초기 외부 리뷰 기록
- [gemini_driver_userspace_checkpoint_ko.md](tools/gemini_driver_userspace_checkpoint_ko.md) — `OLD`; M1/M2 이전 점검 기록

## meta/ — 점검 보고서 · 로드맵 · 외부 사례 · 릴리스
- [aios_product_direction_ko.md](meta/aios_product_direction_ko.md) — 활성 제품 방향 정본; 로컬 효율·작업 공간·지속 상호작용의 의미와 성공 기준
- [integrated_work_guide_ko.md](meta/integrated_work_guide_ko.md) — 저장소 전체 작업 진입·라우팅과 문서 관리 가이드
- [document_freshness_registry_ko.md](meta/document_freshness_registry_ko.md) — 활성 검토 원장; 주요 문서의 역할·검토 범위·재검토 조건·담당 분야
- [maturity_levers_backlog_ko.md](meta/maturity_levers_backlog_ko.md) — 활성 지원 문서; SUPPORTING/ORTHOGONAL 품질 후보와 역사적 진단, 전역 큐 아님
- [codex_handoff_tips_ko.md](meta/codex_handoff_tips_ko.md)
- [aios_build_project_landscape_2026_08_03_ko.md](meta/aios_build_project_landscape_2026_08_03_ko.md) — `REVIEW/RESEARCH`; 2026-08 외부 프로젝트 조사 스냅샷
- [old_docs_check_2026_07_03_ko.md](meta/old_docs_check_2026_07_03_ko.md) — `REVIEW`; 과거 분류 근거를 보존하는 문서 감사 기록
- [minimal_io_and_maturity_workflow_ko.md](meta/minimal_io_and_maturity_workflow_ko.md) — 활성 정본; 유일한 전역 작업 큐와 기술축 성숙도
- [hardening_baseline_2026_07_02_ko.md](meta/hardening_baseline_2026_07_02_ko.md) — OLD/REVIEW historical baseline; current SMAP/entry-AC status is in the maturity workflow and handoff notes
- [inspection_report_2026_04_15.md](meta/inspection_report_2026_04_15.md) — 역사 기록
- [inspection_report_2026_03_30.md](meta/inspection_report_2026_03_30.md) — 역사 기록
- [inspection_and_gaps_ko.md](meta/inspection_and_gaps_ko.md) — 역사적 점검 참고
- [current_kernel_gap_report_ko.md](meta/current_kernel_gap_report_ko.md) — `OLD`; M1/M2 이전 gap report
- [commercial_stability_baseline_ko.md](meta/commercial_stability_baseline_ko.md) — `REVIEW`; 2026-04 QEMU health/driver checkpoint
- [ai_native_os_github_landscape_ko.md](meta/ai_native_os_github_landscape_ko.md) — 2026-04-21 역사적 조사(`OLD`)
- [release_notes_v0.2.0_beta.6.md](meta/release_notes_v0.2.0_beta.6.md) — 릴리스 역사 기록
