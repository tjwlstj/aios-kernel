# 기술 성숙도 레버 백로그 (2026-07-15, 2026-08-10 관리축 정렬)

최종 갱신: 2026-08-15 (native K2-a oracle와 H1 다음 조각 정렬)

이 문서는 특정 기능(M-레벨)이 아니라 **바닥을 올리는 성숙도 레버**를 한곳에 모아 우선순위·착수점·담당·조율지점을 고정한다. Claude가 회귀 정비 중 관찰한 것을 근거로 정리했고, Codex와 의견을 조율한 뒤 착수한다.

이 백로그는 관리 K축을 대체하지 않는 `SUPPORTING`/`ORTHOGONAL` 품질 작업 모음이다.
K1 hierarchy registry v0와 bounded native K2-a source-binding oracle은 `CURRENT`다.
프로젝트의 다음 직접 조각은 [Kernel Room 관리 모델](../kernel-room/kernel_room_management_model_ko.md)의
semantic field/reject 계약을 OS-neutral H1 trace/replay로 옮긴 뒤 K2 live
generation/reconciliation을 확대하는 것이다. 아래 레버의 착수 가능 표시는 그
우선순위를 뒤집지 않는다.

## 문서 관계 (중복 금지)

- **검증 축(V0~V5)**은 `docs/tools/verification_tooling_evolution_design_ko.md`가 정본이다. 아래 레버 ①③은 그 로드맵의 V1/V2/V4와 겹치므로 **여기서 재설계하지 않고 참조만** 한다.
- **관리 K0~K5, 실행 M1~M5, 지속성 C1~C2, 브라우저 W, Linux-hosted H축**은 `docs/meta/minimal_io_and_maturity_workflow_ko.md`가 정본이다. 레버 ⑤는 실행 substrate의 M3 후속 하드닝이며 여기서 구체화한다.
- **커널 지뢰/관례**는 `docs/meta/codex_handoff_tips_ko.md`가 정본이다.
- 이 문서는 위 축을 가로지르는 "품질 바닥" 항목만 다룬다.

## 현재 회귀 방어 진단 (2026-08-03 갱신)

- **마커 커버리지: 양호.** 부팅 셀프테스트가 방출하는 PASS 마커가 스모크 3프로파일 필수 패턴에 전부 등록됨. verdict evaluator(V0)가 "PASS 뒤 panic/역순/중복"까지 fail-closed로 잡는다.
- **CI: 방금 정비됨.** 트리거에 `beta` 추가(이전엔 `main`만이라 beta 작업이 CI 미실행) + `boot-inventory` 3프로파일 구조 드리프트 검사 추가.
- **남은 천장: 대부분의 검증이 여전히 개별 문자열 계약에 묶여 있다.** process event journal v1은 exact ordered-vector marker, structured `process_event_journal`, `state user event_*`까지 닫힌 첫 bounded 예외지만 generic event transport는 아니다. 성숙도의 다음 도약은 나머지 증거의 **텍스트 → 공통 구조 계약**이다.

## 레버 표

| # | 레버 | 축 | 정본 위치 | 상태 | 담당 후보 |
|---|---|---|---:|---|---|
| ① | 범용 버전드 기계판독 이벤트(`[EVT]{json}`) | 검증 | verdict V1 (shared marker manifest / events.jsonl) | **조율 필요** | Claude 파일럿 → Codex 확장 |
| ② | 구조적 서브시스템 카운트 가드 | 검증/커널 | verdict V2 + 인벤토리 baseline | **착수 가능** | Codex 또는 Claude |
| ③ | Fault-injection 게이트(실패 경로 증명) | 검증 | verdict V4 (test-only fault catalog) | **조율 필요** | Codex(verdict 인프라 소유) |
| ④ | UBSan 디버그 빌드 레인 | 커널/빌드 | (신규) | **착수 가능** | Claude |
| ⑤ | 4K 단위 W^X 정밀화 | 커널/하드닝 | workflow §3-B 후순위 하드닝 | **착수 가능(주의)** | Claude |

## 레버 상세

### ① 버전드 기계판독 이벤트 — 가장 고레버리지
- **왜:** 검증을 grep에서 필드 단위 assert로 승격. 문구 변경에 강하고 "정확히 이 값이어야 함"을 코드로 표현 가능. verdict 원칙 5가 이미 "문자열 마커 유지 + versioned event 병행"을 명시.
- **현재 경계:** process event journal v1은 per-boot capacity 8/no-overwrite의 커널 내부 lifecycle/capture evidence schema다. exact `[PROC]` summary와 host structured section은 `CURRENT`지만 generic serial `[EVT]{json}`, shared marker manifest, `events.jsonl` artifact를 구현하지 않는다. journal의 `0→1→0→2→0`도 CPU switch가 아니다.
- **형식(제안):** 사람용 마커는 그대로 두고, 셀프테스트가 한 줄 더 방출.
  `[EVT] {"v":1,"id":"heap.lock.selftest","ok":true,"acquires":4}`
- **파일럿 착수점:** heap lock + context switch 셀프테스트 2개에 `[EVT]` 병행 방출 → `tools/testkit/lib/`에 이벤트 파서 추가 → verdict가 `id`별 기대 필드 assert. 기존 문자열 마커/스모크는 그대로 유지(점진 이행).
- **조율지점(Codex와):** 이벤트 스키마(`v`/`id`/`ok`/payload 규칙), `id` 네임스페이스 규약, events.jsonl 아티팩트로의 승격 시점. verdict V1과 통합되어야 하므로 **스키마 확정 전 Codex 합의 필수.**

### ② 구조적 서브시스템 카운트 가드 — 작고 즉효
- **왜:** 지금 서브시스템을 하나 지워도 health `ok` 숫자가 줄 뿐 의도/회귀 구분 불가. 체크포인트 때 게이트 범위 드리프트를 겪은 것과 같은 계열.
- **착수점:** `KERNEL_SUBSYSTEM_COUNT`와 정상 부팅의 health `ok` 기대값을 인벤토리 baseline에 **명시 고정**하고, boot_verdict/baseline_guard가 이 값 불일치를 FAIL로 판정. Kernel Room gate descriptor의 선언 범위가 전체 시스콜 번호 공간을 분류하는지도 같은 가드로 검사한다(현재는 수동 규칙). 이는 descriptor coverage 검증이며 per-call enforcement 증거가 아니다.
- **조율지점:** 거의 없음. baseline 필드 추가라 안전. Codex의 baseline_guard 위에 얹으면 자연스러움.

### ③ Fault-injection 게이트 — 실패 경로를 신뢰가 아니라 증명으로
- **왜:** 지금은 성공 경로만 테스트되고 fail-closed 경로(주소공간 복원 실패, allocator OOM, process activate 실패)는 "그렇게 짰으니 될 것"이라는 신뢰 상태. verdict V4가 정확히 이걸 계획.
- **착수점:** `make FAULT=1`(또는 boot 플래그)로만 활성화되는 test-only 훅. 셀프테스트가 의도적으로 실패 → verdict가 기대 `DEGRADED`/`FAIL`/`PANIC` + reason ID를 **성공으로** 판정.
- **조율지점(Codex와):** fault catalog의 reason ID 체계(append-only 숫자), production 빌드에서 훅 제거 보장 방식. **verdict V4의 소유가 Codex이므로 Codex 주도가 자연스러움.**

### ④ UBSan 디버그 빌드 레인 — 정적 분석이 못 잡는 런타임 UB
- **왜:** cppcheck는 정수 오버플로/정렬 위반 같은 런타임 UB를 못 잡는다. `-fsanitize=undefined` + 프리스탠딩 최소 핸들러를 별도 빌드 프로파일로 두면 부팅 중 UB를 포착.
- **착수점:** `make UBSAN=1` 프로파일(CFLAGS에 `-fsanitize=undefined -fno-sanitize=alignment` 등 프리스탠딩 호환 서브셋) + `__ubsan_handle_*` 최소 스텁이 `[UBSAN] <kind> at <loc>` 방출 후 panic. boot-matrix에 debug 레인으로 편성.
- **조율지점:** 없음. 독립 빌드 프로파일이라 기존 경로 무영향. **Claude 착수 예정.**
- **주의:** freestanding에서 UBSan 런타임 심볼을 직접 제공해야 함(libubsan 없음). 서브셋만 켜서 심볼 수를 최소화.

### ⑤ 4K 단위 W^X 정밀화 — 하드닝 성숙
- **왜:** 현재 W^X는 2MB huge page 단위라 `.text`와 저메모리(BIOS/VGA)가 한 페이지를 공유하면 그 페이지가 W+X로 남는다(하드닝 보고서에 명시된 잔여 항목).
- **착수점:** 커널 이미지 구간을 4K로 리매핑해 `.text`=RX / `.rodata`=RO / `.data`·`.bss`=RW-NX 분리. `[SEC]` 계열 셀프테스트로 각 구간 권한 검증.
- **조율지점:** 없으나 **위험도 중간** — 페이지 테이블 대수술이라 #PF/#DF 위험. `codex_handoff_tips_ko.md`의 페이징 지뢰(2.5, 2.8)와 직접 연관. Codex의 M3-b private CR3 작업과 페이지테이블 소유가 겹치므로 **M3 안정화 이후** 착수.

## Codex에게 (사전 기입)

의견 조율 후 아래를 맡길 수 있다. 규약은 `codex_handoff_tips_ko.md` §1 그대로.

- **③ Fault-injection 게이트(verdict V4):** Codex가 verdict 인프라를 소유하므로 주도 적임. 시작 전 fault reason ID 체계를 이 문서/verdict 문서에 확정.
- **① 범용 이벤트 스키마 합의:** process event journal의 내부 numeric ID와 혼용하지 않는다. Claude가 `[EVT]{json}` 파일럿(heap/sched 2개)을 올리면 Codex가 스키마 리뷰 후 나머지 셀프테스트로 확장 + events.jsonl 아티팩트(verdict V1) 통합.
- **② 카운트 가드:** baseline_guard 위에 얹는 작업이라 Codex가 하기 편함. 착수 시 `KERNEL_SUBSYSTEM_COUNT`/health ok 기대값을 baseline에 추가.

## Claude 착수 예정

- **④ UBSan 레인**(독립 빌드 프로파일, 무위험) → 다음 후보.
- **① 범용 `[EVT]{json}` 이벤트 파일럿**(process event journal과 별도이며, 스키마는 Codex 합의 후 확정).
- **⑤ 4K W^X**는 M3-b 안정화 이후.

> 우선순위 합의 결과와 진행 상태는 이 표의 "상태" 칸을 갱신해 추적한다.
