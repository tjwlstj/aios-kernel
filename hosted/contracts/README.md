# AIOS Binding Trace Contract v1 (H1-a)

> 상태: `PARTIAL` — H1-a(계약 manifest + strict JSONL loader + transport negative tests)
> 완료(2026-08-23). H1-b(lifecycle replay/fixture matrix), H1-c(CI/artifact)는 진행 전.
>
> 정본: [H1 작업 준비서](../../docs/os/h1_binding_trace_replay_workplan_ko.md) ·
> [Linux-hosted substrate 정책](../../docs/os/linux_hosted_substrate_and_resource_policy_ko.md)

이 디렉터리는 AIOS Kernel Room source binding의 **OS-neutral trace v1 계약**을 소유한다.
`tools/hosted/binding_trace_replay.py`는 이 계약을 소비해 판정할 뿐 product runtime을
소유하지 않는다. `hosted/linux/`는 H2 전까지 만들지 않는다.

## H1-a가 증명하는 것

- transport 경계와 한도(BOM 금지, 최종 newline 필수, blank 행 금지, 행 4096B /
  trace 64 records / 256 KiB 한도, flat object 강제, duplicate key 거부)
- record별 exact field set(누락·unknown 모두 실패), scalar type(u32 / flag /
  u64-decimal / token), enum membership, 상수(schema_version=1,
  observation_only=1, management_only=1)
- envelope 연속성(sequence 1부터 연속), terminal exact-one/마지막 행/count 산술

## H1-a가 증명하지 않는 것 (H1-b 예정)

- lifecycle state machine 재생과 claimed-vs-computed outcome 비교
- cross-axis validity, zero-sentinel 합법성, instance/generation 규칙, stale/rebind 의미
- `trace.host-instance`, `trace.producer-instance`, `trace.source-reuse`,
  `trace.state-transition` phase-5 검사
- `fixtures/` sidecar와 기대 verdict 비교(H1-c의 fixture manifest CLI 포함)

따라서 이 도구의 PASS는 **구조적 계약 적합**이지 semantic binding 정확성의 증거가 아니다.

## 파일

| 파일 | 역할 |
|---|---|
| `binding-trace-v1.contract.json` | versioned exact-field/type manifest — replay와 host tests가 직접 소비한다(JSON Schema 구현체 아님) |
| `fixtures/` | H1-b에서 실제 fixture와 함께 추가된다. 미리 만들지 않는다 |

## CLI

```powershell
py -3 tools/hosted/binding_trace_replay.py <trace.jsonl>            # 사람용 판정 행, exit 0/1
py -3 tools/hosted/binding_trace_replay.py <trace.jsonl> --json     # machine-readable JSON verdict
py -3 tools/hosted/binding_trace_replay.py <trace.jsonl> --contract <path>
```

- exit code: `0` replay PASS, `1` replay FAIL, `2` 사용법/입력 읽기 실패
- JSON verdict는 준비서 §9 필드를 가지며 원본 trace를 덮어쓰지 않는다
- first-reason 우선순위는 준비서 §7.3의 phase 순서를 따른다:
  raw(io→limit→encoding→truncated) → json(syntax→duplicate-key) →
  shape(missing→unknown→type→range) → envelope(sequence→event→outcome→terminal).
  H1-a에서 enum membership 위반은 shape phase에 둔다(record_type/event는 `trace.event`,
  claimed_*는 `trace.outcome`, 나머지 closed enum은 `trace.type`).

## 검증 명령 (정규 host gate)

```powershell
py -3 -m unittest discover -s tools/hosted/tests -p "test_*.py" -v
```

QEMU/boot-marker baseline은 H1-a/H1-b에서 갱신하지 않는다(준비서 §11).

## 경계

- Python 3.11 표준 라이브러리만 사용한다.
- `kernel/`, `os/`는 이 디렉터리와 `tools/hosted/`에 의존하지 않는다.
- PID, pidfd, cgroup, path, timestamp는 canonical identity/generation이 아니다.
- string mapping(`string_mappings_v1`)은 append-only registry다. 같은 숫자라는 이유로
  namespace를 재사용하지 않는다.
