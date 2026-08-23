# AIOS 설계 문서 인덱스

이 파일은 `docs/` 아래 문서의 **단일 탐색·수명주기 원장**이다. 작업 절차와
정본 선택은 [통합 작업 진입 가이드](meta/integrated_work_guide_ko.md), 도메인 경계와
의존 규칙은 [PROJECT.md](../PROJECT.md)를 따른다.

## 작업 시작

| 필요한 것 | 먼저 읽을 문서 | 문서 역할 |
|---|---|---|
| 요청 분류, 정본·스킬·검증 선택 | [통합 작업 진입 가이드](meta/integrated_work_guide_ko.md) | 진입 가이드 |
| 저장소 AI 작업·게시 규칙 | [AGENTS.md](../AGENTS.md), [프로젝트 스킬 색인](../.agents/README.md) | 작업 규칙·스킬 라우터 |
| 현재 구현, 빌드 명령, 저수준 불변식 | [CLAUDE.md](../CLAUDE.md) | 현재 구현 mirror·운영 기준 |
| 파일 위치와 의존 방향 | [PROJECT.md](../PROJECT.md) | 도메인 맵 정본 |
| 제품 축과 전역 우선순위 | [성숙도 우선 작업흐름](meta/minimal_io_and_maturity_workflow_ko.md) | roadmap 정본 |
| 현재 bounded H1 계약 | [H1 trace/replay 작업 준비서](os/h1_binding_trace_replay_workplan_ko.md) | 작업 준비서; H1-a transport 조각 `PARTIAL`, lifecycle replay 진행 전 |
| 검증 판정과 실제 명령 | [검증 도구 진화 설계](tools/verification_tooling_evolution_design_ko.md), [Testkit 가이드](tools/testkit_guide_ko.md) | 검증 정본·운영 가이드 |

## 문서 역할과 수명주기

- 문서 역할은 `진입 가이드`, `정본`, `운영 가이드`, `작업 준비서`, `참고`,
  `역사 기록`으로 구분한다.
- 문서 수명주기는 `활성`, `REVIEW`, `OLD`로 구분한다. `REVIEW`는 현재 정본과
  대조해서 사용하고, `OLD`는 역사적 맥락에만 사용한다.
- 구현 성숙도인 `CURRENT`, `PARTIAL`, `SCAFFOLD`, `PLANNED`는 문서 수명주기와
  별개다. `RESEARCH`는 선택적 연구 트랙이며 구현 완료를 뜻하지 않는다.
- 새 문서와 상태 변경은 이 인덱스에 역할·수명주기를 함께 반영한다. 자세한 생성,
  mirror, 노후화 규칙은 통합 작업 진입 가이드 §8을 따른다.
- 아래 목록에서 `정본`, `운영 가이드`, `작업 준비서`로 명시하지 않은 기존 문서는
  기본적으로 분야별 `참고`로 취급한다. 제목에 “계획”, “방향”, “다음”이 있어도
  현재 구현 성숙도나 전역 작업 큐를 단독으로 결정하지 않는다.

## 문서 신선도 기준

- 최신 구현 여부는 [CLAUDE.md](../CLAUDE.md)와 현재 코드·public header·verifier·정규
  artifact를 함께 확인한다. 이 인덱스의 한 줄 설명은 구현 증거를 대체하지 않는다.
- 제품 관리 구조의 정본은 [Kernel Room 문서 허브](kernel-room/README.md)와
  [관리 모델](kernel-room/kernel_room_management_model_ko.md), 전역 순서는
  [성숙도 작업흐름](meta/minimal_io_and_maturity_workflow_ko.md)이 소유한다.
- M1 uaccess/SMAP, M2 static ELF64 loader 이전 상태를 전제로 한 문서는
  [OLD/REVIEW 문서 감사 기록](meta/old_docs_check_2026_07_03_ko.md)의 분류 근거를
  참고한다. 현재 수명주기 표시는 이 인덱스를 우선한다.
- `OLD`/`REVIEW` 문서 안의 “다음”은 현재 전역 작업 큐가 아니다.

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
- [kernel-room/README.md](kernel-room/README.md) — Room→Cell→Node→NodeBit 문서 허브와 성숙도 경계
- [kernel-room/kernel_room_management_model_ko.md](kernel-room/kernel_room_management_model_ko.md) — 관리 권위·용어·불변식·첫 hierarchy vertical slice의 정본
- [kernel-room/kernel_room_topology_ko.md](kernel-room/kernel_room_topology_ko.md) — canonical hierarchy, identity/binding 설계
- [kernel-room/development_guide_ko.md](kernel-room/development_guide_ko.md) — 관리축의 작은 vertical slice와 검증 규약
- [kernel-room/orbit_cell_node_feasibility_ko.md](kernel-room/orbit_cell_node_feasibility_ko.md) — 문서 `REVIEW`; Orbit 기능 방향은 `RESEARCH`, 지원 기능으로 해석하지 않음

## autonomy/ — 자율 제어 · SLM · 정책
- [agent_operating_contract_ko.md](autonomy/agent_operating_contract_ko.md)
- [autonomous_os_execution_roadmap_ko.md](autonomy/autonomous_os_execution_roadmap_ko.md) — `OLD`; ring3/K1 이전의 2026-04 실행 로드맵
- [slm_autonomous_kernel_plan.md](autonomy/slm_autonomous_kernel_plan.md)
- [slm_hardware_onboarding_ko.md](autonomy/slm_hardware_onboarding_ko.md)
- [slm_learning_optimization_ko.md](autonomy/slm_learning_optimization_ko.md)
- [static_chaos_agent_architecture_ko.md](autonomy/static_chaos_agent_architecture_ko.md)
- [ai_resource_management_development_plan_ko.md](autonomy/ai_resource_management_development_plan_ko.md) — 활성 분야별 작업 계획; 전역 순서는 성숙도 작업흐름이 소유

## os/ — 유저스페이스 OS 계층
- [linux_hosted_substrate_and_resource_policy_ko.md](os/linux_hosted_substrate_and_resource_policy_ko.md) — Linux-hosted userspace service를 기본 delivery 방향으로 고정하고 Kernel Room 의미를 보존하는 정본; resource catalog `CURRENT`와 hosted backend `PLANNED`를 분리
- [h1_binding_trace_replay_workplan_ko.md](os/h1_binding_trace_replay_workplan_ko.md) — native K2-a 다음 H1의 OS-neutral field, lifecycle, reason, fixture, 검증과 1~2주 구현 게이트; H1-a transport 조각 `PARTIAL`, lifecycle replay(H1-b) 진행 전
- [../hosted/README.md](../hosted/README.md) — Linux-hosted 제품 도메인의 책임·의존 경계; 실행 구현은 `PLANNED`
- [browser_console_and_runtime_engine_roadmap_ko.md](os/browser_console_and_runtime_engine_roadmap_ko.md)
- [user_space_os_direction_ko.md](os/user_space_os_direction_ko.md) — `OLD`; ring3/static ELF 이전 방향 기록
- [user_space_os_build_slices_ko.md](os/user_space_os_build_slices_ko.md) — `OLD`; M1/M2 이전 빌드 계획
- [user_space_compat_architecture_ko.md](os/user_space_compat_architecture_ko.md) — `OLD`; ring3 caller 이전 compatibility 설계
- [ai_agent_autonomous_os_requirements_ko.md](os/ai_agent_autonomous_os_requirements_ko.md)

## models/ — AI 모델 스택
- [agent_model_stack_recommendations_ko.md](models/agent_model_stack_recommendations_ko.md)

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
- [integrated_work_guide_ko.md](meta/integrated_work_guide_ko.md) — 저장소 전체 작업 진입·라우팅과 문서 관리 가이드
- [maturity_levers_backlog_ko.md](meta/maturity_levers_backlog_ko.md)
- [codex_handoff_tips_ko.md](meta/codex_handoff_tips_ko.md)
- [aios_build_project_landscape_2026_08_03_ko.md](meta/aios_build_project_landscape_2026_08_03_ko.md) — `REVIEW/RESEARCH`; 2026-08 외부 프로젝트 조사 스냅샷
- [old_docs_check_2026_07_03_ko.md](meta/old_docs_check_2026_07_03_ko.md) — `REVIEW`; 과거 분류 근거를 보존하는 문서 감사 기록
- [minimal_io_and_maturity_workflow_ko.md](meta/minimal_io_and_maturity_workflow_ko.md)
- [hardening_baseline_2026_07_02_ko.md](meta/hardening_baseline_2026_07_02_ko.md) — OLD/REVIEW historical baseline; current SMAP/entry-AC status is in the maturity workflow and handoff notes
- [inspection_report_2026_04_15.md](meta/inspection_report_2026_04_15.md) — 역사 기록
- [inspection_report_2026_03_30.md](meta/inspection_report_2026_03_30.md) — 역사 기록
- [inspection_and_gaps_ko.md](meta/inspection_and_gaps_ko.md) — 역사적 점검 참고
- [current_kernel_gap_report_ko.md](meta/current_kernel_gap_report_ko.md) — `OLD`; M1/M2 이전 gap report
- [commercial_stability_baseline_ko.md](meta/commercial_stability_baseline_ko.md) — `REVIEW`; 2026-04 QEMU health/driver checkpoint
- [ai_native_os_github_landscape_ko.md](meta/ai_native_os_github_landscape_ko.md) — 2026-04-21 역사적 조사(`OLD`)
- [release_notes_v0.2.0_beta.6.md](meta/release_notes_v0.2.0_beta.6.md) — 릴리스 역사 기록
