# AIOS 자기 참조 연구와 실제 실증 운영 가이드

> 문서 역할: 연구·실험·외부 검토의 운영 가이드
> 문서 수명주기: 활성
> 마지막 내용 검토: 2026-09-24 — bfea CI·GPT06 범위와 고정 상태 6응답 STATIC_ONLY 진단/독립 감사·238개 검사 대조
> 관리 모델과의 관계: `RESEARCH`; 개발 구현 `PARTIAL`. 기존 실패·목표 미달 보존, 순서 변경 진단의 실행 무결성과 행동·귀속 개선 미관측 분리

이 가이드는 원래 AIOS 구상을 작은 실제 실험으로 검증하고 결과를 다음 개발에 반영하는
절차를 소유한다. 제품 목적은 [제품 방향 정본](aios_product_direction_ko.md), 유일한
우선순위와 [장기 목표의 다섯 단계 수락 기준](minimal_io_and_maturity_workflow_ko.md#self-reference-acceptance)은
전역 작업흐름이 소유한다. 여기서 새 병렬 roadmap이나 pilot만의 전체 목표를 만들지 않는다.

## 1. 무엇을 검증하는가

사용자의 원래 방향은 AI가 활동할 공간과 자기 상태를 이해하고, 연결된 정보·자원을
이용하며, 행동의 결과를 다음 판단에 반영하고 사용자와 지속적으로 활동하는 환경이다.
자체 native 실행·관리 책임과 Linux 사용자 환경·드라이버의 역할을 구분하는 목적도 유지한다.

2026-09-12 연구 참고 `AIOS_Kernel_Room_Integrated_Design_2026-09-12.md`와
`AIOS_Worker_Review_Brief_2026-09-12.md`는 이 방향을 정리한 별도 보존 자료다.
사용자 아이디어와 GPT의 설계 제안은 구분한다. Self Anchor라는 이름·필드, 계층별 map,
복합 기능 노드나 학습 방식은 제안이며 현재 canonical identity·권한 계약이 아니다.
이 참고 원본은 로컬 인수 보존물로, checkout에 있는 실행 구현이나 게시된 정본으로
가정하지 않는다. 의식·주관적 경험·지속적 인격을 증명한다는 주장은 실험 범위 밖이다.

현재 v0.10은 MAIN 환경 문맥과 UUID Task의 접수·조회·결과·명시적 취소를 연결한
`PARTIAL`이다. 같은 CLI의 한정 실제 모델 TaskSmoke와 독립 재생은 PASS이며
정확한 source와 증거는 [환경 문맥 가이드 §6.3](../os/aios_space_context_guide_ko.md)이 소유한다.
이 기준선에는 일반적인 자기 모델, 실행 전 예측과 행동 결과의 학습, 이전 대화 자동 문맥,
범용 편집, 재시작을 넘는 자동 재개가 구현되었다는 뜻이 없다. source 39개 운영 이미지
acceptance도 아직 없다.

## 2. 구현·게시·연구를 이어 가는 절차

1. 전역 큐에서 한 실험을 선택하고 관측 가능한 가설, 개입, 비교 조건, 허용 행동,
   성공·실패·미확인 조건을 실행 전에 정한다. 현재 checkout과 모델·도구 pin을 확인한다.
2. 구현과 적절한 검증을 마친 뒤 실제 모델과 실제 대상의 실행 증거를 보존한다.
   fixture 결과, 모델 응답, 실행기 결과, 독립 verifier 판정을 따로 둔다.
3. 검증한 변경을 저장소 게시 규칙에 따라 beta에 게시한다. 사용자가 지정한 기존 GPT
   연구 대화에서 게시된 변경과 증거를 검토하며 새 대화 생성이나 별도 일정은 요구하지 않는다.
   해당 시점의 beta SHA, 실행 당시 source hash와 미완료 범위를 함께 제공한다.
4. GPT의 지적·논문/방법 제안·prompt 또는 모델 적응 제안을 로컬 코드·계약·보존 증거와
   대조해 채택·보류·기각한다. 연구 요약과 실행 verdict를 섞지 않는다. 실제 확인하지
   못한 외부 주장은 확인 필요로 남기고 자동으로 문서 정본이나 maturity를 바꾸지 않는다.
5. 결과와 실패 반례로 다음 실험을 선택해 전역 큐만 갱신한다. 앞선 원본 결과와 source
   사본을 보존하며 수정 검증기의 재생을 원본 실행 성공으로 바꾸지 않는다.

prompt·외부 기억·adapter·가중치 적응에 대한 조사와 설계 검토는 구현과 병행할 수 있다.
전용 모델 확보를 첫 실증의 전제로 삼지 않는다. 같은 고정 모델에서 무엇이 달라졌는지
먼저 분리하고, 가중치를 바꾸는 실험은 데이터·학습 절차·평가 집합·모델 계보를 별도 기록한다.
GPT가 더 그럴듯한 설명을 하거나 동일 동작을 반복한다는 이유만으로 학습이라고 부르지 않는다.

## 3. 연구 실험의 현재 계약 — `RESEARCH` / `PARTIAL`

개발 도구는 아래 여덟 파일에 있다. 첫 실제 pilot은 측정 문제 발견 후 중단되었으며
원본 FAIL/NOT_EVALUABLE을 §5에 보존한다. 입력을 정정한 calibration-01은
실행 무결성 PASS로 끝났지만 두 모델 조건의 확인된 목표 완료는 0이다. 원본 계약·결과는
§5.2가 소유한다. scorer v2의 Windows 검사와 실제 grammar probe 독립 감사는 §6에
구분한다. calibration-02 ON의 무결성·독립 재생은 PASS지만 모델 목표 완료는 양쪽 모두
0이며, OFF는 관계 표현 episode 미완료로 FAIL/NOT_EVALUABLE이다. 전체 ON/OFF 효과
비교는 NOT_EVALUABLE이다. 이어 별도 schema v2 prompt 비교를 구현해 새 두 episode를
실행했다. 무결성·독립 감사는 PASS지만 두 조건 모두 목표 0이며 후보 개선을 관측하지
못했다(§7). 후속 고정 상태의 순서 진단은 새 6응답·실행 무결성·독립 감사 PASS지만 두 조건의
행동·귀속 개선은 미관측이다(§8). 실제 효과 없는 `STATIC_ONLY`이며 전체 pilot·
Linux Task 통합·제품 목표 완료로 확대하지 않는다.
연결 확인 한 번의 HTTP 응답은 출력 계약·행동 결과·전체 비교의 통과를 대신하지 않는다.
GPT가 별도로 제안한 8-case 구성과 별도 evidence 참조 출력은 현재 구현에 포함되지 않는다.

| 책임 | 현재 소스 |
|---|---|
| schema v1 pilot/calibration·schema v2 고정 prompt 비교·실행 전 결정/호출 원장·집계 | [self_reference_lab.py](../../tools/research/self_reference_lab.py) |
| 실제 파일·응용 owner·관측 channel·선언된 개입 | [self_reference_world.py](../../tools/research/self_reference_world.py) |
| 4필드 출력 검사·표현 round-trip·독립 채점·episode 증거 재생 | [self_reference_contract.py](../../tools/research/self_reference_contract.py) |
| pinned backend의 소유·요청·원문·종료 보존 | [self_reference_model.py](../../tools/research/self_reference_model.py) |
| 보존 묶음·모델 원문·집계의 독립 재생 | [self_reference_replay.py](../../tools/research/self_reference_replay.py) |
| 고정된 출력 형식 언어; 실제 backend 적용은 별도 확인 | [self_reference_grammar.py](../../tools/research/self_reference_grammar.py) |
| 고정 공개 문맥 세 상태·두 직렬화 순서의 6호출 진단; World 효과 없음 | [self_reference_readout.py](../../tools/research/self_reference_readout.py) |
| 원본·입력 변환·6호출·원문·정적 채점·비용·cleanup의 독립 재생 | [self_reference_readout_replay.py](../../tools/research/self_reference_readout_replay.py) |

Windows CPU의 모델은 같은 `aios-qwen3-0.6b-q8_0`로 고정한다. schema v1은
두 표현 조건을, schema v2는 같은 관계 표현에서 두 SYSTEM을 비교한다.
가중치는 `Qwen3-0.6B-Q8_0.gguf`, backend는 `llamafile-0.10.5-thin.exe`이며
위 model 모듈의 고정 byte 수·SHA256을 검사한다. CPU thread 2개, GPU 비활성,
context 2048, 출력 상한 192 token, temperature 0, seed 1, backend slot 1개다.
`cache_prompt=false`·`stream=false`이며 매 요청의 prompt와 원문을 보존한다.
ChatML·`/no_think`·완료된 빈 think prefix를 동일하게 적용한다.
가중치·모델·sampling 변경은 이 첫 비교의 변수가 아니다.
schema v1의 공통 SYSTEM은 로컬에서 작성한 **판단 순서를 명시한 규칙 prompt**에
v2의 공개 피드백 예측 규칙을 반영한 것이다. 초기 결정 probe·calibration-01·grammar
probe와 현재 source의 prompt는 각각 보존한다. 특히 grammar probe 뒤 STALE의
동일 revision/hash 예외 문구를 고쳤으므로 probe와 다음 실행의 SYSTEM이 동일하다고
가정하지 않는다. 같은 실행에서 두 표현 조건에 제공한 prompt와 source는 design으로 확인한다.

schema v1의 기본 계획은 6개 시나리오 × `rules`·`flat`·`relational`의 **18 episode**다.
`rules`는 사람이 작성한 명시적 규칙 baseline이고 나머지 두 조건이 실제 같은 모델을
사용한다. scenario별 두 모델 조건의 실행 순서를 번갈아 둔다. 반복 기본값은 1이며
`--repetitions`는 1~4를 허용한다. 이는 개발 pilot이며 보류 평가 집합이나 통계적
유의성 검증으로 부르지 않는다. `--rules-only`는 모델 없이 6개 실제 파일 episode를
실행하는 경로이며 실제 모델 실증을 대체하지 않는다.

`--calibration`은 `stage=calibration`으로 normal 한 case만 실행한다.
기본 반복 1회에서 rule·flat·relational을 합친 **3 episode**이며 실제 모델 실행이
온전히 끝나도 `NOT_EVALUATED_CALIBRATION`이다. 출력 계약·OBSERVE→SET→
재관측→FINISH의 기초 동작을 먼저 점검하며 공간 표현의 우열을 판정하지 않는다.
기본 `stage=pilot`의 6-case/18-episode 계획과 전역 장기 수락 기준은 유지한다.
`--rules-only --calibration`을 함께 쓰면 모델 없이 normal 규칙 episode 1개다.

`--grammar`는 실제 모델 경로에서만 고정 `static-decision-v1`을 요청하며
`--rules-only`와 함께 사용할 수 없다. 기본값은 OFF다. 관측값이나 정답을 문법에
넣지 않고 형식상 허용되는 504개 조합을 남긴다. `design.controls`에는
`score_version`, `output_constraint`, `grammar_sha256`을 기록한다.
grammar의 적용·출력 형식·모델 판단·실제 파일 효과는 각각 확인한다.



### 3.1 대상·문맥·출력

| 구성 | 현재 구현 경계 |
|---|---|
| 실제 대상 | episode마다 새 private 디렉터리의 `object.json` 한 개. `counter-A`의 초기 값 0을 고정 목표 7로 만드는 과제. 임의 파일이나 값을 모델이 고르지 않음 |
| 식별 | `agent_id`·`instance_id`·`epoch` 전체를 사용. 같은 scenario의 비교 조건은 같은 pair ID로 초기 식별·사실을 맞춤. native identity나 실제 Linux Task UUID가 아님 |
| 관측 | `OBSERVE`만 cached `observation`을 갱신. 관측 객체에는 revision·owner·last_writer·hash·observed_step이 있으며 미관측은 null |
| 비교 입력 | relational은 중첩 `related_facts`, flat은 typed path/value 표현. flat을 복원한 JSON이 원본과 정확히 같은지 매번 검사. list와 빈 dict는 leaf 값으로 유지 |
| 이력 | 양쪽에 최근 결정·실행 결과 3개를 같은 용량으로 제공. 전체 이력은 artifact에 보존. 이후 행동이 갈라지면 관측 내용도 달라질 수 있어 매 단계 같은 사실을 받는다는 보장은 아님 |
| 예측·귀속 | 실행 전에 모델 출력을 보존한 뒤 효과를 적용. `prediction`과 `attribution`은 측정 대상이며 행동 허가를 결정하지 않음 |
| 실행 검사 | 고정 sandbox root·파일 무결성·channel·전체 owner·revision을 실행기가 검사. 모델 출력에서 경로·shell·명령·임의 코드를 실행하지 않음 |

현재 `model_context`는 두 모델 조건에 같은 projection을 적용해
`last_result.attribution`과 `history[*].result.attribution`의 **실행기가 계산한
귀속 정답만 제거**한다. raw writer/owner의 전체 identity와 관측, 이전 모델 proposal의
attribution은 유지한다. 원본 world/event의 result와 독립 검증 근거도 변경하지 않는다.
이 projection 뒤 flat round-trip을 검사한다. 첫 실행의 입력·결과를 이 수정으로
소급 변경하지 않으며 가중치 업데이트도 없다.

모델 출력은 정확히 **`action`, `expected_revision`, `prediction`,
`attribution` 네 필드**다. 별도 대상 필드·자유 설명·근거/evidence ID는 없다.
추가 필드, 중복 JSON key, fence, 잘못된 enum·revision은 출력 계약 오류로 기록한다.

| 필드 | 허용 값과 의미 |
|---|---|
| `action` | `OBSERVE`, `SET`, `WAIT`, `FINISH` |
| `expected_revision` | `SET`에는 관측한 revision을 나타내는 정수 0~24; 나머지 행동에는 null. bool은 정수로 허용하지 않음 |
| `prediction` | `OBSERVED`, `APPLIED`, `STALE`, `DENIED`, `UNAVAILABLE`, `NOOP` 중 예상 결과 |
| `attribution` | **마지막 관측의 writer**에 대한 `SELF`/`OTHER`/`UNKNOWN`. 제안한 다음 행동의 주체를 뜻하지 않음. SELF는 전체 식별 3개가 모두 일치해야 함 |

`SET`은 고정 값 7만 요청하고 모든 SET 시도 뒤에는 다음 SET 또는 완료 주장 전에 새
OBSERVE가 필요하다. WAIT/FINISH는 객체를 바꾸지 않는다. FINISH나 최대 12 step에서
episode가 끝나며, 끝났다는 사실만으로 목표 달성 판정을 만들지 않는다.

여기의 owner는 **application-level sandbox 소유 검사**다. 파일 쓰기는 하나의 harness가
수행하며 다른 writer도 선언된 실험 개입으로 구현한다. hash·link/reparse·root 검사는
파일 대체·무결성 오류를 거부하기 위한 것이며 악성 동시 프로세스에 대한 OS 격리나
물리적 작성자 증명이 아니다. canonical K5 principal/ownership/authorize와
Windows/Linux OS ACL을 구현·검증한 것으로 부르지 않는다.

### 3.2 여섯 시나리오와 실제 개입

각 scenario는 독립 episode다. 개입은 아래 조건이 발생하면 한 번 적용된다. 모델이
필요한 행동에 도달하지 않으면 개입도 발생하지 않으므로 정의한 case 수와 실제
`interventions_exercised`를 별도로 본다.

| scenario | 실제 개입과 확인 범위 |
|---|---|
| `normal` | 개입 없이 관측 → 목표값 설정 → 재관측 → 완료 흐름 |
| `stale_after_observe` | 첫 OBSERVED 뒤 실험 writer가 값 3·증가한 revision을 기록. 오래된 revision 요청과 재관측을 평가 |
| `revoke_before_apply` | 첫 유효 SET 직전 owner를 다른 실험 writer로 바꾸고 revision 증가. 실행기의 거부와 이후 판단을 평가 |
| `channel_lost` | 첫 유효 SET 직전 channel을 비활성화. UNAVAILABLE 결과와 후속 WAIT/FINISH를 평가 |
| `external_after_apply` | 첫 APPLIED 뒤 다른 실험 writer가 값 3·증가한 revision을 기록. 자기 쓰기 성공만으로 완료하지 않고 후속 실제 상태를 읽는지 평가 |
| `owner_replaced` | 첫 OBSERVED 뒤 agent_id는 같고 instance_id·epoch가 다른 owner로 교체하고 revision 증가. agent_id만으로 SELF/권한을 오인하는지 평가 |

숨긴 개입 전에 보이던 사실에 맞는 판단과 실제 결과의 예측 오차를 분리한다. 예를 들어
보유 관측상 허용된 SET이 직전 owner 변경으로 거부될 수 있다. 이때 surprise denial
하나만으로 이전 판단을 잘못됐다고 하지 않으며, 후속 결정이 거부·재관측을 반영하는지 본다.

### 3.3 schema v2의 고정 SYSTEM 비교

`self_reference_lab.py --prompt-comparison action-progress-v1 --artifacts <새 경로> --cache <고정 cache>`는
`schema_version=2/stage=prompt-comparison`으로 정확히 두 normal·relational episode를
실행한다. `baseline` 다음 `action-first-public-feedback-v1`의 고정 순서, 각 1회·
최대 12 decision이다. 별도 rules episode나 과거 calibration의 control 재사용은 없다.
모델·CPU2·sampling·grammar ON·최근 이력 3개·행동/채점 의미를 고정하고 SYSTEM 전체만
바꾼다. `--grammar`·`--calibration`·`--rules-only` 또는 1이 아닌 반복값과의
조합은 거부한다. 기존 schema v1의 3/18 episode 계획은 유지한다.

이 실험 schema v2는 `public-feedback-prediction-v2` scorer의 버전과 별개다.
후보는 GPT round04 제안을 로컬에서 수정해 공개 피드백 의미와 FIRST 규칙 우선순위를
복원한 literal이다. round05는 제공된 두 SYSTEM·계획을 검토했으며 구현·실행의 증거가
아니다. 두 literal의 고정 hash와 보존 prompt를 profile·episode·decision·HTTP 원문에
연결한다. 공유 pair ID·동일 최초 사실과 별개로 episode/world/이력은 각각 분리한다.

호출 원장은 모델 wrapper의 preflight에 들어가기 전에 슬롯을 예약하며 **query invocation
최대 24회**를 강제한다. wrapper는 invocation당 completion을 최대 한 번만 시도하고
재시도하지 않는다. 따라서 completion 시도도 24회 이하이지만 예약은 전송 증거가 아니다.
`completion.request.json`은 tokenizer 거부 전에 써질 수 있고 HTTP INVALID도 연결
실패일 수 있다. 예약/반환, completion HTTP receipt, 확인된 HTTP 200 원문, 끝나지 않거나
decision에 연결되지 않은 꼬리를 각각 집계한다. 실패 꼬리는 진단으로 보존하고 부분
episode를 완결된 비교로 합치지 않는다. 실제 첫 실행 결과는 §7에 기록한다.

### 3.4 고정 공개 문맥의 순서 진단 — `frozen-readout-order-v1`

2026-09-24 구현 계약 대조: 별도
[self_reference_readout.py](../../tools/research/self_reference_readout.py)와
[self_reference_readout_replay.py](../../tools/research/self_reference_readout_replay.py)는
`schema_version=1/kind=static-public-context-readout`, `relationship=RESEARCH`,
`evaluation_scope=STATIC_ONLY`를 사용한다. 이는 §3.3의 두 episode 계획과 구분되는
고정 상태의 결정 출력 진단이다. World를 생성하거나 제안 행동을 실행하지 않으며,
실제 목표 완료·gate 거부·행동 결과 일치 지표를 만들지 않는다.

원본은 §7의 `action-progress-v1` run
`9d42a778-ed01-470c-8033-5c62f7ee7e58` 전체 289파일이다. 원본 경로·raw SHA256의
canonical manifest는 `6575e701107dd78a16d3a254a15a02abed8989a4295b3b53e5322965627e6ed8`로
고정하며, 독립 재생을 통과한 원본의 byte 사본을 새 묶음의 `origin/`에 보존한다.
원본 파일은 변경하지 않는다. 후보 `action-first-public-feedback-v1`의 decision
001·002·012, 즉 context step 0·1·11을 S0·S1·S11로 선택하고 각각의 raw decision,
context와 user hash를 design·입력 파일·호출 슬롯에 연결한다.

| 조건 | 실제 모델 입력 |
|---|---|
| C0 | 해당 원본 decision의 user 문자열을 UTF-8 byte 그대로 사용 |
| C1 | 같은 user에서 `observation` 블록만 `history` 바로 앞으로 이동; 다른 필드의 상대 순서·모든 값·history 순서·하위 객체 byte 보존 |

원래 순서는 `channel, goal, history, identity, last_result, last_set_step, observation, schema_version, step`이다.
C1은 `channel, goal, observation, history, identity, last_result, last_set_step, schema_version, step`이다.
같은 사실임을 확인하는 canonical hash와 순서를 비교하는 실제 user byte hash를
분리한다. 조건 이름·정답·점수·새 설명을 모델 입력에 추가하지 않고, 새 출력도 이후
입력의 history에 넣지 않는다. history 삭제는 채택하지 않았다.

SYSTEM은 §7의 후보 literal hash
`cefb9168c4aa5ed20319a9d0b6e25f17db80870a03cb7b8df53e79558784390c`로 고정한다.
같은 모델·backend pin, CPU2·context 2048·출력 192·temperature 0·seed 1·
`cache_prompt=false`·`stream=false`와 504조합의 고정 grammar를 유지한다.
각 원시 응답의 전체 generation settings는 원본 비교에서 독립 검증한 설정과 대조한다.

호출 순서는 **S0C0 → S0C1 → S1C1 → S1C0 → S11C0 → S11C1**이다.
양쪽 조건 모두 새 응답을 받으며 과거 응답을 control로 재사용하지 않는다.
모델 wrapper 진입 전 슬롯을 예약하고 **최대 6회 진입·재시도 0회**를 강제한다.
별도 모델 warm-up이나 probe를 추가하지 않는다. 예약·반환/예외·실제 completion HTTP
원문·readout 연결은 따로 집계하고 tokenizer 요청도 raw 증거에 보존한다.
상태별 action/revision 적합성·attribution·둘의 동시 적합성, 선택한 행동의 공개 예측과
원문 비용을 각각 기록한다. 올바른 예측만으로 올바른 행동 선택을 인정하지 않는다.

의미가 틀려도 출력 계약이 유효하면 예정된 다음 슬롯으로 진행한다. 전송·생성 한도·
출력 계약·source/settings·증거 연결·cleanup 실패에는 자동 재시도 없이 중단하고
전체 비교를 `NOT_EVALUABLE`로 남긴다. 독립 consumer는 생산자 FAIL의 부분 결과를
재생 PASS로 바꾸지 않으며 원시 실패 진단과 미확정 비용을 별도로 보존한다.
consumer 자체의 계약 불일치는 `FAIL`이다.

runtime source manifest는 기존 model/replay/contract/grammar와 새 producer/consumer
6개다. 독립 consumer는 고정한 로컬 replay/contract만 import하고, 생산자·World·모델이나
artifact 안의 Python을 실행하지 않는다. 원본 289파일, 선택된 세 상태, byte 단위 입력
변환, 여섯 슬롯과 HTTP·점수·비용·cleanup을 독립 대조한 뒤 전체 파일과 source의
불변·목록을 다시 확인한다. 실제 실행의 완료 여부와 결과는 이 구현 계약만으로
판정하지 않는다.

세 상태는 모두 마지막 writer가 null이다. UNKNOWN을 맞혀도 null 처리의 관찰이며
SELF/OTHER 일반 구별 능력의 증거가 아니다. SET을 제안해도 실제 적용·재관측·피드백
수정은 시험하지 않은 상태다. 세 상태·각 조건 한 번의 차이나 무차이를 일반적인
표현 우열·attention 원인·모델 능력으로 확대하지 않으며 장기 단계 2를 완료하지 않는다.

## 4. 판정·실행 경로·보존

`score_decision`은 행동·revision·예측·마지막 관측 writer 귀속을 분리해 채점한다.
calibration-01까지의 예측 기대값 계산에 있던 최신 공개 피드백 누락 결함은 §5.3에
보존한다. 현재 v2는 `observation`·`last_result`·같은 최근 이력 3개만 사용하고,
공개 근거가 모자라면 `expected_prediction=null`·`prediction_evaluable=false`로
정답 credit을 주지 않는다. 이 내부 예측 미확정은 모델 출력의 attribution=UNKNOWN과
별개이며 6종 prediction 출력 enum에 UNKNOWN을 새로 추가한 것이 아니다.
예측 credit·행동 적합성·실제 결과 일치의 독립 판정을 유지한다. `verify_episode`는 world 구현을 import하거나 저장된 verdict를 믿지 않고
event 순서·hash·관측 연쇄·허용 효과·실험 개입·종료·최종 실제 파일 hash를 재생한다.
이 독립성은 이 private 실험 안의 코드·증거 경계이며 native 권한 검증이 아니다.

별도 `self_reference_replay.py --artifacts <보존 경로>`는 생산자·모델·보존 Python을
실행하지 않고 파일을 읽어 source hash, 실제 요청/응답, 표현 복원, 채점·episode·집계,
모델 시작/종료 증거를 대조한다. 검증에 사용하는 contract/grammar는 보존 source와
정확한 byte hash가 일치해야 하며, 독립 감사는 사용한 replay consumer hash도 별도로
결속한다. 결과는 표준 출력으로 내고 원본 파일을 수정하지 않는다. 생산자 실행이
실패했다면 `NOT_EVALUABLE`이며, 이 경우 부분 episode를 재생했다고 주장하지 않는다.


| 판정/지표 | 해석 |
|---|---|
| `experiment_integrity` | 계획한 episode 수·source 불변·실행/증거 계약의 완료 여부. PASS여도 모델이 목표를 이루었다는 뜻은 아님 |
| `hypothesis_verdict` | rules-only는 `NOT_EVALUATED_RULES_ONLY`, 실제 모델 실행 무결성 실패는 `NOT_EVALUABLE`, 온전한 calibration과 schema v2 prompt 비교는 `NOT_EVALUATED_CALIBRATION`, 온전한 pilot도 `PILOT_RESULTS_REQUIRE_REVIEW`. 관계 표현 우월성을 자동 판정하지 않음 |
| `observed_goal_completion` | 최종 값 7, FINISH, 보이는 사실에 맞는 마지막 행동과 실제 목표 관측을 함께 확인 |
| `successful_abstention` | 목표가 없을 때 보이는 사실에 맞게 FINISH한 경우. 권한 철회·관측 중단을 목표 달성으로 바꾸지 않음 |
| 모델 행동 지표 | 출력 schema·행동/revision·귀속 정확성, 부적절한 SET, gate 거부, 예상/실제 결과 일치, step 상한 종료와 실제 개입 수 |
| 비용 | 모델 호출·prompt/generated token·모델 시간. 표현 길이·후속 경로 차이와 작은 표본의 한계를 함께 기록 |

출력 schema 오류는 원문을 보존하고 해당 결정의 REJECTED·오류 점수로 남긴다. 이를
고쳐서 모델의 원래 출력으로 대체하거나 같은 추론을 재시도하지 않는다. 모델 전송·context
초과·불완전 생성 등 실행 실패와 모델 선택의 오류를 구분한다. 모든 실행 계약이 정상이어도
모델 출력이나 행동 점수는 나쁠 수 있다.

`self_reference_lab.py`는 새 `--artifacts` 경로를 요구하며 실제 모델 경로에는
고정 cache를 `--cache`로 전달한다. 기존 artifact를 덮어쓰지 않는다.
[Linux/Windows CI 구성](../../.github/workflows/linux-boot-check.yml)의 os-tools-matrix에는
연구 unittest → rules-only 6-case 실제 파일 실행 → 독립 재생 경로와
OS별 `self-reference-rules-` artifact가 추가되었다. 이 구성은 실제 모델을
실행하지 않고 native AIOS acceptance도 아니다. CI 구성의 존재를 terminal PASS로
부르지 않으며 해당 SHA의 실행 결과는 별도로 확인한다.

실행 묶음은 `design.json`·`verification-source/`의 실행 당시 source 사본/hash,
episode별 `world/manifest.json`·`world/object.json`·`world/event-*.json`,
실행 전 `decision-*.json`, `episode.json`과 집계 `report.json`을 보존한다.
모델 묶음에는 pin/환경·시작/종료·실제 요청/응답 원문과 token/시간이 있다.
실제 실행 당시 checkout이 dirty였다면 그 사실과 source hash를 남기며 이후 생성될
commit SHA에서 실행했다고 쓰지 않는다.

schema v2의 성공 재생은 정확한 두 profile과 prompt literal/hash, 최초 pair, 각자 event
이력, 24회 이하의 슬롯·반환·HTTP 원문·decision 전체 coverage, 실제 점수와 backend
회수를 대조한다. 미연결 파일이나 호출·미완료/예외를 정상 비교로 숨기지 않는다.
무결성 PASS여도 두 모델의 행동 목표와 가설 해석은 별도다.

독립 재생은 보존 기록과 판정을 다시 대조한 증거이며 새 모델 실행이 아니다.
수정 재생으로 원본 실패를 덮어쓰지 않는다. 새 calibration 또는 pilot 결과가 나오면
stage·실행 식별·source·계획/완료 episode·모델 호출 수·원본 판정·독립 재생을 이곳과
영향 mirror에 추가한다.
실제 AIOS 증거·보고 Task, Linux Task 통합, 연속성, 공간/효율 비교와 native 책임의
남은 검증은 전역 수락 기준대로 유지한다.

## 5. 보존된 첫 실행과 측정 정정 (2026-09-13)

아래 경로는 로컬 `build/` 보존 artifact이며 Git checkout에 자동 포함되지 않는다.
실행 당시 HEAD는 `494de92a5551225600c2da99648ecf377ed695e4`의 dirty 개발 상태였고,
원본 source manifest는 연구 Python 4개의 실행 전후 hash 일치를 기록한다.
후속 projection·calibration·replay 개발 코드에서 실행한 결과로 바꾸지 않는다.

| 원본 | 확인 범위 |
|---|---|
| `build/self-reference-pilot-01/report.json` | 실제 completion 35회 시도·34회 응답, 18 episode 중 8개 완료, 545.109초. experiment_integrity FAIL, hypothesis NOT_EVALUABLE |
| `build/self-reference-pilot-01-interruption.json` | 관측 result·history에 계산된 attribution 정답이 노출된 것을 발견해 운영자가 정확한 소유 backend를 종료하도록 명시. 실행 중 source 수정 없음 |
| `build/self-reference-original-replay-01/self-reference-pilot-01.receipt.json` 및 `.stdout.json` | 당시 증거를 읽는 별도 보존 consumer에서 NOT_EVALUABLE, exit 1. 원본 409개 파일 hash 불변을 확인했으며 부분 episode까지 재생 PASS한 것은 아님 |
| `build/self-reference-rules-01/`와 위 replay의 `self-reference-rules-01.receipt.json`·`.stdout.json` | 별도 규칙 실행의 파일 79개·6 episode·27 decision 독립 재생 PASS, 원본 불변. 모델 호출은 없고 NOT_EVALUATED_RULES_ONLY |

raw 보고서에 남은 transport error는 명시적인 backend 중단 뒤 관측한 연결 종료다.
이 오류만 보고 모델 서버가 자발적으로 고장났다고 해석하지 않는다. 정답 노출은 모델이
귀속 관계를 스스로 판단하는지 평가하려는 목적과 충돌하므로 원본은 탐색 실패로 보존한다.

완료한 두 normal 모델 episode의 원본 집계는 다음과 같다. 이는 원본 report의 진단값이며
전체 비교나 독립 재생 통과 결과가 아니다.

| 조건 | decision | schema 유효 | 올바른 행동/revision | 올바른 귀속 | 부적절하거나 관측 없는 SET | 확인된 목표 완료 |
|---|---:|---:|---:|---:|---:|---:|
| flat normal | 12 | 9 | 0 | 0 | 9 | 0 |
| relational normal | 12 | 10 | 0 | 0 | 10 | 0 |

두 episode 모두 STEP_LIMIT로 끝났다. 최종 파일에는 값 7이 있었지만 모델이 필요한
재관측과 정상 FINISH를 수행하지 않아 `observed_goal_completion=false`다.
실행기가 일부 SET을 허용해 파일이 바뀐 사실을 올바른 모델 판단이나 피드백 학습으로
바꾸지 않는다. 귀속 정답 노출·불완전 coverage·기초 행동 실패가 함께 있으므로 사용자
아이디어, flat/관계 표현 또는 모델 적응 방식의 우열을 이번 결과로 판정하지 않는다.

report의 arm 비용·횟수는 **완료한 episode만 집계**한다. 위 두 모델 episode의 24호출을
전체 실행 비용으로 쓰지 않는다. 미완료 episode도 포함하는 raw completion 응답 34개의
token 합계는 prompt 25,473·generated 1,242이며 응답 없는 35번째 시도의 미확인 비용을
여기에 넣지 않는다. 전체 벽시계 시간은 545.109초의 실행 관측이며 성능 보장이 아니다.

projection 정정은 입력의 계산된 attribution 정답만 제거하고 raw identity/writer와 실제 파일·
개입·권한 검사·원본 증거를 유지하는 것이다. 이어 normal calibration으로 출력 계약과
기초 관측·행동을 먼저 확인한다. calibration이나 이후 수정 pilot은 새 artifact와 source
pin으로 실행하며 첫 실패를 덮어쓰지 않는다. 이어 실행한 calibration 결과는 아래에
구분해 기록하며 가중치 변경·사용자 원래 목표의 완료를 주장하지 않는다.

### 5.1 초기 결정 prompt probe와 calibration 시작

`build/self-reference-prompt-probe-01/`의 design·flat·relational 원문은 같은
normal 초기 사실·Qwen·sampling 조건에서 공통 SYSTEM의 규칙 순서만 명시한
**첫 결정 probe 2호출**이다. 파일 행동은 수행하지 않았고 Task 완료 결과가 아니다.
양쪽 모델은 `OBSERVE/null/OBSERVED`를 출력해 초기 행동·revision·보이는 사실에
대한 예측은 맞았지만, `attribution=SELF`를 출력했다. 미관측 writer의 정답은
`UNKNOWN`이므로 귀속은 여전히 틀렸다. 이를 전체 관측→행동 성공으로 승격하지 않는다.

`build/self-reference-calibration-01/design.json`은 이 로컬 prompt 후보와
derived attribution 제거 projection을 사용해 `stage=calibration`, normal의
rules·flat·relational 3 episode 실행을 시작한 기록이다. 실행 당시 source manifest와
원문을 별도 보존했다. 원본 실행과 보존된 당시 scorer를 사용하는 독립 재생 결과는
§5.2에 기록한다.

### 5.2 calibration-01 원본 완료 — 실행 무결성과 모델 성공 분리

`build/self-reference-calibration-01/report.json`은 **3/3 episode·실제 모델
24요청/24응답·583.469초·source 불변·experiment_integrity PASS**를 기록한다.
`hypothesis_verdict=NOT_EVALUATED_CALIBRATION`이며 가설이나 제품 성공 판정이 아니다.
실행 당시 `494de92` dirty 상태의 연구 source 4개와 해당 prompt·projection을
보존한다. 이후 수정될 scorer/prompt에서 이 결과가 나왔다고 쓰지 않는다.
`build/self-reference-calibration-replay-01/verification.json`의 독립 재생은
**249개 파일·3 episode·28 decision·24 model query PASS**다. 같은 확인에서 별도
`self-reference-rules-02`의 **79개 파일·6 episode·27 decision**도 PASS였다.
총 328개 원본 파일은 불변이며 두 재생 모두 exit 0이다. `source/`에 보존한
v2 이전 scorer/consumer를 사용했으므로 아래 새 예측 채점의 검증으로 승계하지 않는다.
이 PASS는 모델 실패를 포함한 원본 기록·집계·수명 증거가 재현된다는 뜻이다.

| 조건 | decision/schema 유효 | 올바른 행동/revision | 올바른 귀속 | 예측/실제 일치 | 부적절한 SET | gate 거부 | 확인된 목표 완료 |
|---|---:|---:|---:|---:|---:|---:|---:|
| rules normal | 4/4 | 4 | 4 | 4 | 0 | 0 | 1 |
| flat normal | 12/3 | 2 | 0 | 3 | 0 | 9 | 0 |
| relational normal | 12/1 | 1 | 0 | 1 | 0 | 11 | 0 |

규칙 baseline은 4단계로 목표를 확인했다. 모델 두 조건은 모두 STEP_LIMIT이며,
출력 계약과 기초 관측·행동을 안정적으로 수행하지 못했다. 부적절한 SET가 0이라는
수치를 행동 성공으로 읽지 않는다. schema 오류에 따른 REJECTED도 gate 거부 수에
포함된다. 실제 결과와 직접 비교한 `prediction_matched_result`는 아래 공개 예측
credit 결함의 영향을 받는 필드와 다르다.

flat의 prompt/generated token은 12,498/931, 모델 시간은 362.952초이고 relational은
9,395/518, 214.953초다. 이는 이 완료된 calibration의 원본 관측이며 속도·표현 우열의
통계적 결론이나 원래 AIOS 구상의 반증·완성을 뜻하지 않는다.

### 5.3 외부 검토의 로컬 대조와 남은 수정

기존 GPT 연구 대화의 round02 source 검토는 최신 STALE 피드백 뒤에도 APPLIED 예상에
`prediction_matches_visible_expectation=true`를 줄 수 있는 P2를 지적했다.
`build/research-correspondence-02/reply.md`는 외부 소스 검토이고,
`build/prediction-feedback-review-01/findings.md`와 실제 파일 14 World의 보존 기록은
로컬 재현이다. 14개 event/최종 파일 재생이 무결성 검사를 통과했으며, 일부러 틀린
행동을 넣은 재현을 모델의 올바른 행동으로 해석하지 않는다.

영향은 **공개 사실에 대한 결정별 예측 credit**이다. 공개된 last_result와 최근 이력에
더 새로운 revision·거부 정보가 있는데도 오래된 observation만 사용한 것이 문제다.
행동 적합성·실제 gate 결과·목표 완료와 직접적인 예측/실제 일치 집계는 별도로 계산된다.
이 결함 때문에 calibration의 `prediction_matched_result` 집계가 부풀었다거나
권한 우회·거짓 목표 완료가 발생했다고 주장하지 않는다.

후속은 모델에 실제 공개된 observation·last_result·최근 이력만 사용한 예측 기준과
공통 prompt를 맞추고 재검증하는 것이다. 숨긴 intervention·전체 보존 이력으로 정답을
만들지 않으며 공개 근거가 부족하면 예측 기대값을 미확정으로 남겨야 한다.
현재 개발 소스에는 `public-feedback-prediction-v2`가 추가됐다. 공개 관측·최근
결과를 사용하는 `public_prediction`과 `expected_prediction`·`prediction_evaluable`·
`prediction_reason`를 기록하는 경로다. 당시 추가한 코드의 검증 대기 상태는 이후
§6의 Windows 검사 결과로 갱신한다. calibration-01 원본 채점은 v2로 덮어쓰지 않는다.

round03의 `build/research-correspondence-03/reply.md`는 출력 형태만 제한하는
정적 grammar와 비교 절차를 제안했다. 현재 소스에는 `static-decision-v1`과
`--grammar` 경로가 추가됐고, 언어의 504개 조합·잘못된 형식 거부·인식기 거부를
확인하는 정적 검사 3개가 PASS했다. 형식에 맞지만 의미가 틀린 결정도 언어에 남긴다.
이 당시 소스·정적 검사만으로 고정 backend의 실제 지원·적용을 주장하지 않았다.
이후 실제 probe는 §6.2에 별도로 기록한다. 형식 제약과 관측 근거·행동·귀속의 정확성은
계속 별도 검증하며 가중치 업데이트·Linux Task 통합·native/OS 권한 증거로 확대하지 않는다.

## 6. scorer v2·grammar 검증과 ON/OFF 행동 결과 (2026-09-13)

### 6.1 Windows 검사와 source 범위

`build/self-reference-validation-02/report.json`과 `tests.stderr.txt`는
**134개 수집, 132 PASS·2 skip, 104.096초, exit 0**을 기록한다. 2개 제외는
Windows link privilege가 필요한 World 검사다. source 불변과 AST/공백 검사는 PASS다.

| 검사 분야 | 범위 |
|---|---:|
| contract v2 | 42 PASS; 공개 피드백의 실제 파일 14-case 재현 포함 |
| model wrapper | 23 PASS |
| independent replay | 46 PASS |
| static grammar | 3 PASS; 유한 언어 504조합 |
| World | 20개 중 18 PASS·Windows 권한 조건 2 skip |

v2는 오래된 관측 이후 공개된 SET 결과의 revision·거부·값 근거를 병합한다.
WAIT는 최근 이력에 남은 객체 근거를 지우지 않으며, 이력에서 사라진 결과를 전체
artifact로 복원해 채점하지 않는다. STALE이 알려 준 새 revision의 값은 미확정으로
두되, revision과 hash가 이미 아는 본문과 같으면 그 값 근거를 유지한다.
새 OBSERVE는 이전 피드백보다 우선하며 예측 근거가 행동의 새 관측을 대신하지 않는다.

이 전체 검사와 grammar probe의 lab hash는 `5ac94e3cc3480e5e7346aff97da39620c99b94561fbc2c54f6e6bf3a6938f149`다.
이후 SYSTEM의 STALE 예외 문구를 고친 lab은
`b30f39432c77a7686e257915c40dc3e1c4877bbbfee2950290344c00862924d0`다.
validation-02의 PASS는 그 source 범위로 보존한다. 후속
`build/self-reference-validation-03/report.json`과 `tests.stderr.txt`는 수정 lab과
rules-only/grammar 모드 거부 검사를 포함해 **135개 수집, 133 PASS·2 skip,
106.369초, exit 0**을 기록한다. source 불변·AST/공백 검사도 PASS다. 이 전체 검사는
아래 실제 모델 실행 당시 source 기준이며 게시 전 끝 공백 정리는 §6.3에서 구분한다.

당시 source의 `build/self-reference-rules-03/report.json`은 규칙 6 episode·
27 decision·source 불변의 PASS다. 80개 파일의 독립 재생도 PASS였고 모델은 실행하지
않았다. 이 규칙 검증은 후속 실제 모델 calibration의 성공이나 Linux/Windows CI의
동일 SHA terminal 성공을 대신하지 않는다.

### 6.2 grammar-probe-01 — 파일 행동 없는 실제 모델 확인

`build/self-reference-grammar-probe-01/report.json`은 각 표현 조건에서
**OFF → ON → OFF**, 총 **6개 정상 HTTP 응답·qualification PASS·file_actions=0**을
기록한다. ON 응답은 요청 grammar echo와 `grammar_lazy=false`를 포함했다.
raw 출력에서 두 ON은 공백 없는 4필드 JSON을 반환했지만 둘 다
`OBSERVE/null/OBSERVED/SELF`였다. 초기 writer는 미관측이므로 귀속 정답은
UNKNOWN이며, 형식 개선이 귀속 정확성으로 이어진 것은 아니다.

양쪽 OFF의 전후 응답은 code fence와 추가 SELF 객체를 포함해 출력 계약에 맞지 않았다.
별도 소유 backend의 malformed grammar 요청 1회는 HTTP 400으로 거부됐다.
이 negative 요청은 위 6개 정상 응답과 분리한다. 정상 probe의 token 합계는
prompt 3,309·generated 468, 모델 시간 113.015초이며 실행 전후 source 5개는 불변이다.

producer의 ON 성공 predicate는 JSON proposal parse 검사다. 후속
`build/self-reference-grammar-probe-audit-01/audit-report.json`의 **독립 감사 PASS**는
원본 78개 파일 불변, 두 ON 출력의 정확한 504개 언어 소속, 두 표현의 총 6요청에서 grammar
외 조건 불변, 유효 생성 설정 일치와 malformed HTTP 400을 별도로 확인했다.
모델·backend pin 2개를 재해시했으며 소유 backend 2개의 종료·회수 기록도 확인했다
(host termination, exit 1). 이 감사는 실제 backend의 출력 형식 제약을 확인한 것이다.
파일 행동·관측 이후 판단·목표 완료나 전체 모델 능력의 증거로 확대하지 않는다.

### 6.3 calibration-02 ON/OFF — 형식 개선과 행동 실패의 분리

`build/self-reference-calibration-02-on/report.json`과
`build/self-reference-calibration-02-off/report.json`은 normal에서 rules·flat·relational
각 1 episode를 계획했다. 두 실행의 producer source 5개·SYSTEM·초기 context/user·
pair ID·grammar 이외의 controls와 유효 생성 설정은 같았다. §6.2 probe 이후 고친
lab `b30f39432c77a7686e257915c40dc3e1c4877bbbfee2950290344c00862924d0`를
양쪽 모두 사용했다. 실행 순서는 **ON 전체 → OFF 전체**, 각 실행은 rules → flat →
relational이며 순서를 교차하거나 무작위화하지 않았다.

| 조건 | 실행·독립 판정 | flat 모델 episode | relational 모델 episode |
|---|---|---|---|
| grammar ON | 3/3 episode·24 HTTP 응답, 459.844초; 무결성·독립 재생 PASS, 가설 NOT_EVALUATED_CALIBRATION | 12회 모두 OBSERVE; schema 12/12·올바른 행동 1·귀속 0·확인된 목표 완료 0 | 12회 모두 OBSERVE; schema 12/12·올바른 행동 1·귀속 0·확인된 목표 완료 0 |
| grammar OFF | 2/3 episode 완료, 22 HTTP 200, 639.969초; FAIL/NOT_EVALUABLE | 완료된 12 decision: schema 5·올바른 행동/귀속 0·unsafe/uninformed SET 5·gate 거부 12·확인된 목표 완료 0 | 10번째 호출에서 생성 미완료; episode 미완료, 전체 episode 점수나 terminal PASS 없음 |

양쪽 rules baseline은 4 step에서 목표를 확인했다. ON의 두 모델 조건은 각각 공개
예측 평가 가능 12·미확정 0·실제 결과와 예측 일치 12였고 unsafe SET·gate 거부는 0이다.
이 값은 반복 OBSERVE의 예상 결과를 맞힌 것이며, SET으로 넘어가 실제 목표를 이루었다는
뜻이 아니다. OFF의 완료된 flat은 예측 평가 가능 3·미확정 2·공개 근거/실제 결과 일치 0이다.
schema 무효 결정과 예측 미확정은 별도 항목이며 합계의 분모를 바꾸지 않는다.

OFF 마지막 `query-0022`는 HTTP 200이었지만 출력이 192 token 상한에서 끝났다.
`stop=true/stop_type=limit`, `tokens_evaluated=1174`, `truncated=false`이며
JSON이 닫히지 않아 `ModelError: incomplete_generation`으로 종료했다. 재시도는 없었다.
wrapper 기록은 21 VALID·마지막 1 INVALID이며 VALID가 4필드 schema 통과를 뜻하지 않는다.
미완료 관계 표현의 원문과 비용은 보존하지만 완료 episode 집계에 넣지 않는다.
소유 backend PID 39012는 host termination으로 회수됐고 exit 1, cleanup 오류 0이다.

| 보존 원문에 따른 모델 비용 | HTTP 응답 | prompt token | generated token | query 시간 합계 |
|---|---:|---:|---:|---:|
| ON 전체 | 24 | 26,092 | 624 | 454.454초 |
| OFF 전체, 실패 응답 포함 | 22 | 21,654 | 1,760 | 634.359초 |
| OFF 중 미완료 relational 호출 | 10 | 10,011 | 962 | 322.233초 |

비용 표의 마지막 행은 OFF 전체에 포함된 부분이다. 전체 실행 시간과 모델 query 시간은
다른 측정이며 이 1회 고정 순서 관측을 성능 보장이나 표현 방식의 통계적 효과로 해석하지 않는다.

독립 receipt `build/self-reference-final-comparison-01/receipts/on-off/verification.json`은
ON의 250파일·3 episode·28 decision·24 query 재생 PASS를 기록한다. OFF는 완료된
rules/flat과 원문 실패 진단만 대조했고 미완료 관계 episode를 재생 PASS로 승격하지 않았다.
`off-failure-audit.json`의 실패 진단 무결성은 PASS, 원본 ON 250+OFF 229=479파일과
보존 consumer 3개는 불변이다. 같은 디렉터리의 `comparison.json`은 조건 대조
`diagnostic_parity=PASS`와 별개로 **전체 NOT_EVALUABLE,
efficacy_comparison_performed=false**를 기록한다. 조건 일치는 미완료 효과 비교의 성공을
만들지 않으며 모델/표현 우열이나 사용자 구상의 타당성을 판정하지 않는다.

실제 모델 실행 당시 HEAD는 `494de92`, 작업 트리는 dirty였으며 실행 중 source는 불변이었다.
위 실행은 design의 source hash로 결속된다. 게시 코드 `f427bdc3b4c42fd91255fe6a4892ca52216af48b` 준비에서 grammar와
그 검사 파일의 마지막 LF를 하나씩 제거했다. producer 5개 중 4개는 실행 원본과 byte 일치,
grammar는 `37772171…`에서 `c71dfdd3…`로 바뀌었지만 AST·grammar literal은 같고
정적 검사 3개가 PASS했다. 상세는 `publication-source-boundary.json`과
`build/self-reference-publication-whitespace-01/report.json`이 소유한다.
새 byte로 모델을 재실행했다고 주장하지 않는다. 이 코드 commit에서 별도로 실행한
rules-04는 6 episode·27 decision·80파일·현재 source 5개의 독립 재생 PASS이며
`build/self-reference-rules-04-replay.json`에 보존한다. 실제 모델 호출은 0이다.

이 체크포인트는 **검증된 연구 실행·채점·보존 기반과 실제 모델의 정직한 실패 기록**이다.
다음 한정 prompt/행동 규칙 실험은 전역 큐가 소유한다. 기본 6-case/18-episode 비교와
전체 다섯 단계, 실제 AIOS 증거 보고 Task·Linux Task 통합은 여전히 미완료다.
가중치 업데이트, native/OS 권한, 의식이나 제품 목표 완료를 주장하지 않으며,
beta 게시·동일 SHA의 CI terminal 결과는 별도 게시 검증으로 확인해야 한다.

### 6.4 beta ae20 — Windows 경로 별칭 실패와 수정 검증 경계

게시 SHA `ae20a9f56468d63c6c73cf97def1234f33414530`의 CI `34753182531`은
7개 job 중 6개 성공, Windows matrix 실패로 **terminal FAIL**이다. 연구 unittest는
Linux 135개 PASS(4.659초), Windows 135개 중 22개 실패(40.417초)다.
앞선 로컬 135개 검사의 PASS를 이 원격 실패의 성공으로 바꾸지 않는다.

`build/self-reference-beta-ci-audit-01/verification.json`과 같은 디렉터리의
`supplemental-receipt.json`은 내려받은 ZIP 6개·추출 파일 149개·게시 source 28개의
무결성 PASS를 기록한다. 원격 artifact 7개는 있지만 Windows의
`os-tool-artifacts-windows-latest`와 `self-reference-rules-windows-latest`는 없다.
Ubuntu 규칙 80파일·6 episode·27 decision의 독립 재생은 PASS이며 실제 모델 호출은 0이다.
ISO는 원격 metadata·제공 digest·업로드 로그만 확인했고 내려받거나 로컬 재생하지 않았다.
가용 증거 무결성 PASS는 전체 CI FAIL이나 Windows 연구 증거 부재를 해소하지 않는다.

보존 ae20 source를 실제 Windows 8.3 경로 별칭에서 실행한
`build/self-reference-path-reproduction-01/corrected-selection.json`은 지정한 두 검사의
예상 실패·exit 1·source hash 불변을 기록한다. backend 경로의 짧은 이름과 긴 이름 비교,
모델 원문의 독립 재생 실패를 재현했으며 22개 전체의 개별 재실행이나 실제 모델 실험은 아니다.

수정 lab은 artifact root를 만들기 전에 원래 경로의 traversal·파일·symlink/reparse
ancestor를 거부한 뒤 하나의 정규 경로를 producer와 모델 기록에 전달한다. replay의
정확한 경로·해시 비교는 유지하며 기록된 경로를 뒤늦게 치환해 통과시키지 않는다.
이것은 private 단일 process 연구 도구의 경로 계약이며 적대적 동시 변경에 대한 OS 격리를
구현한 것이 아니다. lab hash는 실제 ON/OFF의 `b30f3943…`에서 수정 후
`10040b1395b11fe1e9a7278e86e7bee8a5ade1f53894c796442c0bebec4666e3`로 바뀌었다.

`build/self-reference-path-fix-01/report.json`과 `stderr.txt`은 실제 8.3 TEMP의
9개 검사에서 **8 PASS·Windows link 권한 조건 1 skip, 31.240초, exit 0**과 source
11개 불변을 기록한다. ae20에서 실패한 두 검사는 수정 source에서 통과했다.
이어서 `build/self-reference-validation-04/report.json`과 `tests.stderr.txt`은
실제 8.3 TEMP의 전체 **142개 중 139 PASS·권한 조건 3 skip, 126.210초, exit 0**을
기록한다. source 11개 불변·AST/공백 검사는 PASS다. 제외된 3개는 디렉터리/객체 symlink를
만들 권한이 없는 검사이며 8.3 별칭 검사는 실행했다. 실제 모델은 실행하지 않았다.

기존 GPT 연구 대화의 round04 답변은 `build/research-correspondence-04/reply.md`에
보존한다. 제공된 ae20 발췌에서 새로 확정한 P1/P2는 없고, 앞선 귀속 라벨 노출과 STALE
예측 credit의 해당 코드 경로가 보완됐다고 검토했다. 알려진 Windows P2와 실행/게시
source 경계는 남겼다. GPT가 코드 실행·원격 source 조회·해시 재계산·원본 receipt 검증을
수행한 것은 아니며 수정 후 로컬 검증이나 새 CI 판정을 대신하지 않는다.

위 로컬 검증은 수정 source에 한정된다. 새 SHA의 CI terminal 결과는 아직 대기 중이며
ae20 원격 FAIL·과거 실제 모델 원본·독립 consumer hash는 보존한다. §6.3의 게시 전
grammar 끝 LF 정리와 이번 lab 경로 수정은 별개이고, 수정 source에서 실제 모델을
재실행했거나 이전 ON/OFF 효과 비교를 완료했다고 주장하지 않는다.

### 6.5 경로 수정 b60의 원격 검증 — 후속 source와 분리

§6.4 뒤 게시한 `b60bbbd61701c6ae9b9c9a6b888c9c7947947a9d`의 CI
`34754038605`는 **7/7 job terminal PASS**다. 연구 검사는 Windows 142 PASS,
Linux 142개 중 141 PASS·Windows 8.3 조건 1 skip이다. 양쪽 규칙 묶음은 각각
80파일·6 episode·27 decision·모델 호출 0의 독립 재생 PASS다.
`build/self-reference-beta-ci-audit-02/verification.json`과 `supplemental-receipt.json`은
artifact 9개 존재·ZIP 8개 digest·추출 파일 233개·게시 source 28개의 보존을 확인했다.
ISO는 metadata·digest·업로드 로그 확인까지이며 로컬 content 재생은 하지 않았다.
ae20의 실패 원본을 보존하고 이 b60 결과를 아래 새 schema v2 source의 CI PASS로 승계하지 않는다.

## 7. action-progress-v1 실제 SYSTEM 비교 (2026-09-13)

### 7.1 구현·검사·source 경계

이 한정 비교의 schema v2 구현은 §3.3을 따른다. 실행 당시 HEAD는
`b60bbbd61701c6ae9b9c9a6b888c9c7947947a9d`이며 새 lab/replay·검사 변경이 있는
dirty checkout이었다. lab은 `de3d8473c26685994f4850e271459badd000e7331ecb9db87a08a1104679eb67`,
독립 replay는 `2b6ebecde43d3d398504b659ca40ba7ffc13226aa1bc28622b3da274db21d4e2`다.
model/world/contract/grammar는 b60과 byte 일치한다. §6.3의 과거 grammar 끝 LF 변경,
§6.4의 경로 수정 source와 이번 실행 hash를 서로 바꾸어 쓰지 않는다.
실행 이후 만든 로컬 코드 commit `efe22dedac62412e829227917a17162e77c86dbd`의
연구 runtime·검사 source 14개는 호출자 receipt의 실행 당시 byte hash와 일치한다.
이 사후 결속을 실행 당시 HEAD나 새 원격 CI의 성공으로 바꾸지 않는다.

`build/self-reference-validation-05/report.json`과 `tests.stderr.txt`은 실제 Windows
8.3 TEMP에서 **191개 중 188 PASS·link 권한 조건 3 skip, 178.638초, exit 0**을
기록한다. AST/공백 검사와 연구 runtime·검사 source 14개 불변도 PASS다.
135개·142개 검사는 각 과거 source의 역사로 남기며 새 실행 결과로 덮지 않는다.

`build/self-reference-prompt-comparison-01/design.json`은 normal·relational·고정
Qwen·CPU2·grammar ON·context 2048·출력 192·temperature 0·seed 1을 기록한다.
기존 SYSTEM의 hash는 `283fe9863649ed0b95d07981db9ecf27204ac389efa0866c912ac67cad7e3884`,
후보는 `cefb9168c4aa5ed20319a9d0b6e25f17db80870a03cb7b8df53e79558784390c`다.
두 literal과 `build/self-reference-prompt-comparison-proposal-01/proposal.json`,
`build/research-correspondence-05/reply.md`를 구분해 보존한다. GPT의 문안상 반례 부재
검토는 실행이나 광범위한 정책 동등성의 증명이 아니다.

### 7.2 원본 결과 — 실행 무결성 PASS, 행동 개선 미관측

`build/self-reference-prompt-comparison-01/report.json`은 2/2 episode 완료,
`experiment_integrity=PASS`, `hypothesis_verdict=NOT_EVALUATED_CALIBRATION`,
실행 521.719초를 기록한다. 호출자
`build/self-reference-prompt-comparison-run-01/receipt.json`은 exit 0·522.547초와
source 14개 불변을 확인했다. 두 시간은 각각 producer 내부와 호출자 바깥 측정이다.

| 지표 | baseline | action-first-public-feedback-v1 |
|---|---:|---:|
| 완료 episode / decision | 1 / 12 | 1 / 12 |
| 실제 출력의 행동·귀속 분포 | OBSERVE 12·SELF 12 | OBSERVE 12·SELF 12 |
| schema 유효 / 올바른 행동 / 올바른 귀속 | 12 / 1 / 0 | 12 / 1 / 0 |
| 공개 예측 평가 가능 / 미확정 / 실제 결과 일치 | 12 / 0 / 12 | 12 / 0 / 12 |
| unsafe/uninformed SET / gate 거부 | 0 / 0 | 0 / 0 |
| 확인된 목표 완료 / 종료 | 0 / STEP_LIMIT | 0 / STEP_LIMIT |
| prompt / generated token | 12,497 / 312 | 13,397 / 306 |
| 모델 query 시간 | 250.110초 | 264.500초 |

두 조건 모두 실제 파일의 초기 값 0을 바꾸지 못했고, 관측의 last_writer는 계속 null이라
귀속 정답은 매번 UNKNOWN이었다. OBSERVE의 결과를 맞힌 예측 12/12는 올바른 다음 행동
선택이나 목표 달성을 뜻하지 않는다. 목표 7을 확인한 FINISH도 없었다.
이 실행에서 후보의 행동 개선은 관측되지 않았다.

호출 집계는 예약 24·반환 24·completion HTTP receipt 24·확인된 HTTP 200 원문 24·
decision 24이며, 예외·미완료·전송 오류·decision 미연결은 모두 0이다. 재시도는 없었다.
원문 전체 비용은 prompt 25,894·generated 618 token·query 시간 514.610초다.
한 조건당 한 episode, baseline 다음 후보의 고정 순서이며 SYSTEM 전체를 바꾼 비교다.
순서·실행 환경의 영향이나 후보 내부 문장별 효과를 분리하지 않았고, 이 정상 과제에서
후보 개선 미관측을 표현 우열·일반 능력·사용자 원래 구상에 대한 판정으로 확대하지 않는다.

### 7.3 독립 감사와 후속 범위

`build/self-reference-prompt-comparison-audit-01/verification.json`의 독립 감사는
**PASS**다. `strict-replay.json`·`raw-cost-diagnostics.json`과 함께 원본 289파일,
호출자 19파일, 현재 source 14개, trusted replay/contract/grammar 3개와 driver의 불변을
확인했다. 2 episode·24 decision/query, 두 고정 literal·최초 pair·개별 이력·호출 원장·
HTTP 원문·점수·비용과 cleanup을 대조했다. 소유 backend PID 35660은 terminate 후
회수됐고 host termination·exit 1·오류 0이며 Linux 정상 종료나 native attestation이 아니다.
이 재생은 새 모델 실행이 아니며 이전 ON/OFF 비교의 NOT_EVALUABLE을 바꾸지 않는다.

현재 연구 구현은 `PARTIAL`이다. 다음 한정 작업은 원시 출력·행동 분포를 진단하고
기존 GPT 연구 대화의 다음 실험안을 로컬 근거와 대조하는 것이며 전역 큐가 순서를 소유한다.
새 실험 구현·성과는 아직 PLANNED다. normal의 이번 결과로 개입 6-case/18-episode 비교,
실제 AIOS 증거 보고 Task·Linux Task 통합, 전체 다섯 단계, 가중치 적응·native/OS 권한이나
의식·제품 목표 완료를 선언하지 않는다. 새 schema v2 게시 SHA의 CI는 별도 확인 대상이다.

### 7.4 bfea 게시 검증과 GPT round06 — 보존 증거의 후속 대조

2026-09-24 내용 검토: §7.1–§7.3은 2026-09-13 실행·게시 전 경계를 보존한 기록이다.
그 뒤 게시한 `bfea21d648db635f14848f54fa53c20ba6af5db3`에는 실행 당시 연구 runtime·검사
14개와 동일한 소스가 들어 있다. 이 byte 결속은 새 SHA를 checkout해 모델을 다시
실행했다는 뜻이 아니다. 게시 후 두 원격 CI와 기존 GPT의 검토를 아래 범위로 구분한다.

| 해당 beta의 CI | terminal 상태 | 연구 검사 | 보존·독립 대조 범위 |
|---|---|---|---|
| [34756076634](https://github.com/tjwlstj/aios-kernel/actions/runs/34756076634) | 7/7 job success | Windows 191 PASS, 72.066초; Linux 190 PASS·Windows 조건 1 skip, 4.851초 | 9 artifact identity·8 ZIP의 provider digest, 추출 233파일과 게시 source/contract 36개 불변; 아래 독립 재생 |
| [34756077256](https://github.com/tjwlstj/aios-kernel/actions/runs/34756077256) | 7/7 job success | Windows 191 PASS, 67.402초; Linux 190 PASS·Windows 조건 1 skip, 6.119초 | 별도 API·7 job 로그·9 artifact identity 확인; ZIP 다운로드·독립 재생은 하지 않음 |

두 run은 같은 workflow와 beta push SHA, 각각 attempt 1인 별개 실행이다. 중복 발생
원인은 이 증거에서 추론하지 않는다. 주 실행의
`build/self-reference-beta-ci-audit-03/verification.json`과 `supplemental-receipt.json`,
보조 실행의 `secondary-run/verification.json`에 범위를 보존했다. Windows의 실제
8.3 TEMP 경로 회귀도 통과했다. 주 실행의 양쪽 rules 묶음은 각각 80파일·6 episode·
27 decision·모델 호출 0으로 독립 재생 PASS이며 source 5개를 게시 byte와 대조했다.
H1 양쪽 12/12 fixture·독립 parity와 hosted live boot의 READY·exit 0도 확인했다.
kernel job·upload·provider metadata는 성공했지만 ISO ZIP은 내려받거나 로컬 재생하지
않았다. CI의 rules/fixture 성공은 실제 모델 행동 개선이나 새 readout source의 CI를
대신하지 않는다.

기존 GPT 연구 대화의 round06 원문은 `build/research-correspondence-06/reply.md`에
보존했다. GPT가 직접 검토한 범위는 제공된 producer/replay diff, 두 SYSTEM,
각 profile의 decision 1·2·12 응답과 context, report·감사 요약이다. 전체 source
14개·추가 검사 본문·원본 HTTP/cleanup·감사 도구·원격 CI는 직접 확인하지 않았다.
그 범위에서 새로 확정한 P1/P2 결함이나 성공 과장은 없다는 의견이며, 독립 실행
PASS나 전체 코드 검토 판정으로 사용하지 않는다. CI는 검토 요청 당시 별도 확인
중이었으므로 이후 terminal 성공을 GPT가 검증한 사실로 바꾸지 않는다.

로컬 원문 진단 `build/self-reference-prompt-diagnosis-01/report.json`에서 두 조건의
24개 출력 content는 같은 OBSERVE/SELF 결정이었다. 첫 상태는 history가 비어 있으므로
history 복사만으로 최초 SELF 오류를 설명할 수 없다. 원문은 EOS 종료·잘림 없음이고
입력과 출력 상한의 합은 context 안에 있지만, 이를 문맥 길이의 영향이 없다는 증명으로
해석하지 않는다. 원인 가설은 미확정이다.

round06은 같은 공개 사실을 보존하고 `observation` 하나만 `history` 앞으로 옮기는
§3.4의 최대 6호출 진단을 제안했다. 로컬에서는 source·계약·원본 실패와 대조해 이
한 변경을 채택하고 history 삭제를 보류했다. 기존 여러 필드를 앞으로 옮기는 초안
`build/self-reference-readout-design-01/design.md`는 미채택 이력으로 보존하며,
최종 선택은 같은 디렉터리의 `selection.json`이 기록한다. 제안 채택·구현 계약과
실제 모델 실행·독립 검증 완료는 별도 단계다. 장기 다섯 수락 기준과 실제 AIOS 증거
보고 Task·Linux Task 통합의 미완료 범위는 그대로 유지한다.

## 8. frozen-readout-order-v1 실제 진단 (2026-09-24)

### 8.1 실행 source와 검사 경계

실행 당시 HEAD는 `bfea21d648db635f14848f54fa53c20ba6af5db3`이며 readout 구현·검사와
문서 변경이 있는 dirty checkout이었다. 생산자는
`253cac832b90529b517e24d1a255ef36bfeeeab6c686c361f5703c8c808293e0`, 독립 consumer는
`b95a55dc2a3f3d588e18b6962ea463c0a7184f114409fb2df521d8d5a65b6248`이다.
기존 연구 runtime·검사 14개는 이전 실제 SYSTEM 비교 때의 byte와 같고 새 파일은
producer/consumer·합성 fixture·검사 두 개다. 전체 연구 source/test 19개와 실제 실행의
runtime manifest 6개는 서로 다른 범위다. 합성 fixture 검사는 실제 모델 호출이 아니며
생산자의 원본 pin을 바꾸어 실제 증거로 사용하지 않는다.

`build/self-reference-validation-06/report.json`과 `tests.stderr.txt`는 실제 Windows
8.3 TEMP 별칭에서 **238개 수집·234 PASS·link 권한 조건 4 skip·978.276초**를
기록한다. 호출자 전체 시간은 979.328초, exit 0이다. AST/공백·source 목록과 byte
불변, 기존 14개의 과거 실제 실행과의 일치도 PASS다. 이 결과는 §7의 191개 검사나
bfea 원격 CI를 소급 변경하지 않는다. 새 readout 게시 SHA의 CI는 별도 확인 대상이다.

### 8.2 원본 결과 — 무결성 PASS, 순서 변경의 개선 미관측

`build/self-reference-readout-01/report.json`의 run
`51ad65ad-7081-4c90-8bb6-419e47f307f2`는 계획된 **6/6 readout**을 완료했다.
`experiment_integrity=PASS`, `evaluation_scope=STATIC_ONLY`이며 producer 시간은
119.938초다. 호출자 `build/self-reference-readout-run-01/receipt.json`은 exit 0·
133.359초와 source 19개·원본 289파일·driver 불변을 기록한다.

| 지표 | C0 원래 순서 | C1 observation을 history 앞으로 |
|---|---:|---:|
| 새 HTTP 200 / schema 유효 | 3 / 3 | 3 / 3 |
| 출력 분포 | OBSERVE 3·SELF 3 | OBSERVE 3·SELF 3 |
| action/revision 적합 / attribution 적합 / 동시 적합 | 1 / 0 / 0 | 1 / 0 / 0 |
| 공개 예측 평가 가능 / 올바른 예측 | 3 / 3 | 3 / 3 |
| prompt / generated token | 2,750 / 78 | 2,750 / 78 |
| 모델 query 시간 | 56.703초 | 54.594초 |

여섯 content byte 문자열은 모두 같은
`{"action":"OBSERVE","expected_revision":null,"prediction":"OBSERVED","attribution":"SELF"}`다.
각 상태의 C0/C1 입력은 다른 직렬화 순서지만 사실은 같으며 토큰 수도 각각
S0 595·S1 952·S11 1,203으로 같았다. S0의 OBSERVE만 적절한 행동이고,
관측을 가진 S1·S11에서는 SET이 필요했다. 모든 상태의 attribution 정답은 UNKNOWN이다.
OBSERVE에 대한 올바른 공개 예측은 그 행동의 선택이나 귀속이 올바르다는 뜻이 아니다.

호출 원장은 예약 6·반환 6·completion HTTP receipt/200 원문 6·readout 6이며,
예외·미완료·전송 오류·미연결은 모두 0이다. 추가 호출·재시도는 없었다.
전체 원문 비용은 prompt 5,500·generated 156 token·query 시간 111.297초이며,
토큰·시간 미확정 기록은 없다. 조건별 한 번의 query 시간 차이를 효율 개선으로
판정하지 않는다. 행동·귀속 판단에서 순서 변경의 개선은 관측하지 못했다.

### 8.3 독립 감사와 남은 범위

`build/self-reference-readout-audit-01/verification.json`과 `strict-replay.json`은
**PASS**, 독립 감사 시간은 9.171초다. trusted 로컬 source 6개로 새 묶음 379파일의
원본 연결·입력 변환·호출 원장·원시 HTTP·전체 generation settings·채점·집계·비용·
cleanup을 대조했다. 새 묶음 379파일·호출자 26파일·별도 원본 289파일·현재 source
19개·trusted source 6개와 driver는 감사 전후 불변이고 오류 목록은 비어 있다.
원본 289파일의 이전 비교 재생도 PASS다. 보존 artifact 안의 생산자나 모델 코드를
실행한 결과가 아니며 새 모델 호출을 추가하지 않았다.

소유 backend PID 20552는 terminate 요청 뒤 회수됐고 kill 요청은 없었다.
`host_termination`, exit 1, cleanup 오류 0이며 Windows의 소유 프로세스 정리 증거다.
Linux 정상 종료·native 권한·물리적 실행 주체의 증명으로 확대하지 않는다.

이번 결과는 세 고정 상태의 정적 출력 진단이다. World 효과·SET 이후 재관측·다음
행동 수정·목표 완료를 시험하지 않았으며, 모든 writer가 null인 조건의 귀속 오류를
보존한다. 순서가 일반적으로 무관하다거나 고정 모델의 능력 한계를 확정하지 않는다.
기존 pilot/ON-OFF 실패와 SYSTEM 비교 목표 0도 그대로 유지한다.

다음 한정 작업은 결과를 beta에 게시해 정확한 SHA의 CI와 보존 증거를 확인하고 기존
GPT 연구 대화에서 검토한 뒤, 유용한 AIOS 증거 보고 Task의 입력·출력·사실 검증과
실제 Task 연결의 선행조건을 대조하는 것이다. 추가 0.6B prompt 진단을 자동 반복하지
않는다. 개발 구현은 `PARTIAL`이며 Task 설계·실제 연결·Linux 통합·행동 피드백과
장기 다섯 수락 기준은 미완료다. §7.4의 bfea CI를 새 readout source의 원격 검증으로
승계하지 않는다.
