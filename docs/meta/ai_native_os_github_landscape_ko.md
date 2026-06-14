# AI-native OS GitHub 참고 페이지 정리

확인일: 2026-04-21

## 목적

이 문서는 공개 GitHub 저장소 기준으로 확인 가능한 AI-native OS / agent OS / memory OS
관련 프로젝트를 `AIOS` 개발 관점에서 분류한 메모다.

여기서 말하는 "OS"는 프로젝트마다 의미가 다르다.
따라서 아래 분류에서는 이름보다 실제 계층을 우선한다.

- bare-metal 또는 kernel-first 실험
- 기존 OS 위의 agent runtime
- Linux 배포판/관리 계층
- memory / semantic storage 계층
- 단순 앱 또는 SaaS에 가까운 계층

현재 `AIOS`는 커널이 부팅되고, SLM snapshot / NodeBit / userspace access
기초가 들어간 상태지만, 실제 ring3 userspace handoff와 ELF loader는 아직 없다.
이 문서의 비교 대상은 참고용이며, 현재 구현 완료 상태를 의미하지 않는다.

## 우선 참고해야 할 저장소

| 저장소 | 계층 | 핵심 참고점 | AIOS 적용 후보 |
| --- | --- | --- | --- |
| `agiresearch/AIOS` | host OS 위 agent kernel/runtime | agent scheduling, context/memory/storage/tool manager, terminal UI | `aios-agentd`, `aios-memd`, semantic syscall 설계 |
| `agiresearch/Cerebrum` | AIOS SDK / agent 개발 계층 | agent 개발, 배포, discovery SDK | userspace SDK와 agent manifest |
| `dddimcha/embodiOS` | bare-metal AI OS 실험 | kernel / drivers / mm / AI runtime / GGUF model boot 흐름 | bare-metal AI runtime 비교, 모델 artifact 적재 방식 |
| `cxlinux-ai/cx-core` | Linux 위 AI sysadmin layer | dry-run, rollback, audit log, natural language command UX | AI shell / policy broker preview UX |
| `yashasgc/ArgosOS` | Electron/FastAPI/SQLite 앱 | file storage, tagging, AI-driven search, ingest/retrieval agents | userspace semantic storage 참고 |
| `BAI-LAB/MemoryOS` 또는 `MemTensor/MemOS` | memory OS 계층 | short/mid/long memory, memory API, isolation/share 구조 | `aios-memd` 장기기억 계층 참고 |

## `agiresearch/AIOS`

- URL: https://github.com/agiresearch/AIOS
- 성격: 전통 커널 OS가 아니라, 기존 OS 위에서 LLM agent를 운영하는
  AI Agent Operating System이다.
- 공개 README 기준 구성:
  - AIOS Kernel
  - AIOS SDK
  - LLM core
  - context manager
  - memory manager
  - storage manager
  - tool manager
  - scheduler
  - Web UI / Terminal UI
- 참고할 점:
  - agent 요청을 runtime에서 scheduling하는 구조
  - memory / storage / tool manager를 agent application에서 분리하는 방향
  - computer-use agent에서 VM controller / MCP server를 둔 sandbox 접근
  - remote kernel mode 개념

AIOS 적용 관점:

- `aios-agentd`는 이 프로젝트의 agent scheduler / tool manager 구조를 참고할 수 있다.
- `aios-memd`는 memory manager와 semantic file system 방향을 참고할 수 있다.
- 단, 이 저장소의 "kernel"은 Linux/Windows 같은 커널이 아니라 agent runtime
  추상 계층이므로, `aios-kernel`의 ring3 / ELF / MM / driver 구현과 직접 비교하면 안 된다.

## `agiresearch/Cerebrum`

- URL: https://github.com/agiresearch/Cerebrum
- 성격: AIOS용 Agent SDK.
- 참고할 점:
  - agent development / deployment / distribution / discovery 흐름
  - agent hub와 SDK 분리
  - agent manifest와 runtime API 경계

AIOS 적용 관점:

- `os/runtime/` 아래 userspace agent manifest 설계에 참고 가능하다.
- `aios-agentd`가 직접 모델/툴을 모두 소유하지 않고,
  SDK/manifest를 통해 agent를 등록하는 형태를 검토할 수 있다.

## `dddimcha/embodiOS`

- URL: https://github.com/dddimcha/embodiOS
- 성격: "AI model does not run on an OS; it becomes the OS"를 표방하는
  bare-metal AI OS 실험.
- 공개 README 기준 구성:
  - `kernel/ai`
  - `kernel/core`
  - `kernel/drivers`
  - `kernel/mm`
  - GGUF model 포함 ISO
  - QEMU 실행과 실제 USB boot 안내
- 참고할 점:
  - boot artifact 안에 model artifact를 포함하는 흐름
  - 작은 모델을 kernel-level shell과 연결하는 UX
  - AI runtime을 OS 기초 계층에 매우 가깝게 둔 실험

AIOS 적용 관점:

- `AIOS`도 bare-metal kernel-first 방향이므로 비교 가치가 있다.
- 다만 `AIOS`는 AI가 하드웨어를 직접 소유하게 하기보다,
  `slm_hw_snapshot_t`, `SYS_SLM_NODEBIT_LOOKUP`, mediated I/O, policy gate를 통해
  관찰/제안/적용을 분리하는 방향을 유지해야 한다.
- 따라서 embodiOS는 "모델을 어떻게 boot artifact에 싣는가"와 "초기 AI shell UX"만
  제한적으로 참고한다.

## `cxlinux-ai/cx-core`

- URL: https://github.com/cxlinux-ai/cx-core
- 성격: Ubuntu/Debian/Fedora/CentOS Stream 위에서 동작하는 AI-powered Linux
  administration layer.
- 공개 README 기준 특징:
  - natural language terminal command
  - background daemon
  - JSON-RPC over Unix socket
  - sandboxed execution
  - dry-run mode
  - local-first ML
  - audit logging
- 참고할 점:
  - AI가 명령을 바로 실행하지 않고 preview를 먼저 내는 UX
  - rollback / audit log / sandbox를 기본값으로 두는 방향
  - terminal context를 AI side-panel과 연결하는 방식

AIOS 적용 관점:

- `aios-shell` 또는 `aios-osd`의 command UX에 매우 유용하다.
- NodeBit policy broker의 `observe` 모드를 dry-run preview와 연결할 수 있다.
- apply는 `risky_io_allowed`, `allow_bits`, `SLM_NODEBIT_F_APPLY_ALLOWED`를 모두 통과한
  경우에만 `SYS_SLM_PLAN_SUBMIT`으로 이어지는 구조가 맞다.

## `yashasgc/ArgosOS`

- URL: https://github.com/yashasgc/ArgosOS
- 성격: 이름은 OS지만, 공개 저장소 기준으로는 Electron + React frontend,
  FastAPI backend, SQLite 기반의 AI document management system이다.
- 공개 README 기준 구성:
  - Electron desktop app
  - React UI
  - FastAPI backend
  - IngestAgent
  - RetrievalAgent
  - PostProcessorAgent
  - SQLite database
  - local file blob storage
- 참고할 점:
  - 파일 수집, 태깅, 검색, 후처리 agent 분리
  - 문서/지식 관리용 semantic storage 구조

AIOS 적용 관점:

- kernel 또는 userspace OS 구조 참고로는 우선순위가 낮다.
- `aios-memd`의 cold object index, memory journal, document ingest path에는
  참고할 수 있다.

## Memory OS 계열

대표 후보:

- https://github.com/BAI-LAB/MemoryOS
- https://github.com/MemTensor/MemOS

성격:

- 전통 OS가 아니라 LLM / agent 장기기억 관리 계층이다.
- short-term / mid-term / long-term memory, graph, retrieval, memory correction,
  multi-agent memory isolation/share 같은 개념을 다룬다.

AIOS 적용 관점:

- `aios-memd`가 처음부터 대형 vector database가 될 필요는 없다.
- 먼저 아래 작은 구조를 갖추는 편이 안전하다.
  - append-only memory journal
  - task trace
  - confidence / trust score
  - namespace isolation
  - promotion / quarantine
- kernel은 persistent memory 정책을 직접 들고 있지 않고,
  memory fabric / shared window / health / snapshot만 제공하는 방향을 유지한다.

## 참고는 가능하지만 직접 기준선으로 삼지 않을 것

### Steve / Walturn

- URL: https://www.hey-steve.com/
- GitHub 기반 프로젝트로 확인되는 저장소보다는 SaaS/AI hub 성격이 강하다.
- shared memory, app hub, workflow UX는 참고 가능하지만 kernel-first OS 비교 대상은 아니다.

### VAST AI OS

- URL: https://www.vastdata.com/
- GitHub 저장소 중심 프로젝트가 아니라 기업용 data / compute / messaging /
  agent platform이다.
- AgentEngine, eventing, messaging, tracing은 userspace runtime 참고 가치가 높다.
- 단, 이 문서는 GitHub 저장소 정리이므로 세부 비교는 별도 landscape 문서로 분리한다.

### ThunderSoft AIOS / Apex.OS

- GitHub 중심 오픈소스 프로젝트라기보다 상용/산업용 플랫폼이다.
- 실시간성, safety case, deterministic runtime은 중요하지만,
  여기서는 GitHub 페이지 비교 표의 주 대상에서는 제외한다.

## AIOS 쪽으로 가져올 설계 항목

가져올 만한 것:

- agent runtime은 커널 밖 userspace에 둔다
- agent / tool / model / memory manager를 분리한다
- command apply 전 dry-run / observe path를 둔다
- memory는 namespace와 trust score를 가진다
- tool invocation은 audit log와 rollback hint를 남긴다
- remote agent / remote kernel mode는 장기 목표로 남긴다
- 모델 artifact는 boot artifact에 싣는 방식과 userspace load 방식 모두 비교한다

가져오지 않을 것:

- AI가 직접 MMIO나 register 값을 생성하는 구조
- userspace 없이 AI OS가 완성된 것처럼 보이는 문구
- memory OS를 커널 persistent store처럼 과장하는 구조
- agent SaaS / workflow hub를 kernel-first OS와 같은 계층으로 놓는 비교

## 다음 구현 체크리스트

이 문서에서 바로 이어질 수 있는 `AIOS` 작업은 아래 순서가 적합하다.

1. `os/runtime/policy/`에 NodeBit policy broker manifest sample 추가
2. `os/runtime/agent/`에 agent manifest / role / capability 초안 추가
3. `os/runtime/memory/`에 memory journal namespace / trust score sample 추가
4. `os/tools/evaluate_nodebit_policy.py` 출력에 `trace_id`, `rollback_hint`,
   `fault_domain` 후보 필드 검토
5. `aios-shell` 구상 시 dry-run / observe-first UX를 기본으로 문서화

## 결론

GitHub 기준으로 가장 실질적인 참고 대상은 `agiresearch/AIOS`와
`cxlinux-ai/cx-core`이고, bare-metal 비교 대상으로는 `dddimcha/embodiOS`가 유효하다.
`ArgosOS`는 이름보다 구현 계층을 낮춰 봐야 하며, memory OS 계열은
`aios-memd`의 장기기억 설계에 제한적으로 참고하는 편이 맞다.

현재 `AIOS`의 차별점은 kernel-first 접근과 NodeBit / mediated I/O / hardware-access
snapshot을 통한 안전한 AI 하드웨어 접근 경계다. 이 차별점은 유지하고,
GitHub의 다른 AI OS 프로젝트에서는 userspace runtime, SDK, memory, dry-run UX만
선별적으로 가져오는 것이 좋다.
