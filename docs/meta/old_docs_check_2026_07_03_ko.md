# OLD 문서 체크리스트 (2026-07-03)

이 문서는 현재 구현 기준보다 뒤처진 문서를 `OLD` 또는 `REVIEW`로 분류해 둔 체크리스트다.
파일 이동은 하지 않는다. 기존 링크를 유지하되, 새 작업자는 먼저 최신 기준 문서를 읽고 아래 문서는
역사적 맥락이나 이전 판단을 확인할 때만 참고한다.

## 최신 기준

- [CLAUDE.md](../../CLAUDE.md): 현재 빌드/테스트 명령, ring3/ELF/uaccess/SMAP 상태, 불변식.
- [minimal_io_and_maturity_workflow_ko.md](minimal_io_and_maturity_workflow_ko.md): M1 uaccess/SMAP, M2 static ELF64 loader 완료와 M3/M4 다음 작업흐름.
- [tools/testkit/README.md](../../tools/testkit/README.md): testkit lane과 산출물.
- [testkit_guide_ko.md](../tools/testkit_guide_ko.md): testkit 세부 사용법.

## OLD로 체크한 문서

| 체크 | 문서 | 이유 | 대체 기준 |
|---|---|---|---|
| [x] OLD | [user_space_os_build_slices_ko.md](../os/user_space_os_build_slices_ko.md) | ring3 handoff, static ELF loader, 일부 uaccess 상태가 M1/M2 이전 기준으로 적혀 있음 | `minimal_io_and_maturity_workflow_ko.md` |
| [x] OLD | [user_space_os_direction_ko.md](../os/user_space_os_direction_ko.md) | 실제 ring3 handoff와 ELF loader가 아직 없다고 설명하는 구간이 남아 있음 | `CLAUDE.md`, `minimal_io_and_maturity_workflow_ko.md` |
| [x] OLD | [user_space_compat_architecture_ko.md](../os/user_space_compat_architecture_ko.md) | ring3 caller가 없다는 전제에서 작성된 compatibility 설계 문서 | `CLAUDE.md`의 Ring3 Execution |
| [x] OLD | [current_kernel_gap_report_ko.md](current_kernel_gap_report_ko.md) | 상단 갱신 주석은 있으나 본문은 ring3/syscall/ELF 부재 전제가 남아 있음 | `minimal_io_and_maturity_workflow_ko.md` |
| [x] OLD | [ai_native_os_github_landscape_ko.md](ai_native_os_github_landscape_ko.md) | GitHub landscape 비교 중 현재 AIOS 상태 설명이 M1/M2 이전임 | `CLAUDE.md`, `minimal_io_and_maturity_workflow_ko.md` |
| [x] OLD | [gemini_driver_userspace_checkpoint_ko.md](../tools/gemini_driver_userspace_checkpoint_ko.md) | Gemini 점검 당시의 ring3 scaffold 이전 체크포인트 문서 | `minimal_io_and_maturity_workflow_ko.md` |
| [x] OLD | [gemini_cli_first_review_ko.md](../tools/gemini_cli_first_review_ko.md) | 첫 리뷰 기록으로, ring3/TSS/syscall/ELF 부재를 전제로 함 | `CLAUDE.md`, 최신 testkit 결과 |

## REVIEW로 체크한 문서

| 체크 | 문서 | 이유 |
|---|---|---|
| [x] REVIEW | [ai_resource_management_development_plan_ko.md](../autonomy/ai_resource_management_development_plan_ko.md) | resource/service 계획 자체는 유효하지만 ring3 이후 전제 문장이 일부 낡았다. |
| [x] REVIEW | [code_boundary_and_structure_tree_ko.md](../kernel/code_boundary_and_structure_tree_ko.md) | 디렉토리 경계 설명은 유효하나 ring3 이전 표현이 일부 남아 있다. |
| [x] REVIEW | [orbit_cell_node_feasibility_ko.md](../kernel-room/orbit_cell_node_feasibility_ko.md) | 독립 userspace node/process mesh 부재 설명은 여전히 대체로 맞지만, "ring3 scaffold" 표현은 첫 ring3 slice 완료 이후 재검토가 필요하다. |

## Claude/testkit 명령 확인

Claude가 주로 참고하는 테스트 진입점은 [CLAUDE.md](../../CLAUDE.md)의 `Build & Test Commands`와 `tools/testkit`이다.

권장 명령:

```powershell
python .\tools\testkit\aios-testkit.py all --strict
python .\tools\testkit\aios-testkit.py kernel --target test --strict
python .\tools\testkit\aios-testkit.py boot-matrix --profiles full minimal storage-only --strict
python .\tools\testkit\aios-testkit.py boot-inventory --profiles full minimal storage-only --strict
python .\tools\testkit\aios-testkit.py boot-perf --profiles full minimal storage-only --strict
python .\tools\testkit\aios-testkit.py shell --strict
python .\tools\testkit\aios-testkit.py shell --strict --skip-build
python .\tools\testkit\aios-testkit.py os
pwsh -File .\tools\testkit\kernel\build-windows.ps1 -Target test
pwsh -File .\tools\testkit\kernel\build-windows.ps1 -Target test -SmokeProfile minimal
```

현재 Codex Windows 환경에서는 `python`이 Windows Store alias로 잡힐 수 있다. 그 경우 `py` 또는 Codex 번들 Python 경로를 사용한다.

확인한 testkit 구조:

- `kernel` lane: `make all`/`make iso` 또는 Windows `build-windows.ps1`를 통해 ISO를 만들고 QEMU smoke 로그 패턴을 검사한다.
- `boot-matrix`: `full`, `minimal`, `storage-only` 프로파일을 순차 실행하고 summary JSON을 만든다.
- `boot-inventory`: boot matrix 결과를 compact inventory로 baseline과 비교한다.
- `boot-perf`: serial log의 selftest/perf 값을 host-local baseline과 비교한다.
- `shell`: QEMU `-serial stdio`로 `ping`, `state list`, `state health`, `state mem`, `state pipeline`, `state nodes`, `state slm`, `state user`, `state sec`, `state time`, `state version`, `state bogus`를 검증한다.
- `os`: `os/tools` 샘플 기반 smoke를 실행하고 `kernel/build/tool-smoke/`에 결과를 남긴다.

추가로 `Z:\aios\.claude\settings.local.json`에는 Claude 허용 명령 일부가 남아 있다:

```text
Bash(wc *)
Bash(ls -la /m/aios/aios-kernel/os/tools/*.py)
Bash(xargs grep *)
```

## GitHub 원격 페이지 점검

점검일: 2026-07-03.

- GitHub 원격 저장소: `https://github.com/tjwlstj/aios-kernel`
- `beta` 브랜치의 `CLAUDE.md`에는 Linux/MSYS2, Windows PowerShell, Python testkit 명령이 명시되어 있다.
- `beta` 브랜치의 `tools/testkit/README.md`에도 `kernel`, `boot-matrix`, `boot-inventory`, `boot-perf`, `os`, `all`, Windows `build-windows.ps1` 명령이 정리되어 있다.
- GitHub Actions의 `Linux Boot Check`는 `python tools/testkit/aios-testkit.py os`, `python tools/testkit/aios-testkit.py all --strict`를 실행한다. 최신 `beta` 로컬 워크플로는 여기에 `cppcheck`와 `shell --strict --skip-build`까지 포함한다.
- 기본 GitHub repo 페이지에 보이는 README는 브랜치/캐시/기본 브랜치 상태에 따라 오래된 `scripts/build-windows.ps1`, `scripts/aios-allinone.py` 경로가 보일 수 있다. 새 기준은 `tools/testkit/` 경로다.
- 이 체크 문서와 OLD 배너는 아직 로컬 변경사항이다. GitHub에 보이려면 커밋/푸시가 필요하다.
