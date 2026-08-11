# AIOS 설계 문서 인덱스

문서를 도메인별로 나눠 정리했다. 도메인 경계와 의존 규칙은 저장소 루트의
[PROJECT.md](../PROJECT.md)를 먼저 참고할 것.

## 문서 신선도 기준

- 최신 구현 기준은 [CLAUDE.md](../CLAUDE.md),
  [Kernel Room 문서 허브](kernel-room/README.md),
  [minimal_io_and_maturity_workflow_ko.md](meta/minimal_io_and_maturity_workflow_ko.md)를 우선한다.
- 제품 관리 구조의 정본은 **Room→Cell→Node→NodeBit**다. aggregate snapshot과
  syscall-range 분류에 더해, bounded bootstrap Cell/Node/NodeBit registry와 그 내부
  parent binding인 K1 v0가 `CURRENT`다. 전체 topology는 계속 `PARTIAL`이며 external
  source binding, live lifecycle/reconciliation, 정책·권한 적용은 `PLANNED`다.
- M1 uaccess/SMAP, M2 static ELF64 loader 이전 상태를 전제로 한 문서는
  [OLD 문서 체크리스트](meta/old_docs_check_2026_07_03_ko.md)에 따로 표시했다.
- `OLD` 문서는 역사적 맥락용이며, 새 구현 판단에는 최신 기준 문서를 먼저 사용한다.

## 전체 아키텍처
- [design.md](design.md) — 전체 설계 개요
- [architecture.mmd](architecture.mmd) / [architecture.png](architecture.png) — 아키텍처 다이어그램

## kernel/ — 커널 내부
- [hardware_core_foundation_ko.md](kernel/hardware_core_foundation_ko.md) — `REVIEW`; 초기 hardware bootstrap 기록, 현재 우선순위 아님
- [driver_model_foundation_ko.md](kernel/driver_model_foundation_ko.md)
- [memory_parallel_optimization_ko.md](kernel/memory_parallel_optimization_ko.md)
- [multi_agent_memory_fabric_foundation_ko.md](kernel/multi_agent_memory_fabric_foundation_ko.md)
- [kernel_entropy_noise_sources_ko.md](kernel/kernel_entropy_noise_sources_ko.md)
- [organic_kernel_structure_ko.md](kernel/organic_kernel_structure_ko.md)
- [code_boundary_and_structure_tree_ko.md](kernel/code_boundary_and_structure_tree_ko.md)
- [kernel_user_boundary_optimization_ko.md](kernel/kernel_user_boundary_optimization_ko.md)
- [enum_and_lowlevel_slm_alignment_ko.md](kernel/enum_and_lowlevel_slm_alignment_ko.md)
- [boot_marker_notes.md](kernel/boot_marker_notes.md)

### kernel-room (관리 계층 정본)
- [kernel-room/README.md](kernel-room/README.md) — Room→Cell→Node→NodeBit 문서 허브와 성숙도 경계
- [kernel-room/kernel_room_management_model_ko.md](kernel-room/kernel_room_management_model_ko.md) — 관리 권위·용어·불변식·첫 hierarchy vertical slice의 정본
- [kernel-room/kernel_room_topology_ko.md](kernel-room/kernel_room_topology_ko.md) — canonical hierarchy, identity/binding 설계
- [kernel-room/development_guide_ko.md](kernel-room/development_guide_ko.md) — 관리축의 작은 vertical slice와 검증 규약
- [kernel-room/orbit_cell_node_feasibility_ko.md](kernel-room/orbit_cell_node_feasibility_ko.md) — Orbit `RESEARCH`; 지원 기능으로 해석하지 않음

## autonomy/ — 자율 제어 · SLM · 정책
- [agent_operating_contract_ko.md](autonomy/agent_operating_contract_ko.md)
- [autonomous_os_execution_roadmap_ko.md](autonomy/autonomous_os_execution_roadmap_ko.md) — `OLD`; ring3/K1 이전의 2026-04 실행 로드맵
- [slm_autonomous_kernel_plan.md](autonomy/slm_autonomous_kernel_plan.md)
- [slm_hardware_onboarding_ko.md](autonomy/slm_hardware_onboarding_ko.md)
- [slm_learning_optimization_ko.md](autonomy/slm_learning_optimization_ko.md)
- [static_chaos_agent_architecture_ko.md](autonomy/static_chaos_agent_architecture_ko.md)
- [ai_resource_management_development_plan_ko.md](autonomy/ai_resource_management_development_plan_ko.md)

## os/ — 유저스페이스 OS 계층
- [linux_hosted_substrate_and_resource_policy_ko.md](os/linux_hosted_substrate_and_resource_policy_ko.md) — schema v1 upstream resource policy와 Kernel Room 의미를 보존하는 Linux-hosted substrate 경계의 정본; resource catalog `CURRENT`와 hosted backend `PLANNED`를 분리
- [browser_console_and_runtime_engine_roadmap_ko.md](os/browser_console_and_runtime_engine_roadmap_ko.md)
- [user_space_os_direction_ko.md](os/user_space_os_direction_ko.md)
- [user_space_os_build_slices_ko.md](os/user_space_os_build_slices_ko.md)
- [user_space_compat_architecture_ko.md](os/user_space_compat_architecture_ko.md)
- [ai_agent_autonomous_os_requirements_ko.md](os/ai_agent_autonomous_os_requirements_ko.md)

## models/ — AI 모델 스택
- [agent_model_stack_recommendations_ko.md](models/agent_model_stack_recommendations_ko.md)

## tools/ — 테스트툴 · 빌드 · 보조 도구
- [test_tooling_ko.md](tools/test_tooling_ko.md)
- [testkit_guide_ko.md](tools/testkit_guide_ko.md)
- [boot_kernel_testkit_expansion_plan_ko.md](tools/boot_kernel_testkit_expansion_plan_ko.md)
- [windows_build.md](tools/windows_build.md)
- [gemini_cli_usage_strategy_ko.md](tools/gemini_cli_usage_strategy_ko.md)
- [gemini_cli_first_review_ko.md](tools/gemini_cli_first_review_ko.md)
- [gemini_driver_userspace_checkpoint_ko.md](tools/gemini_driver_userspace_checkpoint_ko.md)

## meta/ — 점검 보고서 · 로드맵 · 외부 사례 · 릴리스
- [maturity_levers_backlog_ko.md](meta/maturity_levers_backlog_ko.md)
- [codex_handoff_tips_ko.md](meta/codex_handoff_tips_ko.md)
- [aios_build_project_landscape_2026_08_03_ko.md](meta/aios_build_project_landscape_2026_08_03_ko.md) — 현재 성숙도에 맞춘 OS·runtime·브라우저 실행 프로젝트 최신 조사
- [old_docs_check_2026_07_03_ko.md](meta/old_docs_check_2026_07_03_ko.md)
- [minimal_io_and_maturity_workflow_ko.md](meta/minimal_io_and_maturity_workflow_ko.md)
- [hardening_baseline_2026_07_02_ko.md](meta/hardening_baseline_2026_07_02_ko.md) — OLD/REVIEW historical baseline; current SMAP/entry-AC status is in the maturity workflow and handoff notes
- [inspection_report_2026_04_15.md](meta/inspection_report_2026_04_15.md)
- [inspection_report_2026_03_30.md](meta/inspection_report_2026_03_30.md)
- [inspection_and_gaps_ko.md](meta/inspection_and_gaps_ko.md)
- [current_kernel_gap_report_ko.md](meta/current_kernel_gap_report_ko.md)
- [commercial_stability_baseline_ko.md](meta/commercial_stability_baseline_ko.md) — `REVIEW`; 2026-04 QEMU health/driver checkpoint
- [ai_native_os_github_landscape_ko.md](meta/ai_native_os_github_landscape_ko.md) — 2026-04-21 역사적 조사(`OLD`)
- [release_notes_v0.2.0_beta.6.md](meta/release_notes_v0.2.0_beta.6.md)
