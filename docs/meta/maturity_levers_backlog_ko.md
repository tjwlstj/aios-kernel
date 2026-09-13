# 기술 성숙도 레버 백로그 (2026-07-15, 2026-08-10 관리축 정렬)

> 문서 수명주기: 활성 지원 문서. 역할은 `SUPPORTING`/`ORTHOGONAL` 품질 후보 목록이며 전역 작업 큐가 아니다.
> 분류·내용 검토일: **2026-09-09**. 과거 진단과 제안을 현재 우선순위·승인 조건에서 분리했다.
> 아래 후보의 효과나 전체 구현 상태를 이번 문서 정비로 재검증한 것은 아니다.

이 문서는 기존 관측에서 나온 품질 개선 후보를 보존한다. 번호는 실행 순서가 아니며,
각 항목은 구체적인 제품 문제나 남은 위험을 해결할 때 선택한다.
[AIOS 제품 방향 정본](aios_product_direction_ko.md)의 성공 조건과
[전역 작업 큐](minimal_io_and_maturity_workflow_ko.md)가 다음 작업을 결정한다.
품질 후보 전체를 로컬 AI·환경 문맥·작업공간·상호작용 개발의 공통 선행조건으로 삼지 않는다.

당시 Claude/Codex에 배정하려던 작업은 현재의 고정 담당자나 별도 승인 의무가 아니다.
담당 범위와 필요한 검토는 현재 작업에서 정하며, 기존 사용자 승인과
[통합 작업 가이드](integrated_work_guide_ko.md)의 변경 범위별 검증 원칙을 따른다.
이 문서의 역할과 재검토 범위는 [문서 최신성 원장](document_freshness_registry_ko.md)을 따른다.

## 문서 관계 (중복 금지)

- **검증 축(V0~V5)**의 설계는 [검증 도구 진화 설계](../tools/verification_tooling_evolution_design_ko.md)를 참조한다. 후보 ①~③과 겹치는 계약을 여기서 별도로 확정하지 않는다.
- **관리 K축과 실행·지속성·브라우저·Linux-hosted 축의 전역 순서**는 [성숙도 작업흐름](minimal_io_and_maturity_workflow_ko.md)을 따른다. 후보 ⑤는 native 실행 substrate의 하드닝 제안이다.
- **커널 지뢰/관례**는 [handoff](codex_handoff_tips_ko.md)를 참조하되 native 변경에 해당하는 범위만 적용한다.
- **hosted 장애 검증**은 [backend 수명 가이드](../os/aios_backend_lifecycle_guide_ko.md)와 [운영 이미지 가이드](../os/aios_operating_image_guide_ko.md)의 별도 증거를 따른다. 이를 native 범용 fault catalog의 완료로 세지 않는다.

## 보존된 회귀 방어 진단 (2026-08-03 기록)

다음 세 항목은 당시 native 검증 경로에 관한 원문 관측이다. “방금”, “여전히”,
“다음 도약”은 그 시점의 표현이며 현재 CI 상태나 전역 다음 작업을 뜻하지 않는다.

- **마커 커버리지: 양호.** 부팅 셀프테스트가 방출하는 PASS 마커가 스모크 3프로파일 필수 패턴에 전부 등록됨. verdict evaluator(V0)가 "PASS 뒤 panic/역순/중복"까지 fail-closed로 잡는다.
- **CI: 방금 정비됨.** 트리거에 `beta` 추가(이전엔 `main`만이라 beta 작업이 CI 미실행) + `boot-inventory` 3프로파일 구조 드리프트 검사 추가.
- **남은 천장: 대부분의 검증이 여전히 개별 문자열 계약에 묶여 있다.** process event journal v1은 exact ordered-vector marker, structured `process_event_journal`, `state user event_*`까지 닫힌 첫 bounded 예외지만 generic event transport는 아니다. 성숙도의 다음 도약은 나머지 증거의 **텍스트 → 공통 구조 계약**이다.

### 이후 기록 (2026-09-03)

H1-a/b/c trace/replay·12개 fixture·artifact/parity는
[당시 exact-SHA 원격 acceptance (§13.2)](../os/h1_binding_trace_replay_workplan_ko.md#132-2026-09-03-원격-acceptance-완료)를
통과했다. 그때 적은 “다음은 H2 observe-only source와 K2 live generation/reconciliation”은
이전 작업 계획으로 보존하며, 현재 hosted 구현 상태나 다음 제품 작업을 대신하지 않는다.

## 품질 후보 표

아래 상태는 검토할 작업의 종류다. 구현 완료나 착수 승인, 예상 효과를 보장하지 않는다.

| # | 후보 | 축 | 관련 설계 | 선택 시 확인할 경계 |
|---|---|---|---|---|
| ① | 범용 버전드 기계판독 이벤트(`[EVT]{json}`) | 검증 | verdict V1 | 기존 도메인별 증거로 해결하지 못하는 문제와 이행 범위 |
| ② | 구조적 서브시스템 카운트 가드 | 검증/커널 | verdict V2 + 인벤토리 baseline | 현재 가드와의 중복, profile별 정당한 차이 |
| ③ | native fault-injection 게이트 | 검증 | verdict V4 | 실제 미검증 실패 경로, 정상 lane과 장애 lane 분리 |
| ④ | UBSan 디버그 빌드 레인 | 커널/빌드 | 별도 설계 필요 | freestanding 호환성, 빌드·부팅 영향 |
| ⑤ | 4K 단위 W^X 정밀화 | 커널/하드닝 | 실행 substrate 하드닝 | 현재 page-table 경계, 복원 및 회귀 위험 |

## 후보 상세

다음은 기존 제안의 기술적 출발점이다. 착수 시 현재 코드와 남은 증거를 좁게 확인한 뒤
필요한 항목만 작업 계획으로 옮긴다. 이 목록을 완수해야 다른 제품 작업을 할 수 있는 것은 아니다.

### ① 버전드 기계판독 이벤트
- **왜:** 검증을 grep에서 필드 단위 assert로 승격. 문구 변경에 강하고 "정확히 이 값이어야 함"을 코드로 표현 가능. verdict 원칙 5가 이미 "문자열 마커 유지 + versioned event 병행"을 명시.
- **구분할 경계:** process event journal v1의 per-boot capacity 8/no-overwrite lifecycle/capture evidence, exact `[PROC]` summary, host structured section과 generic serial `[EVT]{json}` 제안은 별도다. H1 source-binding JSONL replay나 hosted 구조화 증거도 도메인 전용 계약이므로 generic `[EVT]` 이행을 완료한 것으로 세지 않는다. journal의 `0→1→0→2→0`도 CPU switch가 아니다.
- **형식(제안):** 사람용 마커는 그대로 두고, 셀프테스트가 한 줄 더 방출.
  `[EVT] {"v":1,"id":"heap.lock.selftest","ok":true,"acquires":4}`
- **파일럿 착수점:** heap lock + context switch 셀프테스트 2개에 `[EVT]` 병행 방출 → `tools/testkit/lib/`에 이벤트 파서 추가 → verdict가 `id`별 기대 필드 assert. 기존 문자열 마커/스모크는 그대로 유지(점진 이행).
- **계약 검토:** 이벤트 스키마(`v`/`id`/`ok`/payload 규칙), `id` 네임스페이스, events.jsonl 아티팩트 이행 범위를 producer·consumer 양쪽과 맞춘다. 특정 에이전트의 별도 승인을 요구하는 규칙은 아니다.

### ② 구조적 서브시스템 카운트 가드
- **관측 배경:** 당시 health `ok` 감소나 gate 범위 변화가 의도된 변경인지 구별할 명시 계약이 필요하다는 제안이었다. 현재 가드로 이미 다루는 범위는 중복 구현하지 않는다.
- **착수점(제안):** `KERNEL_SUBSYSTEM_COUNT`와 정상 부팅의 health `ok` 기대값을 인벤토리 baseline에 명시하고 boot_verdict/baseline_guard와 결합한다. Kernel Room gate descriptor의 선언 범위 확인도 후보지만, descriptor coverage 검증은 per-call enforcement 증거가 아니다.
- **위험 검토:** baseline 필드 추가도 정상 profile을 잘못 거부하거나 기대값 갱신으로 회귀를 숨길 수 있다. 실제 남은 차이를 확인하고 해당 profile의 정상·반례만 검증한다.

### ③ Fault-injection 게이트 — 실패 경로를 신뢰가 아니라 증명으로
- **관측 배경:** 당시 주소공간 복원 실패, allocator OOM, process activate 실패 같은 native 경로에 명시적인 장애 주입 증거가 필요하다는 제안이었다. 저장소 전체가 성공 경로만 검증한다는 뜻이 아니다. hosted backend supervisor 소실·명시적 복구에는 별도 실제 장애 증거가 있다.
- **착수점(제안):** 실제 미검증 경로를 하나 선택하고 `make FAULT=1`(또는 boot 플래그) 같은 test-only 진입을 설계한다. 이 명령은 구현된 사용법이 아니다. 별도 장애 판정은 기대 `DEGRADED`/`FAIL`/`PANIC`과 reason ID가 실제로 관측됐는지를 확인하며, 장애 run을 정상 부팅 PASS로 바꾸지 않는다.
- **계약 검토:** fault catalog의 reason ID 체계, production 빌드와 정상 acceptance에서 훅을 제외하는 방법, 원본 실패 증거 보존을 함께 정한다.

### ④ UBSan 디버그 빌드 레인 — 정적 분석이 못 잡는 런타임 UB
- **왜:** cppcheck는 정수 오버플로/정렬 위반 같은 런타임 UB를 못 잡는다. `-fsanitize=undefined` + 프리스탠딩 최소 핸들러를 별도 빌드 프로파일로 두면 부팅 중 UB를 포착.
- **착수점(제안):** `make UBSAN=1` 프로파일(CFLAGS에 `-fsanitize=undefined -fno-sanitize=alignment` 등 프리스탠딩 호환 서브셋) + `__ubsan_handle_*` 최소 스텁이 `[UBSAN] <kind> at <loc>` 방출 후 panic. 제안된 flag와 handler는 구현된 사용법이 아니며 별도 debug lane의 필요성을 먼저 확인한다.
- **위험 검토:** 별도 빌드라도 공통 CFLAGS·linker·런타임 심볼·CI 시간에 영향을 줄 수 있다. 기본 빌드와의 분리 및 회귀 범위를 확인한다.
- **주의:** freestanding에서 UBSan 런타임 심볼을 직접 제공해야 함(libubsan 없음). 서브셋만 켜서 심볼 수를 최소화.

### ⑤ 4K 단위 W^X 정밀화 — 하드닝 성숙
- **관측 배경:** 당시 2MB huge page에서 `.text`와 저메모리(BIOS/VGA)가 권한을 공유하는 잔여 범위가 하드닝 후보였다. 실제 착수 시 현재 매핑과 남은 W+X 범위를 다시 확인한다.
- **착수점:** 커널 이미지 구간을 4K로 리매핑해 `.text`=RX / `.rodata`=RO / `.data`·`.bss`=RW-NX 분리. `[SEC]` 계열 셀프테스트로 각 구간 권한 검증.
- **위험 검토:** 페이지 테이블 변경은 #PF/#DF와 복원 실패를 유발할 수 있다. handoff의 페이징 주의점 및 현재 private CR3 경로와 소유 범위를 맞추고, 전역 큐에서 이 하드닝을 선택했을 때만 진행한다.

## 후보를 실제 작업으로 선택할 때

- 해결할 제품 문제나 구체적인 남은 위험, 변경 범위, 관련 구현·증거를 먼저 적는다.
- 담당자는 현재 작업 단위로 정한다. 과거 Claude/Codex 배정이나 “다음 후보” 표시는 현재 승인 조건이 아니다.
- 필요한 정상·반례 검증은 변경 범위에 맞춘다. 품질 후보를 근거 없이 확대하거나 native 전체 검증을 다른 종류의 작업에 일괄 적용하지 않는다.
- 선택된 작업의 순서와 진행 상태는 전역 큐 및 해당 계약 문서에 기록한다. 이 목록은 별도의 경쟁 작업 큐를 만들지 않는다.
