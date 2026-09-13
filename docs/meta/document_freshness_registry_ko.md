# AIOS 문서 신선도 원장

> 문서 역할: 사람용 내용 검토·재검토 조건 원장
> 문서 수명주기: 활성
> 마지막 내용 검토: 2026-09-13 — v0.10 증거 보존·자기 참조 실증 절차와 전역 큐·mirror 대조
> 이번 검토 경계: 아래 추가 검토 설명과 해당 행의 범위만 갱신했다. 2026-09-09의 문서 역할 정비·당시 v0.8 소스 대조와 외부 기준일은 보존하며 실제 모델 Task·새 이미지 실행 판정과 구분한다.

이 원장은 **어떤 사실을 어느 문서에서 확인하고, 언제 다시 확인할지**를 관리한다.
[문서 색인](../README.md)은 탐색, [통합 작업 진입 가이드](integrated_work_guide_ko.md)는
요청별 작업 절차를 소유한다. 제품 목적은 [제품 방향 정본](aios_product_direction_ko.md),
현재 전역 작업 큐는 [성숙도 작업흐름](minimal_io_and_maturity_workflow_ko.md)이 소유한다.
이 표의 재검토 후속은 문서 확인 조건이며 새 구현 우선순위가 아니다.

2026-09-13 추가 내용 검토: 이 체크포인트의 개발 소스 v0.10의 문맥·접수·조회·결과·취소 계약과
보존된 결과, 새 Windows hosted 전체 990개 수집·85개 제외 및 Linux Task fixture 3개
PASS를 대조했다. 검사 대상 runtime·계약·도구 소스는 150개이며 Python 파일만의 수가 아니다.
실제 모델 `task-model-smoke-01`은 정상 답변·진행 중 Task 취소·worker/전체 backend 종료와
정상 VM 종료의 한정 범위에서 PASS다. 보존 source의 독립 재생도 PASS이며 원본 artifact 222개와
runtime 39개·검사 대상 source 150개의 불변·일치 범위를 확인했다. 새 이미지 acceptance와
외부 자료 재검토는 수행하지 않았다. 아래 해당 행만 갱신하며 과거 source와 외부 기준일은 유지한다.

같은 날 추가한 자기 참조 실증 검토는 사용자가 승인한 반복 절차와 전체 다섯 단계 수락
기준, 첫 Windows CPU sandbox pilot의 6개 scenario·4필드 출력·독립 검증·CI 구성의
소스 대조다. 이어 첫 pilot의 명시적 중단·FAIL/NOT_EVALUABLE, 원본 409개 불변과
규칙 실행 79개/6 episode/27 decision 독립 재생, derived attribution 입력 제거와
normal 3 episode calibration 선행을 대조했다. 별도 2호출 prompt probe의 행동/귀속
분리도 확인했다. calibration-01 원본은 3/3 episode·24모델응답·무결성 PASS지만 모델
목표 완료 0이다. 보존된 v2 이전 scorer의 calibration 249파일/3episode/28decision/24query와
rules-02 79파일/6episode/27decision 재생은 PASS, 원본 328개는 불변이다.
GPT의 공개 피드백 예측 credit 지적과 실제 파일 14 World 재현의 영향 범위를
대조했다. 이어 validation-02의 134개/132 PASS·2 skip·104.096초, source별 검사 범위와
grammar probe의 6응답/파일 행동 0·ON 형식/귀속 오류를 확인했다. 최종 validation-03은
135개/133 PASS·2 skip·106.369초이며 grammar probe 독립 감사 78파일 PASS도 대조했다.
calibration-02 ON 재생은 250파일/3episode/28decision/24query PASS지만 모델 목표는
양쪽 모두 0이다. OFF는 22HTTP 응답 중 마지막 생성 미완료로 FAIL/NOT_EVALUABLE,
관계 episode 미완료다. 원본 479개 불변·조건 대조 PASS와 별개로 전체 ON/OFF 비교는
NOT_EVALUABLE이며 효과 비교를 수행하지 않았다. 마지막 실패 응답 포함 비용과 게시 전
grammar 끝 LF 정리의 byte 경계, 새 코드의 rules-04 독립 재생 PASS를 정본 §6과 대조했다.
개발 구현은 `PARTIAL`이고 다음 prompt 실험은 PLANNED다. 새 CI terminal·게시 검증과
장기 목표는 별도이며 외부 연구의 현재 사실을 확인한 것으로 기록하지 않는다.

## 1. 읽는 방법

- **역할·수명주기**는 각 문서의 상단과 색인을 대조한 값이다. 수명주기 `활성`은
  현재 계약이나 절차로 사용하는 문서, `REVIEW`는 현재 정본과 대조할 참고,
  `OLD`는 역사 기록을 뜻한다. 구현의 `CURRENT/PARTIAL/PLANNED`와 별개다.
- **마지막 내용 검토일**에는 검토한 범위를 함께 적는다. 기존 문서가 갱신일만
  기록했다면 그 사실을 표시한다. 날짜가 없으면 `미기록`으로 남긴다.
- **근거 기준·범위**는 별도의 시점이다. 오늘 목적·링크·문구를 정리해도 과거 실행의
  source pin, 검증일, 외부 자료 확인일은 그대로다. 표 전체가 최근 갱신됐다는 이유로
  모든 행의 기술적 근거가 최신이라고 읽지 않는다.
- **담당·후속**은 문서의 사실 소유 도메인과 다음 확인 책임을 뜻한다. 특정 AI 세션을
  영구 소유자로 두지 않는다. 세션이 바뀌어도 문서·코드·보존 증거에서 확인한다.

2026-09-09에는 아래 문서의 역할과 연결을 대조하고, 후속 v0.8 개발 변경이 영향을 주는
문서는 해당 코드·검증 경로를 추가 대조했다. 모든 본문을 현재 runtime으로
재검증하거나 모든 외부 링크의 현재 내용을 다시 조사한 것은 아니다. 이 범위 밖의
문서는 [전체 색인](../README.md)에서 찾고, 작업에 사용하게 될 때 필요한 행을 추가한다.

## 2. 재검토 규칙

| 조건 | 필요한 확인 | 완료 기록 |
|---|---|---|
| 제품 목적·관리 의미·전역 우선순위 변경 | 해당 정본을 먼저 고치고 영향을 받는 계약·색인·mirror를 대조 | 내용 검토일과 바뀐 범위; 실행 성숙도는 별도 근거가 있을 때만 수정 |
| API·schema·명령·경로·source 목록·권한·실행 방법 변경 | 그 사실의 소유 가이드와 실제 코드·검증 경로 대조 | 바뀐 계약과 유효한 증거의 source 범위; 과거 기록은 보존 |
| 관련 작업 착수 | 결정에 필요한 현재 branch·파일·도구·권한·관측 시점을 확인 | 확인한 상태와 미확인 사항을 해당 작업 결과에 남김; 전체 문서를 다시 읽을 필요 없음 |
| 외부 근거 확인 후 30일 경과, 관련 작업 착수, 또는 release/API/EOL 변경 인지 | 사용할 주장에 한해 공식 1차 자료를 다시 확인 | 실제 확인일·정확한 URL/버전·달라진 주장. 자동 pin 변경이나 upstream 코드 반입은 하지 않음 |
| `REVIEW/OLD` 문서를 현재 설계에 인용 | 현재 정본·코드와 충돌하는 전제부터 구분 | 유효한 참고 범위만 인용; 문서 안의 “다음”을 전역 큐로 복사하지 않음 |
| 링크·서식만 수정 | 경로와 문서 역할의 일치 확인 | 링크 정비 범위만 기록; 기술 내용·외부 자료·실행 검토일을 새 날짜로 덮지 않음 |

30일은 외부 정보를 의사결정에 다시 쓰기 위한 **재확인 기준**이다. 모든 문서를 매달
일괄 수정하라는 뜻은 아니며, 안정된 내부 계약을 문서 나이만으로 무효화하지 않는다.
공식 자료를 확인할 수 없으면 해당 주장과 확인 필요 조건을 남기고, 이를 새 기술 선택의
확정 근거로 사용하지 않는다. 관련 없는 작업까지 멈추거나 구현 성숙도를 자동 하향하지 않는다.

## 3. 제품·작업·관리 정본

| 문서 | 역할 | 수명주기 | 마지막 내용 검토일·범위 | 근거 기준·범위 | 재검토 trigger | 담당 도메인·후속 |
|---|---|---|---|---|---|---|
| [제품 방향](aios_product_direction_ko.md) | 제품 목적·공간·상호작용·성공 기준 정본 | 활성 | 2026-09-13, 기존 목적 유지·v0.10 Task 표면 대조 | 환경 문맥·Task 개발 PARTIAL, 한정 실제 모델 Task PASS, 미완료 범용 상호작용을 구분 | 사용자 목적·성공 기준 변경 | 제품 방향; 운용 계약·전역 큐에 영향 전달 |
| [통합 작업 진입](integrated_work_guide_ko.md) | 요청 분류·사실 소유자·변경 절차 | 활성 | 2026-09-13, 문맥·Task 계약 소유자 연결 | 저장소 작업 규칙; runtime의 자동 환경 발견·영속 기억 구현 아님 | 작업 규칙·도메인·정본 경로 변경 | 문서 운영; 진입 경로와 스킬 라우팅 대조 |
| [AGENTS](../../AGENTS.md), [프로젝트 스킬](../../.agents/README.md) | 작업 규칙·도메인별 절차 라우터 | 활성 | 2026-09-09, 제품 목적·전역 큐·신선도 연결 | 작업 규칙과 문서 동기화·triage·관리 구조·Linux 큐레이터 스킬; runtime 상태는 분야 정본에 위임 | 권한·작업 절차·도구·정본 경로 변경 | 저장소 운영; 관련 스킬의 규칙·링크 대조 |
| [전역 작업흐름](minimal_io_and_maturity_workflow_ko.md) | 유일한 전역 큐·기술축 성숙도 정본 | 활성 | 2026-09-13, 장기 실증의 다섯 단계·첫 pilot과 반복 작업 순서 정렬 | v0.10 한정 PASS와 미완료 image39 보존; 자기 참조 PARTIAL·연구 검사/감사 PASS와 모델 목표 0/OFF 미완료·다음 한정 prompt 비교 분리 | 단계 완료·선행조건·사용자 우선순위 변경 | roadmap; `agent-consumer-next`와 기술축 표 함께 대조 |
| [자기 참조 연구·실증](self_reference_research_workflow_ko.md) | 실험·beta 이후 기존 GPT 검토·로컬 반영 운영 가이드 | 활성 | 2026-09-13, 승인 절차·sandbox 계약·실패/재생·v2/grammar 감사·ON/OFF 최종 경계 대조 | RESEARCH/PARTIAL; 실패 원본·v2/grammar 감사 PASS·ON 모델 목표 0/OFF 미완료·479파일 불변/전체 비교 NOT_EVALUABLE; 전체 수락 기준은 전역 큐 | 실행·검증 결과, 실험 조건·모델 pin·연구 제안 채택 변경 | 연구·검증; 실제 artifact와 영향 mirror를 대조하고 원본 실패 보존 |
| [PROJECT](../../PROJECT.md) | 도메인 맵·의존 방향 정본 | 활성 | 2026-09-13, v0.10과 자기 참조 연구의 도메인 책임 대조 | 기존 source별 지원 범위 보존; 연구 driver/검증과 향후 hosted Task runtime 분리 | 파일 이동·새 도메인·의존 경계 변경 | 저장소 구조; 각 도메인 README 연결 |
| [CLAUDE](../../CLAUDE.md) | 구현 mirror·빌드·저수준 불변식 | 활성 | 2026-09-13, 실제 Task PASS의 stale 부정 정정·장기 실증 mirror 대조 | v0.10 증거와 image39 미완료 보존; 연구 PARTIAL·첫 실패/calibration 무결성/모델 행동 실패 분리 | 명령·공개 계약·gate·구현 상태 변경 | 구현·검증; 변경 사실의 원 소유 가이드 대조 |
| [인수인계](codex_handoff_tips_ko.md) | 환경·장애 원인·검증 경계 운영 참고 | 활성 | 2026-09-13, v0.10과 최신 증거 정본 진입 대조 | native·hosted 역사 본문 유지; Windows 전체·Linux fixture와 한정 실제 모델 TaskSmoke PASS는 별도 | 실행 환경·명령·재현 조건 변경 | 작업 운영; 실제 현재 상태를 먼저 재확인 |
| [관리 모델](../kernel-room/kernel_room_management_model_ko.md) | 관리 의미·권위·불변식·분야별 의존 정본 | 활성 | 2026-09-13, 환경 문맥 부재 문구 정정·Task 경계 | 관리 의미·native ABI 유지; hosted Task와 canonical identity 분리 | identity·세대·관계·용어·관리 권위 변경 | Kernel Room; topology·개발 가이드 동기화 |
| [관리 개발 가이드](../kernel-room/development_guide_ko.md) | 관리 변경·검증 운영 가이드 | 활성 | 2026-09-13, 환경 문맥·Task와 전역 큐 대조 | native 계약·과거 실행 증거 유지; 한정 실제 모델 Task PASS·이미지 미완료 | 개발 절차·검증 경로·관리 계약 변경 | Kernel Room; 작은 변경과 verifier 대조 |
| [관리 topology](../kernel-room/kernel_room_topology_ko.md) | 관리 정본에 종속된 개념도 | 활성 | 2026-09-03, 본문 갱신 기록 | hierarchy·identity·binding 설계; 독립 성숙도 정본 아님 | 관리 모델의 용어·관계 변경 | Kernel Room; 개념도와 정본 일치 확인 |
| [에이전트 운용 계약](../autonomy/agent_operating_contract_ko.md) | 상태·행동·거부·결과·복구 계약 | 활성 | 2026-09-13, UUID·단일 제어 루프·같은 CLI 취소·UNKNOWN 대조 | Task PARTIAL; 고정 서버 정적 근거와 실제 backend 종료 검증은 별도 | 상태 schema·행동·권한·사용자 개입·서버 pin/slot 설정 변경 | autonomy·hosted; 실제 소비·결과 검증은 전역 큐에서 선택 |

[저장소 문서 색인](../README.md), [Kernel Room 허브](../kernel-room/README.md),
[hosted 도메인 진입](../../hosted/README.md)은 2026-09-09 역할·경계 정비를 반영한
활성 색인이다. 저장소 문서 색인은 2026-09-13에 v0.10 Task와 증거 정본의 진입을
추가 대조하고 자기 참조 연구·실증 가이드의 진입을 연결했다. README·PROJECT·CLAUDE의
첫 실패·calibration 재생·v2 검사/grammar probe·후속 source와 미완료 행동 비교 경계도 함께 대조했다. 버전·테스트 수·source별 상세 결과는 위 정본과 아래 가이드가 소유한다.
새 문서·도메인·실행 경로가 생기면 연결을 함께 대조한다.
hosted 도메인 진입과 Kernel Room 허브는 2026-09-13에 v0.10 Task의 문맥·취소 경계를
추가 대조했다. 이 체크포인트의 v0.10과 보존된 v0.9·기존 beta·이미지 기록을 구분하며, 보존된 실제 문맥
소비/fixture와 한정 실제 모델 Task PASS·미완료 새 이미지의 증거를 분리한다.

## 4. 실행·검증 가이드

아래의 날짜는 각 가이드가 명시한 내용·근거 시점이다. 실행 ID, 정확한 명령, 버전,
해시, 성공과 실패의 원본은 각 가이드가 소유하며 이 원장에 중복하지 않는다.

| 문서 | 역할 | 수명주기 | 마지막 내용 검토일·범위 | 근거 기준·범위 | 재검토 trigger | 담당 도메인·후속 |
|---|---|---|---|---|---|---|
| [Linux 실행 기반 정책](../os/linux_hosted_substrate_and_resource_policy_ko.md) | 실행 기반·source 반입 경계 정본 | 활성 | 2026-09-13, 환경 문맥·Task 구현 경계 정정 | upstream exact reference 확인은 **2026-08-23** 유지; source 목록과 runtime 구현은 별도 | 실행 책임·반입 정책 변경; upstream 작업 착수/30일 | hosted·Linux resource; 공식 근거와 manifest부터 대조 |
| [H1 trace/replay](../os/h1_binding_trace_replay_workplan_ko.md) | bounded H1 계약·검증 정본 | 활성 | 2026-09-03, 보존된 원격 acceptance | host-only 계약/replay 증거; live producer·H2 runtime 제외 | H1 field/reason/lifecycle·bundle·checker 변경 | hosted·testkit; 해당 source의 재생 계약 확인 |
| [부팅·하드웨어](../os/aios_userspace_boot_hardware_guide_ko.md) | 시작 로그·Linux-visible inventory 운영 가이드 | 활성 | 2026-09-08, 운영 mirror; 공식 검토는 09-03 | 보존된 source 관측·실행별 artifact; native 드라이버 지원 아님 | bootstrap·inventory·권한·source/검증 변경 | hosted; boot 계약과 실제 관측 대조 |
| [CLI·인터넷](../os/aios_cli_internet_guide_ko.md) | 고유 CLI·DNS/HTTP(S) 실행 가이드 | 활성 | 2026-09-13, v0.10 UUID 질문 흐름 연결; 공식 검토는 09-03 유지 | 보존된 기존 CLI·인터넷 증거와 새 Task의 실제 모델 검증을 구분 | 명령·네트워크 계약·launcher 변경 | hosted; CLI 사용법·session 판정 대조 |
| [일반 서비스](../os/aios_service_lifecycle_guide_ko.md) | CONSOLE_RUNTIME 수명·재접속 계약 | 활성 | 2026-09-04, 본문 공식 검토 기록 | 서비스 기반과 실제 MAIN AI_SERVICE를 구분 | 서비스 수명·identity·receipt·재접속 변경 | hosted; source와 service verifier 대조 |
| [MAIN 결속](../os/aios_agent_binding_guide_ko.md) | 실제 모델 요청·명시적 관리 결속 계약 | 활성 | 2026-09-13, v0.10 개발 경계 연결; 본문 실행 기록은 09-07 유지 | 당시 결속·모델 실행과 후속 문맥·Task 검증의 source를 구분 | MAIN protocol·binding·모델 profile 변경 | hosted·Kernel Room; 모델 응답과 실행 증거 구분 |
| [MAIN 환경 문맥](../os/aios_space_context_guide_ko.md) | 환경 관측·신선도·실제 모델 입력과 소비 검증 정본 | 활성 | 2026-09-13, v0.10·Windows 전체·Linux fixture·한정 실제 모델·독립 재생 대조 | PARTIAL; §6.3의 Windows 990개/85개 제외와 Linux fixture 3개 PASS, 한정 실제 모델 TaskSmoke와 보존 source 독립 재생 PASS·미완료 image39를 분리. 과거 원본 FAIL/수정 재생 유지 | 관측 범위·TTL·입력·receipt·모델 실행·실제 검증 결과 변경 | hosted·autonomy·testkit; raw→입력→실행→사용자 출력과 거부·복구 대조 |
| [자원 관측](../os/aios_resource_observation_guide_ko.md) | MAIN/backend 관계·CPU/RSS·PSI 관측 | 활성 | 2026-09-13, v0.10 개발 경계 연결; 본문 근거는 09-07 유지 | 당시 source 관측 증거 유지; Task 자원 창과 모델 계산 귀속을 구분 | 관계·관측 필드·source 유효성 변경 | hosted·resource; 관측과 제어 주장 분리 |
| [Cell 수명](../os/aios_cell_lifecycle_guide_ko.md) | 한정 Cell 관리 전이·재결속 계약 | 활성 | 2026-09-13, v0.10 개발 경계 연결; 본문 근거는 09-07 유지 | 당시 관리 세대 전이·거부 증거 유지; 전체 hierarchy·Task acceptance와 별도 | 관리 상태·세대·reconcile 변경 | Kernel Room·hosted; 해당 세대 전이와 verifier 확인 |
| [backend 수명](../os/aios_backend_lifecycle_guide_ko.md) | 실행 대상 결속·같은 CLI의 recover | 활성 | 2026-09-13, v0.10 취소 소유 범위 연결; recover 근거는 09-08 유지 | 같은 CLI retained handle 범위의 명시적 정지와 MAIN의 독립 종료 관측; 한정 실제 모델 TaskSmoke PASS | backend identity·owner lease·receipt·recover 변경 | hosted; 허용된 cleanup·정상 종료 경계 확인 |
| [운영 이미지](../os/aios_operating_image_guide_ko.md) | 설치·반복 부팅·기본 선택·acceptance 정본 | 활성 | 2026-09-13, v0.10 개발 경계 연결; 공식 자료·운영 근거는 09-08 유지 | 보존 이미지별 원본 증거 유지; source 39개 신규 이미지와 부팅 시 backend 소유 handle 분석 미완료 | Build/Smoke/Run·selector·installer·OS lock·pin 변경 | hosted·Windows; 원본/사용자 사본·실행 도구 범위 확인 |
| [검증 설계](../tools/verification_tooling_evolution_design_ko.md) | verdict·evidence·provenance 정본 | 활성 | 2026-09-03, 본문 최종 갱신 기록 | 정규 lane과 진단 lane 분리; 설계된 후속 기능은 별도 | verdict·artifact·baseline·CI 판정 계약 변경 | testkit; fail-closed와 증거 출처 대조 |
| [Testkit](../tools/testkit_guide_ko.md) | 실제 검증 명령·결과 사용 가이드 | 활성 | 2026-09-13, 동기 SpaceSmoke·후속 TaskSmoke의 계약 분리 | 보존 source별 판정 유지; 새 smoke 구현은 실제 모델 성공 증거가 아님 | 명령·테스트 진입·platform·artifact 경로 변경 | testkit·hosted; 새 실행과 보존 원본 재검증 구분 |
| [QEMU MCP](../tools/qemu_mcp_guide_ko.md) | 진단 편의 도구 운영 가이드 | 활성 | 2026-08-28, 도입 기준 기록 | 등록·도구 노출도 당시 환경의 기록; 정규 verdict 아님 | 실제 도구 사용 착수·등록/버전 변경·외부 근거 30일 | 개발 도구; 현재 등록·노출 상태를 직접 확인 |

로컬 `build/`와 이미지 원본은 비추적 증거다. fresh checkout에서 파일이 없다는 이유로
이전 PASS를 현재 재생했다고 쓰지 않는다. 반대로 과거 이미지의 source bytes가 현재 파일과
다르면 그 원본의 보존 runtime으로 재생하고, 새 source의 검증 여부는 별도로 기록한다.
원본 FAIL을 사후 PASS로 덮거나 문서 날짜에 맞추어 원본 artifact를 바꾸지 않는다.

## 5. 분야별 후보·참고·외부 조사

다음 문서는 후보·설계·당시 조사 맥락을 제공한다. 현재 제품 방향과 충돌하는 문장을
그대로 구현 지시로 사용하지 않는다. 외부 주장이 필요한 경우에만 §2의 공식 자료 재검토를
수행하며, 조사 날짜를 이번 문서 정비일로 덮지 않는다.

| 문서 | 역할 | 수명주기 | 마지막 내용 검토일·범위 | 근거 기준·범위 | 재검토 trigger | 담당 도메인·후속 |
|---|---|---|---|---|---|---|
| [빌드 프로젝트 조사](aios_build_project_landscape_2026_08_03_ko.md) | 외부 사례 참고 | REVIEW | 2026-08-10, 구현 경계 대조 기록 | 외부 조사는 **2026-08-03**, RESEARCH | 비교·도입 작업 착수 또는 외부 근거 30일 | 도구·제품 조사; 선택한 사례의 현재 1차 자료 확인 |
| [과거 AI-native OS 조사](ai_native_os_github_landscape_ko.md) | 역사적 외부 조사 | OLD | 2026-04-21, 조사 기록 | 당시 공개 프로젝트 스냅샷 | 새 비교 근거로 재사용할 때 | 제품 조사; 당시 결론 보존하고 새 공식 근거를 별도 기록 |
| [모델 스택 추천](../models/agent_model_stack_recommendations_ko.md) | 모델·학습 전략 참고 | REVIEW | 2026-09-09, 분류·내용 경계 | 원문 기재 기준은 **2026-03-28**, 출처별 실제 확인일은 미확인; 모델·라이선스·조합의 현재성도 재검증하지 않음 | 모델 선택·다운로드·학습 착수 또는 외부 근거 30일 | 모델·runtime; 실제 목적·하드웨어와 공식 자료로 재평가 |
| [초기 자율 OS 요구](../os/ai_agent_autonomous_os_requirements_ko.md) | 초기 native 요구 참고 | REVIEW | 2026-09-09, 분류·내용 경계 | 기존 기술 내용의 원 검토일 미기록; 초기 경로·기능 부재 전제는 현재 계약 아님 | 요구 재사용·현재 제품 계약으로 반영할 때 | autonomy·native; 제품 정본·운용 계약과 충돌 분리 |
| [품질 backlog](maturity_levers_backlog_ko.md) | SUPPORTING/ORTHOGONAL 품질 후보 | 활성 | 2026-09-09, 후보와 역사 구분 | 당시 진단·이전 다음 단계는 보존 기록; 현재 전역 큐 아님 | 해당 후보를 실제 작업으로 선택할 때 | 검증·품질; 현재 gap 재현 후 전역 큐에서 우선순위 판단 |
| [브라우저·runtime roadmap](../os/browser_console_and_runtime_engine_roadmap_ko.md) | W 분야의 브라우저 전달 설계 가이드 | 활성 | 2026-09-13, v0.10 문맥·Task와 W 기능 경계 대조 | hosted 개발 소스와 W의 PLANNED/RESEARCH는 별도; 브라우저 구현 검증 아님 | W 기능·browser adapter·engine 작업 착수 | W·autonomy; 실제 소비 계약·현재 구현 경계 대조 |

역사 기록을 삭제하거나 모두 활성으로 다시 쓰는 것이 신선도 관리의 목표는 아니다.
현재 결정에 쓰는 사실의 소유자와 유효 범위를 찾을 수 있고, 필요한 부분을 다시 검토할 수
있으면 된다. `REVIEW`의 정정·재분류가 필요할 때는 본문 배너·색인·이 행을 같은 작업에서
맞추며, 전역 구현 큐의 변경은 전역 작업흐름에서 별도로 결정한다.

## 6. 한 번의 갱신을 마치는 방법

1. 현재 변경이 영향을 주는 사실 소유 문서를 고르고 실제 본문·코드·근거를 대조한다.
2. 확인한 주장, 확인하지 않은 범위, 실행/외부 근거의 날짜를 분리해 적는다.
3. 해당 행의 내용 검토일·범위와 trigger·담당 후속을 갱신한다. 역할 변경은 배너와 색인도 맞춘다.
4. 중복 수치·결론을 다른 문서에 늘리지 않고 소유 문서로 연결한다. 링크와 변경 범위를 확인한다.

문서만 바꾼 작업에는 그 사실을 남기고 종료한다. 새로운 runtime 성숙도, 자동화된 신선도
검사기, 영속 AI 기억 또는 통합 공간 인지 기능이 구현됐다고 보고하지 않는다.
