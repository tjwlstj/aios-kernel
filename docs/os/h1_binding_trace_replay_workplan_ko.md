# H1 OS-neutral binding trace/replay 작업 준비서

> 기준일: 2026-08-15
>
> 최종 갱신: 2026-09-03
>
> 문서 상태: bounded H1 구현·검증 정본 및 후속 작업 기준선
>
> H1 구현 성숙도: `CURRENT` (H1-a transport, H1-b bounded semantic replay와 12개
> fixture, H1-c self-contained bundle/parity 및 exact-SHA 원격 Linux/Windows 검증 완료;
> 2026-09-03, §13.2. live producer와 H2 runtime은 포함하지 않음)
>
> 방향 분류: Kernel Room `DIRECT` + Linux-hosted `HOSTED_DESIGN`

이 문서는 bounded native K2-a source-binding oracle 다음에 수행할 H1의 정확한
구현 경계, trace 의미, fail-closed verifier와 일정을 고정한다. 현재
`hosted/contracts/`의 contract/fixture, replay 실행체, artifact/parity CLI와 host tests는
구현됐고 같은 SHA의 원격 cross-OS terminal 및 artifact를 확인해 H1은 `CURRENT`다.
live native lifecycle producer와 Linux adapter는 여전히 없으며 이 완료에 포함되지 않는다.

용어와 관리 불변식은
[Kernel Room 관리 모델](../kernel-room/kernel_room_management_model_ko.md)이 우선하고,
upstream source/import 경계는
[Linux-hosted substrate 정책](linux_hosted_substrate_and_resource_policy_ko.md)을
따른다. 두 정본과 이 준비서가 충돌하면 앞의 두 정본을 먼저 고친 뒤 H1을 구현한다.

## 1. 왜 H1을 먼저 하는가

현재 K2-a는 canonical Node 101과 producer-owned SLM MAIN source 사이의 한
boot-local immutable binding을 정확히 증명한다. 그러나 source refresh, exit,
recreate, stale observation과 explicit rebind는 아직 증명하지 않는다.

Linux adapter를 먼저 만들면 PID, pidfd, cgroup path, host boot ID 같은 편리한 Linux
표현이 canonical 의미를 사실상 결정하게 된다. H1은 그 전에 다음을 고정한다.

- canonical identity와 external source identity의 분리
- canonical, parent, source, binding generation의 독립성
- source update/exit 뒤 이전 tuple의 stale rejection
- 새 instance를 발견한 뒤에만 가능한 explicit rebind
- native와 hosted raw evidence가 달라도 같은 semantic verdict를 내는 replay 규칙

따라서 H1은 Linux 기능 추가가 아니라 Kernel Room source lifecycle의
substrate-neutral 안전 계약이다. K1 1024B snapshot, K2-a 256B snapshot, 커널 enum,
boot marker와 `state binding`은 H1에서 변경하지 않는다.

## 2. 현재 증거와 완료 뒤에도 남는 경계

| 대상 | 현재 상태 | H1과의 관계 |
|---|---|---|
| K1 hierarchy registry v0 | `CURRENT` | canonical Cell 1, Node 101과 generation의 정본 입력 |
| bounded native K2-a oracle | `CURRENT` | field/reject 의미와 native projection fixture의 기준 |
| K2 전체 lifecycle/reconcile | `PARTIAL` | H1 replay가 생겨도 live producer refresh는 남음 |
| H0 resource manifest/guard | `CURRENT` | upstream source-only 경계를 제공할 뿐 runtime 증거가 아님 |
| H1 trace/replay | `CURRENT` (2026-09-03) | H1-a/b/c와 exact SHA 원격 Linux/Windows bundle 및 독립 parity 완료 (§13.2) |
| H2 Linux observe-only adapter | `PLANNED` | H1 acceptance gate 뒤에만 시작 |
| authorize/apply | `PLANNED` | H1 범위 밖, K5와 별도 승인 필요 |

H1이 `CURRENT`가 되어도 Linux daemon, live native lifecycle producer, resource
attribution, quota, scheduler migration, privileged actuator 또는 production isolation은
생기지 않는다. K2 전체는 live reconcile이 생길 때까지 `PARTIAL`이다.

## 3. 산출물과 의존 방향

각 구현 조각은 빈 디렉터리를 만들지 않고 필요한 contract/replay/test 또는 fixture를
실제 파일과 함께 추가한다. 아래는 2026-08-31 현재 H1-c 로컬 구현 구조다.

```text
hosted/
└── contracts/
    ├── README.md                         # binding trace v1 사람용 정본
    ├── binding-trace-v1.contract.json    # exact field/type/event 계약 manifest
    └── fixtures/                         # 12개 checked-in fixture와 exact sidecar
        ├── manifest.json                 # trace와 기대 verdict의 분리
        ├── valid/
        │   ├── full-lifecycle.jsonl
        │   └── native-k2a-observation.jsonl
        └── invalid/
            └── *.jsonl

tools/
└── hosted/
    ├── binding_trace_replay.py           # stdlib-only parser/replay/CLI
    └── tests/
        ├── test_binding_trace_replay.py
        └── test_binding_trace_fixture_manifest.py
```

- `hosted/contracts/`가 public trace 의미와 fixture를 소유한다.
- `tools/hosted/`는 contract를 소비해 판정할 뿐 product runtime을 소유하지 않는다.
- `hosted/linux/`는 H2 전에는 만들지 않는다.
- `kernel/`과 `os/`는 `hosted/` 또는 `tools/`에 의존하지 않는다.
- Python 3.11 표준 라이브러리만 사용하고 별도 JSON/schema dependency를 추가하지
  않는다. `contract.json`은 JSON Schema 표준 구현체에 의존하는 파일이 아니라 replay와
  host tests가 직접 소비하는 versioned exact-field/type manifest다.

## 4. Trace transport v1

H1 trace는 UTF-8 JSON Lines다. 각 행은 하나의 flat JSON object이고 raw evidence다.
fixture의 기대 PASS/FAIL은 trace 안에 넣지 않고 `fixtures/manifest.json` sidecar에
둔다.

초기 bounded limit은 다음과 같이 고정한다.

- UTF-8, BOM 없음
- 실제 LF와 CRLF terminator만 허용, 마지막 newline 필수. EOF의 단독 `CR`은
  CRLF로 보지 않고 content 안의 bare CR로 `trace.encoding` 처리
- 빈 행 금지
- 행당 UTF-8 4096 bytes 이하. 길이에서 제외하는 것은 실제 LF 또는 CRLF terminator뿐이며,
  EOF의 단독 `CR`은 content byte 수에도 포함
- trace당 terminal을 포함해 64 records 이하(따라서 event는 최대 63개), 전체 256 KiB 이하
- JSON object nesting과 array 금지
- `record_type`별 exact field set; 누락과 unknown field 모두 실패
- duplicate object key 실패
- `NaN`, `Infinity`, `-Infinity` 같은 비유한 JSON token 실패
- integer field에 JSON boolean, float, exponent, 음수 또는 범위 밖 값 금지
- 문자열 enum은 ASCII lower-kebab-case exact match

[RFC 8259](https://www.rfc-editor.org/rfc/rfc8259.html)은 duplicate object name을
받는 구현의 동작이 서로 다를 수 있음을 설명한다. H1은 이 모호성을 허용하지 않고
duplicate key를 parse 단계에서 즉시 거부한다.

## 5. Record v1 필드

모든 event 행은 아래 축을 이름으로 분리한다. 단독 `generation`, `id`, `kind`, `instance`처럼
어느 주체의 값인지 모호한 필드는 두지 않는다.

| 축 | 필드 | 규칙 |
|---|---|---|
| envelope | `schema_version`, `record_type`, `trace_id`, `trace_sequence`, `host_instance`, `event` | `record_type=event`, schema 1, 같은 trace/host instance, 1부터 연속 sequence |
| claimed result | `claimed_outcome`, `claimed_reason` | producer 주장을 replay 계산값과 비교; 자체 PASS로 신뢰하지 않음 |
| canonical | `canonical_namespace`, `canonical_id`, `canonical_kind`, `canonical_generation`, `canonical_valid` | 초기 namespace `node`, kind `ai-service`; v1 trace 동안 ID/generation 불변 |
| parent | `parent_cell_id`, `parent_generation`, `parent_valid` | canonical Node의 exact Cell parent; v1 trace 동안 ID/generation 불변 |
| producer | `producer_instance`, `producer_owned`, `copied_read` | trace producer lifetime; Linux raw boot ID나 process ID를 canonical/source instance로 대용하지 않음 |
| source | `source_namespace`, `source_id`, `source_instance`, `source_generation`, `source_kind`, `source_role`, `source_valid` | event 시점의 current producer source tuple |
| binding | `bound_source_instance`, `bound_source_generation`, `binding_generation`, `binding_valid`, `binding_current`, `kind_match`, `role_match`, `generation_valid` | binding에 캡처된 source와 current source를 분리 |
| lifecycle | `lifecycle_state`, `lifecycle_valid` | 초기 stable string은 `active`, `exited` |
| observation | `observed_at_ns`, `observed_at_valid` | 순서의 정본이 아니며 generation으로 재사용 금지 |
| safety | `observation_only`, `management_only` | H1에서는 항상 integer `1` |

v1 scalar type은 parser 구현에 따라 값이 달라지지 않도록 다음처럼 고정한다.

- `u32`: JSON integer `0..4294967295`; exponent/float/bool 금지
- `flag`: JSON integer `0|1`; Python의 `bool`이 `int`의 하위 타입이어도 bool 금지
- `u64-decimal`: regex `^(0|[1-9][0-9]{0,19})$`를 만족하고
  `0..18446744073709551615` 범위인 JSON string
- enum/token: 문서와 contract manifest에 열거한 ASCII lower-kebab string

`schema_version`, `trace_sequence`, terminal count는 `u32`다. `*_valid`,
`binding_current`, `kind_match`, `role_match`, `producer_owned`, `copied_read`,
`observation_only`, `management_only`는 `flag`다.
canonical/source/parent ID, current/bound source를 포함한 모든 instance/generation과 `observed_at_ns`는
`u64-decimal`이다. valid identity·instance·generation은 0이 아니어야 하며,
`observed_at_valid=0`인 경우에만 `observed_at_ns="0"`을 허용한다. 이 decimal-string
경계는 JSON number의 53-bit 상호운용 모호성을 피하면서 native u64 의미를 보존한다.
`binding_valid=0`인 초기 discover에서는 bound source와 binding generation의 0이
정규 sentinel이며 `generation_valid=1`은 현재 유효한 canonical/parent/source 축에만
적용된다.

instance 소유권도 분리한다.

- `host_instance`: trace 수집 도메인의 boot/session lifetime. H1 trace 안에서는 non-zero,
  불변이며 drift는 host-instance mismatch다.
- `producer_instance`: adapter/producer process lifetime. H1 trace 안에서는 non-zero,
  불변이며 collector restart는 H3에서 새 trace로 증명한다.
- `source_instance`: 관측 대상 source lifetime. exit/recreate 뒤 rediscover에서만 새 값으로
  바뀔 수 있다.

envelope과 claimed result의 초기 값 범위도 exact하게 고정한다.

- `trace_id`: 1..64자 ASCII lower-kebab,
  `^(?=.{1,64}$)[a-z0-9]+(?:-[a-z0-9]+)*$`
- `record_type`: event 행은 `event`, 마지막 terminal 행은 `terminal`
- `event`: `discover|bind|observe|update|exit|rebind`
- `claimed_outcome`: `accepted|rejected`
- `claimed_reason`: accepted이면 `none`, rejected이면 §7.1의 semantic reject string 하나

stale record는 새 event를 만들지 않고 current `source_*`와 더 오래된
`bound_source_*`를 함께 실어
`event=observe claimed_outcome=rejected claimed_reason=stale`로 표현한다. rediscovery는
`event=discover`, 그 뒤 결속은 `event=rebind`로 구분한다.

terminal은 lifecycle event와 다른 exact field set을 가진다.

```text
schema_version
record_type=terminal
trace_id
trace_sequence
host_instance
producer_instance
record_count
accepted_count
rejected_count
final_state
final_binding_generation
observation_only=1
management_only=1
```

terminal은 exact-one이며 반드시 마지막 행이다. transport의 `max_records=64`는 이
terminal도 포함하므로 event는 최대 63개다. `record_count`는 terminal 앞의 event
행 수이고 `trace_sequence=record_count+1`, `accepted_count+rejected_count=record_count`다.
이 count들과 `final_binding_generation`, `final_state`는 producer가 기록하지만 replay가
독립 계산해 exact match를 요구한다. `final_state` 허용값은
`discovered|bound|exited`이며 정규 full lifecycle은 `bound`다. terminal 누락·중복·후행
record와 count/sequence/final-state 불일치는 모두 전체 replay `FAIL`이다.

초기 string mapping은 다음과 같다.

| H1 string | native K2-a numeric 의미 |
|---|---|
| `canonical_namespace=node` | `KERNEL_ROOM_NAMESPACE_NODE=2` |
| `source_namespace=native-slm-agent-tree` | `...NATIVE_SLM_AGENT_TREE=1` |
| `canonical_kind=ai-service` | `KERNEL_ROOM_NODE_KIND_AI_SERVICE=1` |
| `source_kind=ai-service` | `...SOURCE_KIND_AI_SERVICE=1` |
| `source_role=main` | `...SOURCE_ROLE_MAIN=1` |
| `lifecycle_state=active` | `...SOURCE_LIFECYCLE_ACTIVE=1` |

향후 `linux-userspace-service` 같은 source namespace string은 append-only registry로
추가한다. numeric equality나 raw PID/cgroup path로 기존 namespace를 재사용하지 않는다.
native K2-a projection에서는 H1 `bound_source_instance/generation`이 256B binding
record의 `source_instance/generation`에 대응하고, H1 current `source_*`는 SLM producer
source snapshot에서 온다. 같은 숫자라는 이유로 두 축을 하나로 생략하지 않는다.

### 5.1 Validity와 lifecycle truth table

validity/match/ownership field는 producer가 기록한 claim이며 verifier가 event와 누적
state에서 독립 재계산해 exact match를 요구한다. `binding_valid`는 retained binding
record의 구조·generation이 완전하다는 뜻이고 current source와 일치한다는 뜻이 아니다.
현재 결속 여부는 별도 `binding_current`가 나타낸다. 따라서 K2와 마찬가지로 stale
record도 `binding_valid=1 binding_current=0`일 수 있다.

아래는 canonical full-lifecycle fixture의 exact 값이다. `i1/i2`는 source instance,
`gN`은 source generation, `bN`은 binding generation이다.

| event | current source | bound source | binding gen | `binding_valid` | `binding_current` | lifecycle | claim |
|---|---|---|---:|---:|---:|---|---|
| discover | i1/g1 | 0/0 | b0 | 0 | 0 | active | accepted/none |
| bind | i1/g1 | i1/g1 | b1 | 1 | 1 | active | accepted/none |
| observe | i1/g1 | i1/g1 | b1 | 1 | 1 | active | accepted/none |
| update | i1/g2 | i1/g1 | b1 | 1 | 0 | active | accepted/none |
| stale observe | i1/g2 | i1/g1 | b1 | 1 | 0 | active | rejected/stale |
| rebind | i1/g2 | i1/g2 | b2 | 1 | 1 | active | accepted/none |
| observe | i1/g2 | i1/g2 | b2 | 1 | 1 | active | accepted/none |
| exit | i1/g3 | i1/g2 | b3 | 1 | 0 | exited | accepted/none |
| stale observe | i1/g3 | i1/g2 | b3 | 1 | 0 | exited | rejected/stale |
| rediscover (`event=discover`) | i2/g1 | i1/g2 | b3 | 1 | 0 | active | accepted/none |
| rebind | i2/g1 | i2/g1 | b4 | 1 | 1 | active | accepted/none |
| observe | i2/g1 | i2/g1 | b4 | 1 | 1 | active | accepted/none |

모든 행에서 `canonical_valid=1 parent_valid=1 source_valid=1 generation_valid=1
lifecycle_valid=1 kind_match=1 role_match=1 producer_owned=1 copied_read=1
observation_only=1 management_only=1`이다. canonical fixture의 timestamp 미지원 경로는
`observed_at_valid=0 observed_at_ns="0"`이다. 초기 discover만 binding record가 없으므로
`bound_source_instance="0" bound_source_generation="0" binding_generation="0"`이다.
그 뒤 invalid/stale 상태에서도 마지막 retained binding record와 epoch를 버리지 않는다.
exit 행의 `source_valid=1`은 active라는 뜻이 아니라 exited tombstone evidence의 구조와
generation이 유효하다는 뜻이며, active 여부는 `lifecycle_state`로만 판정한다.

## 6. Lifecycle state machine

정규 valid fixture는 다음 의미 순서를 가진다.

```text
discover(i1,g1)
  -> bind(binding g1)
  -> observe
  -> update(i1,g2; binding g1 becomes stale)
  -> observe rejected(stale, current source g2/bound g1, binding g1, state unchanged)
  -> rebind(i1,g2, binding g2)
  -> observe
  -> exit(i1,g3, binding g3 invalidated)
  -> observe rejected(stale, current source g3/bound g2, binding g3, state unchanged)
  -> rediscover(i2,g1; event=discover)
  -> rebind(binding g4)
  -> observe
  -> terminal(final_state=bound)
```

전이 규칙은 다음과 같다.

replay state는 `discovered|bound|exited` 세 값뿐이다. `discover`와 `update` 뒤에는
`discovered`, `bind`/`rebind` 뒤에는 `bound`, `exit` 뒤에는 `exited`다. accepted
`observe`와 모든 expected rejection은 state를 바꾸지 않는다.

1. `discover`는 non-zero producer/source instance와 source generation을 등록하지만
   binding을 암묵적으로 만들지 않는다.
2. `bind`는 존재하는 canonical parent와 discovered active source를 exact-one으로
   묶고 binding generation을 증가시킨다.
3. `observe`는 현재 tuple과 모든 generation이 일치할 때만 성공하며 상태를 바꾸지
   않는다.
4. `update`는 같은 source instance 안에서 current source generation만 엄격히 증가시킨다.
   기존 binding의 `bound_source_generation`과 binding generation은 자동 갱신하지
   않으며, 그 binding은 즉시 stale이 된다.
5. update 뒤 `observe`는 current source와 old bound tuple, current binding epoch를 싣고
   `stale`로 거부되며 replay state를 변경하지 않는다.
   같은 active instance를 다시 쓰려면 current source tuple과 이전보다 큰 binding
   generation으로 명시적 `rebind`를 해야 한다.
6. `exit`는 source generation과 binding generation을 증가시키고 active binding을
   무효화한다.
7. exit 이전 tuple의 `observe`, `update`, `bind`는 current exited source, old bound tuple,
   current invalidated binding epoch를 싣고 `stale`로 거부되며 replay state를 변경하지 않는다. rejected
   probe가 captured old binding epoch까지 재생하지 않는다.
8. 새 source instance는 `discover` 뒤에만 generation 1부터 시작할 수 있다.
9. recreate 뒤 `rebind`는 새 source tuple과 이전보다 큰 binding generation을 요구한다.
10. H1 v1 trace에는 canonical Node/Cell lifecycle event가 없으므로 canonical/parent
    ID와 generation은 처음부터 끝까지 불변이다. target lifecycle은 후속 contract
    version에서 별도 event와 fixture가 생기기 전까지 v1 값 변경을 허용하지 않는다.
11. 예상 밖 거부와 잘못된 성공은 모두 전체 replay `FAIL`이며 state를 변경하지 않는다.
12. exact-one terminal이 마지막 state와 count를 재확인한 뒤에만 replay를 완료한다.

## 7. Reason namespace 분리

### 7.1 Semantic reject strings

H1은 현재 K2-a append-only numeric reason과 일대일로 대응하는 stable string을 먼저
사용한다. 이 mapping은 contract manifest와 host drift test가 소유하며 커널 enum을 H1
구현 편의로 변경하지 않는다.

| native ID | H1 stable string |
|---:|---|
| 0 | `none` |
| 1 | `init-order` |
| 2 | `missing` |
| 3 | `schema` |
| 4 | `malformed` |
| 5 | `overflow` |
| 6 | `duplicate` |
| 7 | `orphan` |
| 8 | `namespace` |
| 9 | `kind` |
| 10 | `role` |
| 11 | `instance` |
| 12 | `zero-generation` |
| 13 | `generation-rollback` |
| 14 | `stale` |
| 15 | `tail` |

`tail`은 native fixed-capacity projection의 의미를 보존한다. JSON transport의 unknown
field나 trailing bytes를 `tail`로 축약하지 않는다.

여러 native semantic 조건이 동시에 깨지면 K2 validator의 검사 순서를 그대로 따른다.
예를 들어 current source보다 낮은 bound source generation과 old binding epoch가 함께 들어오면
`stale`가 binding-generation rollback보다 먼저다. 정규 stale fixture는 이런 다중 위반을
피하려고 current binding epoch와 분리된 current/bound source tuple을 사용한다.

### 7.2 Trace/verdict reasons

transport와 replay 자체의 실패는 별도 `trace.*` namespace를 쓴다.

```text
trace.io
trace.encoding
trace.syntax
trace.duplicate-key
trace.missing-field
trace.unknown-field
trace.type
trace.range
trace.limit
trace.truncated
trace.sequence
trace.trace-id
trace.event
trace.outcome
trace.terminal
trace.host-instance
trace.producer-instance
trace.source-reuse
trace.state-transition
trace.fixture-mismatch
```

semantic reject와 parser failure를 같은 reason으로 합치지 않는다. 첫 실패의 raw line,
record sequence와 계산된/claimed outcome을 machine-readable verdict에 남긴다.

### 7.3 First-reason 우선순위

같은 malformed trace가 여러 조건을 깨도 Ubuntu와 Windows는 같은 first reason을 내야
한다. verifier는 아래 phase 순서를 고정하고, 같은 phase에서는 가장 이른 line과
`contract.json`의 field order를 사용한다.

1. raw boundary: `trace.io -> trace.limit -> trace.encoding -> trace.truncated`
2. JSON: `trace.syntax -> trace.duplicate-key`
3. shape/type: `trace.missing-field -> trace.unknown-field -> trace.type -> trace.range`
4. envelope/terminal: `trace.sequence -> trace.trace-id -> trace.event -> trace.outcome -> trace.terminal`
5. semantic replay: native semantic reason, 그 다음 `trace.host-instance ->
   trace.producer-instance -> trace.source-reuse -> trace.state-transition`
6. sidecar comparison: `trace.fixture-mismatch`

첫 실패 뒤 추가 진단을 수집할 수는 있지만 overall outcome은 다시 PASS로 바뀌지 않는다.
H1 전용 `trace.*` reason에는 이번 준비 커밋에서 numeric ID를 배정하지 않으며 K2의
`0..15`를 재해석하지 않는다.

구현은 한 record에서 여러 native 축이 동시에 깨져도 C validator의 의미 순서를
보존한다. source instance mismatch와 어느 generation 축의 stale도 generation rollback보다
먼저 판정한다. producer가 거부를 주장했다고 trace가 유효해지는 것은 아니며, 정상
lifecycle 안에서 상태 불변으로 허용하는 expected rejection은 exact `stale`뿐이다.

## 8. 최소 fixture matrix

### 정상

- source update 뒤 stale/rebind와 exit 뒤 stale/rediscover/rebind를 모두 포함한 full lifecycle
- current native K2-a의 Node 101/Cell 1/SLM MAIN field projection
- CRLF 입력 parity
- `observed_at_valid=0 observed_at_ns=0`인 timestamp 미지원 경로

native K2-a projection fixture는 현재 immutable observation만 증명한다. synthetic full
lifecycle fixture가 live native exit/rebind를 구현했다고 주장하지 않는다.

### Transport negative

- malformed JSON, duplicate key, unknown/missing field
- blank line, BOM, invalid UTF-8, final newline 누락
- bare `CR`, `CRCRLF`, EOF의 단독 `CR`(마지막 경우에는 `trace.encoding`이
  `trace.truncated`보다 우선)
- oversized line/trace와 record overflow
- bool-as-int, float/exponent, negative/out-of-range integer
- sequence 0, gap, duplicate, reorder와 trace ID drift
- unknown event/outcome/reason
- terminal 누락·중복·non-final, terminal 뒤 record, count/final-state 불일치

invalid UTF-8 fixture는 저장소에 깨진 text file을 직접 보관하지 않고 host test가 임시
byte stream으로 생성한다. 나머지 canonical fixture는 정상 UTF-8 파일로 유지한다.

### Semantic negative

- missing discovery, duplicate canonical/source tuple, orphan parent
- namespace/kind/role/instance mismatch
- zero canonical/parent/current-source/bound-source/binding generation where valid
- v1 trace 안의 canonical/parent ID 또는 generation drift
- same-instance source generation rollback
- source update가 binding을 자동 갱신하거나 stale rejection 없이 통과
- host/producer-instance mismatch와 source identity reuse without valid generation
- exit 뒤 old observation을 accepted로 주장
- source recreate 뒤 rediscover 없는 rebind와 binding generation rollback
- missing validity, `observation_only=0`, `management_only=0`
- event별 truth table과 다른 `binding_valid`/`binding_current`/retained bound tuple
- `producer_owned=0` 또는 current K2 projection의 `copied_read=0`

fixture manifest는 각 파일의 expected overall outcome과 first reason을 소유한다. invalid
trace 안의 `claimed_outcome=rejected`가 곧 fixture PASS를 뜻하지 않는다.

2026-08-31 checked-in matrix는 정상 2개와 반례 10개다. CRLF, invalid UTF-8,
non-finite JSON, limits, bool/float와 조합형 first-reason 반례는 host test가 임시 bytes로
추가 생성한다. `native-k2a-projection` label은 source ID/instance/generation과 binding
generation을 현재 C producer/header 값과 exact 대조하며 임의 synthetic tuple에 붙일 수
없다.

## 9. Replay verdict와 artifact

CLI는 사람용 terminal line과 machine-readable JSON verdict를 분리한다. JSON verdict는
최소한 다음을 가진다.

```text
schema_version
outcome
passed
trace_id
record_count
accepted_count
rejected_count
last_sequence
final_binding_generation
first_failure{reason,line,sequence,detail}
reasons[]
observation_only
management_only
```

원본 trace를 verdict로 덮어쓰지 않는다. fixture 실행은 새 디렉터리에 contract,
manifest, 12개 raw trace, 12개 개별 verdict, aggregate verdict와 provenance를 보존한다.
provenance는 exact input/output byte 수와 SHA-256, clean git HEAD/GitHub SHA,
Python/platform과 verifier SHA-256을 가진다. expected negative fixture의 실행 성공은
"replay가 지정된 이유로 trace를 거부했다"는 뜻이며 trace 자체의 outcome은 `FAIL`이다.

parity loader는 provenance에 적힌 hash만 신뢰하지 않는다. exact file set과 checked-out
contract/manifest/raw fixture/verifier를 대조하고 모든 fixture verdict와 aggregate를 raw
input에서 다시 계산한다. 왼쪽 `Linux`, 오른쪽 `Windows`, clean checkout,
`GITHUB_SHA=HEAD`, `runner_os=platform.system`, CPython 3.11과 동일 HEAD를 요구한다.
누락·손상·위조 bundle은 infrastructure `trace.io`/exit 2, 재생 결과 차이는
`trace.fixture-mismatch`/exit 1이다.

여기서 provenance 검사는 **내용과 선언의 정합성** 검사이지 OS 실행 출처의 암호학적
증명이 아니다. `runner_os`, `platform.system`, git/CI metadata를 모두 함께 재작성한
로컬 묶음만으로 실제 Linux/Windows 실행을 증명할 수 없다. host parity 테스트도 두 OS
metadata를 mock하므로 parser/comparator 회귀 증거일 뿐 cross-OS 실행 증거가 아니다.
H1-c acceptance에서는 동일 GitHub Actions run의 두 named producer job과 parity job,
그 run의 exact SHA 및 해당 job에서 업로드·다운로드한 세 artifact의 출처를 함께
확인해야 한다. 임의로 전달받은 bundle의 단독 `PASS`를 원격 acceptance로 승격하지 않는다.

artifact 전송 무결성도 별도 필수 조건이다. 두 download step은
`digest-mismatch: error`를 명시하고 실패를 `continue-on-error`로 숨기지 않는다.
다운로드 뒤 digest 검사가 실패해도 추출된 파일이 남을 수 있으므로, 그 파일의 replay
성공으로 전송 실패를 지우면 안 된다. 후속 Windows download와 comparator는
`always() && !cancelled()`로 진단을 계속하고 parity artifact는 `always()`로 보존하되,
앞선 download step의 실패는 job 실패로 남긴다. 이 구분은
[download-artifact v8 구현](https://github.com/actions/download-artifact/blob/3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c/src/download-artifact.ts)의
다운로드 완료 뒤 digest mismatch 처리 순서를 따른다.

## 10. 구현 순서와 일정

정본의 1~2주 H1 구간을 다음 세 개의 되돌리기 쉬운 조각으로 나눈다. 각 커밋은 host
tests와 `git diff --check`를 통과해야 하며 빈 scaffold만 커밋하지 않는다.

| 순서 | 조각 | 종료 증거 | 당시 H1 상태 |
|---|---|---|---|
| H1-a — 완료 (2026-08-23) | contract manifest, strict JSONL loader, transport negative tests | duplicate key, truncation, type/limit 반례가 exact reason으로 실패 | `PARTIAL` |
| H1-b — 로컬 완료 (2026-08-31) | lifecycle replay, valid/semantic-negative fixture matrix, native K2-a projection | full lifecycle와 exact native projection PASS, stale/rollback/orphan 등 fail-closed | H1은 계속 `PARTIAL` |
| H1-c 로컬/CI wiring — 구현 완료 (2026-08-31) | fixture manifest CLI, self-contained bundle, 독립 재생 parity, 전용 Ubuntu/Windows jobs | 로컬 12/12와 host suite PASS; forged/same-OS/spoof/tamper 반례 거부 | H1은 계속 `PARTIAL` |
| H1-c 원격 acceptance — 완료 (2026-09-03) | exact SHA Linux/Windows bundle과 parity artifact 확인 | 두 fixture job과 parity job terminal 성공, 12/12 match와 artifact 세트 확인 (§13.2) | `CURRENT` |

H1-a/H1-b/H1-c 로컬 산출물은 구현됐고 host suite는 기존 `os-tools-matrix` 양 OS에서
실행된다. fixture bundle은 무관한 OS-tool 실패와 결합하지 않도록 전용 Ubuntu/Windows
job에서 만들며, 별도 parity job이 두 producer job만 `needs`로 받는다. 이 wiring의
원격 결과는 §13.2에서 확인했다. 어느 단계도 빈 `hosted/linux/`를 만들지 않는다.

H1-c 원격 acceptance gate는 완료됐다. 다음 첫 H2 slice는 Linux kernel module이
아니라 action이 전부 `UNSUPPORTED`인 한
observe-only userspace service다.

## 11. 현재 및 예정 검증 명령

현재 H1-a/H1-b/H1-c 및 공통 회귀 변경의 최소 host gate는 다음과 같다.

```powershell
py -3 -m unittest discover -s tools/hosted/tests -p "test_*.py" -v
py -3 tools/platform/linux_resource_guard.py
py -3 -m unittest discover -s tools/platform/tests -p "test_*.py" -v
py -3 -m unittest discover -s tools/testkit/tests -t tools/testkit -p "test_*.py" -v
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/testkit/tests/test_build_windows_verdict.ps1
git diff --check
```

2026-09-03 현재 hosted suite는 132개다. 이 수치는 로컬 verifier/fixture/artifact
구현의 회귀 증거이며 원격 cross-OS acceptance나 live runtime 증거로 승격하지 않는다.
추가된 두 workflow 정적 계약 테스트는 다운로드 실패 은폐 금지와 실패 뒤 진단 보존을
검사한다. 일반 YAML validator와 실제 runner 동작 증거는 아니다.
추가 입력 회귀는 byte 한도 안의 깊은 JSON 중첩을 `trace.syntax`로 거부하고,
contract/manifest/bundle JSON의 decoding 실패를 infrastructure 오류로 보존한다.
잘못된 trace ID는 verdict metadata로 승격하지 않으며, surrogate가 포함된 실패
진단도 UTF-8 JSON 출력과 artifact에 안전하게 escape한다. 사람용 trace/parity 실패
detail도 같은 surrogate 경계를 보존한다.

fixture-manifest 실행은 새 artifact 디렉터리를 필수로 요구한다.

```powershell
py -3 tools/hosted/binding_trace_replay.py `
  --fixture-manifest hosted/contracts/fixtures/manifest.json `
  --artifact-dir build/hosted-binding-trace/manual --json
py -3 tools/hosted/binding_trace_replay.py `
  --compare-fixture-bundles <linux-bundle> <windows-bundle> `
  --artifact-dir build/hosted-binding-trace/parity --json
```

H1이 host-only contract/verifier만 바꾸는 동안 QEMU baseline과 boot marker는 갱신하지
않는다. kernel/K2 producer가 바뀌는 별도 조각에서만 strict QEMU, PowerShell direct
verdict, shell과 inventory lane을 다시 요구한다.

## 12. 2026-08-15 공식 자료 재검토

- [kernel.org signed release index](https://www.kernel.org/pub/linux/kernel/v6.x/)에서
  primary `6.12.103`과 forward `6.18.44`가 각각 해당 series의 최신 exact patch임을
  재확인했다.
- [QEMU 공식 download page](https://www.qemu.org/download/)에서 2026-08-11 정식
  `11.1.0` release를 확인해 H0 exact source baseline을 이전 exact release에서 11.1.0으로
  갱신한다. 이것은 runtime qualification이나 support 승격이 아니다.
- [pidfd_open(2)](https://man7.org/linux/man-pages/man2/pidfd_open.2.html)은 task를
  가리키는 file descriptor와 exit 관측을 제공한다. pidfd는 Linux source lifetime
  evidence이며, process-local FD 숫자 자체를 canonical Node/source instance로 저장하지
  않는다.
- [cgroup v2 정본](https://docs.kernel.org/admin-guide/cgroup-v2.html)은 process를
  계층적으로 조직하고 resource를 배분하는 Linux interface다. process migration과
  controller lifecycle이 있으므로 cgroup path/object를 AIOS Cell identity로 재명명하지
  않는다.
- [PSI 정본](https://docs.kernel.org/accounting/psi.html)은 CPU, memory, I/O stall
  pressure를 관측한다. PSI 값은 source observation이며 NodeBit eligibility,
  authorize 또는 자동 throttle verdict가 아니다.

모든 Linux/QEMU row는 계속 `source_only` 또는 `identity_semantics=none`,
`code_import=false`다. 이 조사로 upstream code import, Linux backend 실행 또는
license compatibility 승인이 생기지 않는다.

## 13. 유지·acceptance 체크리스트

- K1/K2 public ABI와 exact marker를 변경하지 않는가
- evidence trace와 fixture expected verdict가 분리됐는가
- 모든 generation과 instance의 소유 축이 이름으로 드러나는가
- PID, pidfd, cgroup, path, timestamp를 canonical identity/generation으로 쓰지 않는가
- expected rejection 뒤 replay state가 불변인가
- duplicate key, truncation, unknown field, bool-as-int가 실패하는가
- contract/manifest/verdict/aggregate에서 `true`, integer, float의 JSON 타입 drift가
  일반 값 동등성으로 통과하지 않는가
- update 뒤 old generation은 stale이며 명시적 rebind 전에는 다시 관측할 수 없는가
- stale old instance 뒤 rediscover 없이는 rebind할 수 없는가
- Ubuntu/Windows가 같은 fixture outcome과 first reason을 내는가
- H1 완료가 H2 runtime/apply 성숙도로 과장되지 않는가

### 13.1 2026-09-02 로컬 closure와 남은 원격 gate

| 요구 | 현재 직접 확인한 근거 | 남은 조건 |
|---|---|---|
| lifecycle/retained binding/first reason | hosted host suite 122개, full-lifecycle/native projection과 semantic negative | 현재 로컬 코드의 원격 양 OS 회귀 |
| fixture expected/raw 분리 | checked-in manifest 12개, 로컬 12/12 exact match | 같은 exact SHA에서 두 producer job 실행 |
| 재현 가능한 입력·출력 묶음 | 로컬 bundle의 입력 14개·출력 13개, verifier SHA-256 일치 | clean producer checkout의 원격 bundle 두 개 |
| checkout 바이트 안정성 | contract/manifest/JSONL/verifier의 `git check-attr` 결과 `text`/`eol=lf` | 실제 Ubuntu/Windows checkout 간 같은 input hash |
| 독립 parity 판정 | 재생 comparator와 spoof/tamper/type-drift host 반례 | 동일 Actions run의 parity terminal 성공과 artifact 보존 |
| artifact 전송 실패 유지 | download 실패 은폐 금지 및 후속 진단 보존의 workflow 정적 계약 2개 | 실제 download/action step 성공과 세 named job terminal 확인 |
| ABI/source-only 경계 | `kernel/` 실행 코드와 public header 변경 없음, `os/` 변경은 README뿐 | 후속 조각에서도 경계 유지 |

로컬 확인 묶음은 `build/hosted-binding-trace/manual-qemu-helper-closure-20260902/`다.
이 묶음은 Windows에서 dirty worktree로 만들어졌으므로 원격 parity acceptance 입력이
아니다. CI 정의에 사용한 `checkout@v6`, `setup-python@v6`, `upload-artifact@v7`,
`download-artifact@v8` tag의 존재는 GitHub API로 확인했지만 실제 workflow 성공을
뜻하지 않는다. 2026-09-02 사용자가 beta 검증 후 동일 SHA의 main 승격까지 승인했다.
당시 남은 동작은 beta checkpoint/push와 새 exact SHA의 두 fixture job·parity job
terminal 및 artifact 확인이었고, 아래 §13.2에서 이 gate를 닫았다. main은 별도의 전체
CI 검증을 통과한 beta SHA로만 fast-forward한다.

### 13.2 2026-09-03 원격 acceptance 완료

H1-a/b/c의 host-only contract/replay/fixture/artifact 경계를 `CURRENT`로 승격한다.
검증한 구현 SHA는 `e6c7979b9114d32e5f2e28536d307e9cccaf8381`이며
[GitHub Actions run 33688967921](https://github.com/tjwlstj/aios-kernel/actions/runs/33688967921)의
다음 세 named job이 같은 SHA에서 terminal `success`를 기록했다.

| Job | 결과 | 해당 run의 artifact |
|---|---|---|
| `hosted-binding-trace-fixtures-ubuntu` | 12/12 fixture match, terminal success | `hosted-binding-trace-Linux` (ID `9869240544`) |
| `hosted-binding-trace-fixtures-windows` | 12/12 fixture match, terminal success | `hosted-binding-trace-Windows` (ID `9869251261`) |
| `hosted-binding-trace-parity` | 독립 재생 12개 parity PASS, terminal success | `hosted-binding-trace-parity` (ID `9869258340`) |

- parity의 Linux/Windows download, compare와 upload step도 모두 성공했다.
  `digest-mismatch: error`를 완화하거나 실패를 무시한 결과가 아니다.
- 두 producer의 `git.dirty=false`, `HEAD=GITHUB_SHA`, 정확한 Linux/Windows provenance,
  입력 14개·출력 13개, verifier SHA-256 및 모든 bundled input hash가 검증됐다.
  이 구현의 verifier SHA-256은
  `9355bfd6beb49a298fe2a0ebd7c846ec2406866faae037123e386725d2dbacad`다.
- 세 원격 artifact를 `build/hosted-binding-trace/ci-33688967921/`에 내려받고,
  같은 clean checkout에서 comparator를 다시 실행했다. 로컬 독립 재생도 12개 PASS이며
  `ci-33688967921-local-parity/` 결과와 원격 parity verdict가 일치한다.
- 위 run의 Ubuntu/Windows host suite도 132개 회귀를 통과했다. 이 증거는 Ubuntu
  runner의 H1 host 검증이며 primary Linux backend qualification은 아니다.
- 같은 run의 static-analysis, 두 OS-tools matrix와 build-and-boot를 포함한 전체
  7개 job도 terminal `success`를 기록했다.
- 이 완료를 반영하는 문서·contract maturity metadata 커밋은 입력 hash와 SHA가
  바뀌므로 beta에서 같은 CI/artifact gate를 다시 통과해야 main 승격 대상이 된다.

H2 userspace service, live native producer/K2 reconciliation, resource attribution,
authorize/apply, Linux qemu-mcp 실제 E2E는 이 완료에 포함되지 않는다. 다음 직접 조각은
H2 bounded observe-only service이며 backend 구현은 계속 `PLANNED`다.
