# AIOS 설계 문서 인덱스

문서를 도메인별로 나눠 정리했다. 도메인 경계와 의존 규칙은 저장소 루트의
[PROJECT.md](../PROJECT.md)를 먼저 참고할 것.

## 전체 아키텍처
- [design.md](design.md) — 전체 설계 개요
- [architecture.mmd](architecture.mmd) / [architecture.png](architecture.png) — 아키텍처 다이어그램

## kernel/ — 커널 내부
- [hardware_core_foundation_ko.md](kernel/hardware_core_foundation_ko.md)
- [driver_model_foundation_ko.md](kernel/driver_model_foundation_ko.md)
- [memory_parallel_optimization_ko.md](kernel/memory_parallel_optimization_ko.md)
- [multi_agent_memory_fabric_foundation_ko.md](kernel/multi_agent_memory_fabric_foundation_ko.md)
- [kernel_entropy_noise_sources_ko.md](kernel/kernel_entropy_noise_sources_ko.md)
- [organic_kernel_structure_ko.md](kernel/organic_kernel_structure_ko.md)
- [code_boundary_and_structure_tree_ko.md](kernel/code_boundary_and_structure_tree_ko.md)
- [kernel_user_boundary_optimization_ko.md](kernel/kernel_user_boundary_optimization_ko.md)
- [enum_and_lowlevel_slm_alignment_ko.md](kernel/enum_and_lowlevel_slm_alignment_ko.md)
- [boot_marker_notes.md](kernel/boot_marker_notes.md)

### kernel-room (토폴로지)
- [kernel-room/README.md](kernel-room/README.md)
- [kernel-room/kernel_room_topology_ko.md](kernel-room/kernel_room_topology_ko.md)
- [kernel-room/development_guide_ko.md](kernel-room/development_guide_ko.md)
- [kernel-room/orbit_cell_node_feasibility_ko.md](kernel-room/orbit_cell_node_feasibility_ko.md)

## autonomy/ — 자율 제어 · SLM · 정책
- [autonomous_os_execution_roadmap_ko.md](autonomy/autonomous_os_execution_roadmap_ko.md)
- [slm_autonomous_kernel_plan.md](autonomy/slm_autonomous_kernel_plan.md)
- [slm_hardware_onboarding_ko.md](autonomy/slm_hardware_onboarding_ko.md)
- [slm_learning_optimization_ko.md](autonomy/slm_learning_optimization_ko.md)
- [static_chaos_agent_architecture_ko.md](autonomy/static_chaos_agent_architecture_ko.md)
- [ai_resource_management_development_plan_ko.md](autonomy/ai_resource_management_development_plan_ko.md)

## os/ — 유저스페이스 OS 계층
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
- [minimal_io_and_maturity_workflow_ko.md](meta/minimal_io_and_maturity_workflow_ko.md)
- [hardening_baseline_2026_07_02_ko.md](meta/hardening_baseline_2026_07_02_ko.md)
- [inspection_report_2026_04_15.md](meta/inspection_report_2026_04_15.md)
- [inspection_report_2026_03_30.md](meta/inspection_report_2026_03_30.md)
- [inspection_and_gaps_ko.md](meta/inspection_and_gaps_ko.md)
- [current_kernel_gap_report_ko.md](meta/current_kernel_gap_report_ko.md)
- [commercial_stability_baseline_ko.md](meta/commercial_stability_baseline_ko.md)
- [ai_native_os_github_landscape_ko.md](meta/ai_native_os_github_landscape_ko.md)
- [release_notes_v0.2.0_beta.6.md](meta/release_notes_v0.2.0_beta.6.md)
