# AIOS Binding Trace Contract v1 (H1-a/H1-b/H1-c)

> 상태: `PARTIAL` — H1-a transport, bounded H1-b semantic replay, H1-c fixture
> manifest/self-contained artifact/parity 구현 완료(2026-08-31), type-strict 로컬
> 재검증 완료(2026-09-02).
> Ubuntu/Windows matrix와 별도 parity job도 구성했지만, 현재 exact SHA의 원격 terminal
> 결과는 아직 확인하지 않았으므로 H1 전체를 `CURRENT`로 승격하지 않는다.
>
> 정본: [H1 작업 준비서](../../docs/os/h1_binding_trace_replay_workplan_ko.md) ·
> [Linux-hosted substrate 정책](../../docs/os/linux_hosted_substrate_and_resource_policy_ko.md)

이 디렉터리는 AIOS Kernel Room source binding의 **OS-neutral trace v1 계약**을 소유한다.
`tools/hosted/binding_trace_replay.py`는 이 계약을 소비해 판정할 뿐 product runtime을
소유하지 않는다. `hosted/linux/`는 H2 전까지 만들지 않는다.

## 현재 구현이 증명하는 것

### H1-a transport/envelope

- transport 경계와 한도(BOM 금지, 최종 newline 필수, blank 행 금지, 행 4096B /
  terminal 포함 trace 64 records, 즉 event 최대 63개 / 256 KiB 한도, flat object 강제,
  duplicate key 거부)
- 실제 LF/CRLF terminator만 행 길이에서 제외. bare `CR`, `CRCRLF`, EOF 단독 `CR`은
  `trace.encoding`이며 EOF 단독 `CR`의 `trace.truncated`보다 우선
- record별 exact field set(누락·unknown 모두 실패), scalar type(u32 / flag /
  u64-decimal / token), enum membership, 상수(schema_version=1,
  observation_only=1, management_only=1)
- envelope 연속성(sequence 1부터 연속), trace ID 불변(`trace.trace-id`), terminal
  exact-one/마지막 행/count 산술

### H1-b bounded semantic replay

- contract의 `trusted_target_v1`에 고정한 K1 Node 101 / Cell 1 projection을 C header
  정본과 host drift test로 대조
- `discover → bind → observe → update → stale observe → rebind → observe → exit →
  stale observe → rediscover → rebind → observe` state machine과 retained binding 재생
- current source, retained bound source, binding epoch, lifecycle, zero sentinel,
  validity/match/ownership 축을 독립 계산
- source/binding generation은 decimal string을 정수로 정규화해 비교하고, 새 source
  lifetime만 generation 1에서 시작
- producer claim과 computed outcome/reason의 exact 비교. 문서로 허용한 expected
  rejection은 state를 바꾸지 않는 `rejected/stale` 경로뿐이며, `orphan`/rollback 등
  다른 semantic 오류는 정확히 rejected라고 써도 trace 자체가 FAIL
- host/producer lifetime drift, retired source instance reuse, 미허용 transition과 terminal
  accepted/rejected/final-state/final-binding-generation 독립 재계산
- phase 1~4에 실패가 하나라도 있으면 phase 5를 실행하지 않아 parser/envelope first
  reason을 semantic cascade가 가리지 않음

### H1-c fixture/artifact/parity

- exact sidecar manifest가 12개 checked-in fixture의 기대 outcome과 첫 reason을 소유
- expected-negative trace는 원래 verdict `FAIL`을 유지하고, sidecar와 exact match할 때만
  fixture case가 match
- 실행마다 contract, manifest, raw fixture, 개별 verdict, aggregate verdict,
  git SHA/dirty/GitHub SHA, Python/platform, verifier SHA-256 provenance를 새 artifact
  디렉터리에 보존
- 기존 artifact 디렉터리를 재사용하지 않아 stale PASS와 병합하지 않음
- parity loader가 exact file/member set과 hash, clean checkout, `GITHUB_SHA=HEAD`,
  `runner_os=platform.system`, checked-out verifier/contract/manifest/raw fixture 일치를 강제
- 각 개별 verdict와 aggregate를 bundled raw fixture에서 독립 재생한 뒤 Linux/Windows의
  같은 git SHA, fixture 순서와 `(outcome, first reason, line, sequence)`를 비교
- 왼쪽은 exact `Linux`, 오른쪽은 exact `Windows`만 허용해 same-OS와 reversed bundle을 거부

이 provenance 검증은 bundle 내용·metadata의 정합성 검사이며 실행 호스트의 인증은
아니다. 두 OS metadata를 mock하는 host test는 comparator 회귀만 증명한다. 실제
cross-OS acceptance는 같은 Actions run과 exact SHA의 named Ubuntu/Windows producer
job, parity job 및 그 run에서 전달된 artifact 출처를 함께 확인해야 한다. 임의 로컬
bundle의 metadata를 맞춘 뒤 얻은 parity `PASS`로 H1을 `CURRENT`로 올리지 않는다.

single-trace PASS는 이 bounded contract의 transport와 semantic replay를 통과했다는 뜻이다.
이는 live native lifecycle producer, Linux service, canonical Linux identity, resource
attribution/apply를 증명하지 않는다.

## 파일

| 파일 | 역할 |
|---|---|
| `binding-trace-v1.contract.json` | versioned exact-field/type manifest — replay와 host tests가 직접 소비한다(JSON Schema 구현체 아님) |
| `fixtures/manifest.json` | fixture 순서, evidence kind, expected outcome/first reason sidecar |
| `fixtures/valid/full-lifecycle.jsonl` | 12-event synthetic lifecycle의 canonical 정상 경로 |
| `fixtures/valid/native-k2a-observation.jsonl` | current K2-a field의 self-contained projection. live lifecycle 캡처는 아님 |
| `fixtures/invalid/*.jsonl` | orphan, rollback, stale claim, identity drift, source reuse, state/transport/terminal 반례 |

## CLI

```powershell
py -3 tools/hosted/binding_trace_replay.py <trace.jsonl>            # 사람용 판정 행, exit 0/1
py -3 tools/hosted/binding_trace_replay.py <trace.jsonl> --json     # machine-readable JSON verdict
py -3 tools/hosted/binding_trace_replay.py <trace.jsonl> --contract <path>
py -3 tools/hosted/binding_trace_replay.py `
  --fixture-manifest hosted/contracts/fixtures/manifest.json `
  --artifact-dir build/hosted-binding-trace/Windows --json
py -3 tools/hosted/binding_trace_replay.py `
  --compare-fixture-bundles <linux-bundle> <windows-bundle> `
  --artifact-dir build/hosted-binding-trace/parity --json
```

- single trace: `0` trace PASS, `1` trace FAIL, `2` 사용법/contract/입력 오류
- fixture manifest: `0` 모든 기대값 exact match, `1` 하나 이상 fixture mismatch,
  `2` manifest/path/I/O/artifact infrastructure 오류
- bundle parity: `0` same-SHA/input/verdict parity PASS, `1` 비교 mismatch,
  `2` 누락·손상 bundle 또는 artifact infrastructure 오류
- JSON verdict는 준비서 §9 필드를 가지며 원본 trace를 덮어쓰지 않는다
- first-reason 우선순위는 준비서 §7.3의 phase 순서를 따른다:
  raw(io→limit→encoding→truncated) → json(syntax→duplicate-key) →
  shape(missing→unknown→type→range) → envelope(sequence→trace-id→event→outcome→terminal)
  → semantic(native reason→host→producer→source reuse→state transition→computed terminal)
  → fixture mismatch. enum membership 위반은 shape phase에 둔다(record_type/event는
  `trace.event`, claimed_*는 `trace.outcome`, 나머지 closed enum은 `trace.type`).

## 검증 명령 (정규 host gate)

```powershell
py -3 -m unittest discover -s tools/hosted/tests -p "test_*.py" -v
```

2026-09-03 현재 이 suite는 132개이며 EOF 단독 `CR`, 실제 CRLF, `CRCRLF`, non-finite
JSON, 앞선 blank line과 뒤쪽 BOM/invalid UTF-8, full lifecycle, retained binding,
claimed-vs-computed mismatch, multi-axis native first-reason, header/string/native-tuple drift,
manifest/path/stale artifact, 같은 ID의 alternate contract drift, JSON bool/int/float type
drift, forged empty aggregate, OS-label spoof, bundle tamper와 parity 반례를 포함한다.
CI wiring의 다운로드 실패 유지와 실패 뒤 진단 계속 실행도 두 정적 계약 테스트로
고정한다. 이 검사는 일반 YAML parser나 실제 Actions 실행을 대체하지 않는다.
깊은 JSON 중첩은 trace에서 `trace.syntax`, contract/manifest/bundle에서 infrastructure
오류로 보존한다. 잘못된 trace ID는 verdict metadata에 복사하지 않으며 surrogate를
포함한 실패 진단도 JSON 출력과 artifact, 사람용 trace/parity detail에서 안전하게 escape한다.

host suite는 `.github/workflows/linux-boot-check.yml`의 `os-tools-matrix`에서 Ubuntu와
Windows 양쪽에 실행된다. 별도 `hosted-binding-trace-fixtures-ubuntu`와
`hosted-binding-trace-fixtures-windows` job이 clean exact-SHA bundle을 만들고,
`hosted-binding-trace-parity` job이 `actions/download-artifact@v8`로 받아 독립 재생과
동일 verdict tuple을 확인한다. 이 변경의 원격 실행 결과는 아직 확인 전이다.

두 다운로드는 `digest-mismatch: error`이며 `continue-on-error`를 허용하지 않는다.
Linux 다운로드가 실패해도 Windows 다운로드와 comparator는 취소되지 않은 한 실행하고,
parity artifact는 `always()`로 보존한다. 이미 풀린 파일의 replay가 성공하더라도
다운로드 무결성 실패로 인한 job 실패는 유지한다. bundle의 parity `PASS`만으로 CI
acceptance를 대신할 수 없으며 세 named job의 terminal 성공을 함께 확인해야 한다.

QEMU/boot-marker baseline은 H1 host-only 조각에서 갱신하지 않는다(준비서 §11).

## 경계

- Python 3.11 표준 라이브러리만 사용한다.
- `kernel/`, `os/`는 이 디렉터리와 `tools/hosted/`에 의존하지 않는다.
- PID, pidfd, cgroup, path, timestamp는 canonical identity/generation이 아니다.
- string mapping(`string_mappings_v1`)은 append-only registry다. 같은 숫자라는 이유로
  namespace를 재사용하지 않는다.
- `trace.*` reason에는 numeric ID를 배정하지 않으며 native K2 `0..15`를 재사용하지
  않는다. C header가 native numeric 정본이고 contract string 배열은 drift test로만 묶는다.
- `.gitattributes`가 contract/manifest/JSONL/verifier를 LF로 고정하고 CRLF parity는
  host test가 임시 raw bytes로 생성한다.
