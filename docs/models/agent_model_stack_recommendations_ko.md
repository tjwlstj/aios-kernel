# AI 에이전트 OS용 모델 스택 추천

기준일: 2026-03-28

## 1. 목표

이 문서는 AIOS를 "다중 SLM + LLM 병렬 에이전트 OS"로 키우기 위한
초기 모델 스택과 학습 전략을 정리한다.

핵심 전제는 다음과 같다.

- 처음부터 foundation model을 새로 pretrain하지 않는다
- 메인 AI는 안정성을 우선하고, 실시간 변화는 memory와 adapter 중심으로 처리한다
- 하위 SLM/LLM은 역할별로 분리하되, 가능한 한 같은 계열 모델을 우선 사용한다
- tokenizer, serving stack, quantization, tuning 파이프라인을 단순하게 유지한다

즉, 목표는 "가장 강한 모델 1개"가 아니라
"오버헤드가 낮고 계속 학습 가능한 에이전트 군집"이다.

---

## 2. 왜 한 계열로 시작해야 하는가

AIOS가 지향하는 구조에서는 여러 모델이 동시에 돌더라도,
초기에는 모델 계열을 최대한 통일하는 편이 유리하다.

이유:

- tokenizer와 prompt template를 통일하기 쉽다
- serving stack(vLLM/SGLang/Ollama 등)을 단순화할 수 있다
- quantization, LoRA, adapter 배포 파이프라인을 재사용할 수 있다
- 메인 AI와 하위 모델 간 역할 분담 실험이 더 쉽다
- 장기기억, tool schema, policy trace를 공통 형식으로 다루기 좋다

처음부터 여러 벤더의 모델을 섞으면 성능은 좋아질 수 있어도,
OS 수준 오버헤드와 운영 복잡도가 빠르게 커진다.

---

## 3. 추천 원칙

추천은 다음 원칙을 따른다.

- 메인 AI: 14B~32B급 개방형 instruct 모델
- 하위 SLM: 0.27B~8B급의 저지연 모델
- 코딩 전용 하위 모델은 별도 선택 가능
- 장기기억은 별도 embedding 모델로 분리하는 것이 효율적
- 실시간 학습은 full fine-tuning보다 LoRA/QLoRA + memory update를 우선

---

## 4. 1순위 추천 스택: Qwen 중심 단일 계열

가장 추천하는 출발점은 Qwen 중심 단일 계열이다.

### 메인 AI

- `Qwen3-14B`
- 여유가 있으면 `Qwen3-32B`

추천 이유:

- Qwen3는 thinking / non-thinking 모드를 한 모델 안에서 전환할 수 있다
- agentic capability와 MCP 지원을 공식적으로 강조한다
- 119개 언어/방언을 지원하며 한국어도 포함한다
- 8B / 14B / 32B dense 모델이 모두 공개되어 역할 분할이 쉽다
- Apache 2.0 기반이라 상용 실험과 커스터마이즈가 상대적으로 편하다

AIOS 구조에선 메인 AI가 항상 깊게 생각할 필요가 없으므로,
같은 모델에서 사고 예산을 조절할 수 있는 점이 특히 유리하다.

### 하위 SLM

- `Qwen3-8B`: planner / critic / tool supervisor
- `Qwen3-4B`: summarizer / router / memory distiller
- `Qwen3-1.7B` 또는 `Qwen3-0.6B`: intent classifier / guard / protocol normalizer

추천 이유:

- 같은 계열이라 prompt, tokenizer, deployment stack을 공유하기 쉽다
- Qwen3-4B도 이전 대형 계열과 비교해 성능 효율이 높다고 공식 자료가 강조한다
- 모델 크기별 역할 분리가 명확해서 agent tree 구조를 설계하기 좋다

### 코드 전용 하위 모델

- 기본은 `Qwen3-8B`로 시작
- 코드 비중이 커지면 `Qwen2.5-Coder-14B` 또는 `Qwen2.5-Coder-32B` 추가

추천 이유:

- Qwen2.5-Coder 계열은 다양한 크기로 공개되어 있고,
  32B instruct는 공개 코드 모델 성능을 강하게 내세운다
- 메인 AI와 완전히 다른 생태계로 갈아타지 않고도 coding worker를 분리할 수 있다

### 적합한 환경

- 24GB급 VRAM: `Qwen3-8B` 메인 + `Qwen3-1.7B/4B` 보조
- 48GB급 VRAM: `Qwen3-14B` 메인 + `Qwen3-4B/8B` 보조
- 80GB급 또는 다중 GPU: `Qwen3-32B` 메인 + `Qwen3-8B/4B` 보조

---

## 5. 2순위 추천 스택: Gemma 중심 저비용/온디바이스

비용과 경량화가 더 중요하면 Gemma 계열이 좋다.

### 메인 AI

- `Gemma 3 12B`
- 여유가 있으면 `Gemma 3 27B`

추천 이유:

- 4B / 12B / 27B는 128K context를 지원한다
- 140개 이상 언어 지원과 function calling, 구조화 출력이 강점이다
- QAT 체크포인트가 제공되어 저정밀 추론 최적화에 유리하다

### 하위 SLM

- `Gemma 3 4B`: planner / summarizer / retrieval mediator
- `Gemma 3 1B`: lightweight route / guard
- `Gemma 3 270M`: classification / extraction / normalization / compliance

`Gemma 3 270M`은 특히 "작고, 빠르고, task-specific fine-tuning"에 맞춰 설계된 모델이라
AIOS에서 아주 작은 역할 전용 모델 플릿을 만들 때 적합하다.

### 장기기억 보조

- `EmbeddingGemma` 308M

추천 이유:

- 장기기억/검색은 생성 모델보다 embedding 모델을 분리하는 편이 훨씬 효율적이다
- EmbeddingGemma는 300M급 온디바이스 지향 오픈 embedding 모델이라
  local-first memory stack과 잘 맞는다

### 적합한 환경

- 저전력 PC / 랩탑 / 엣지 장치
- 항상 켜져 있는 background agent
- privacy-first local memory assistant

---

## 6. 3순위 추천 스택: 코드/에이전트 작업 특화

AIOS 초기 목표가 커널 개발, 시스템 코드, 도구 자동화에 더 가깝다면
코딩 특화 worker를 섞는 것이 효율적이다.

### 추천 조합

- 메인 AI: `Qwen3-14B` 또는 `Mistral Small 3.1`
- coding worker: `Devstral Small 1.1`
- 보조 SLM: `Qwen3-4B` 또는 `Phi-4-mini-instruct`

### 이유

- `Mistral Small 3.1`은 24B급, 128K context, 멀티모달/다국어, Apache 2.0으로
  범용 상위 worker나 메인 보조 모델로 좋다
- `Devstral Small 1.1`은 Mistral이 code agent 용도로 강하게 밀고 있는 공개 모델이다
- `Phi-4-mini-instruct`는 3.8B급, 128K context, reasoning-dense, constrained 환경 지향이라
  verifier / judge / small reasoning worker로 적합하다

주의:

- 이 조합은 성능은 좋지만 tokenizer와 serving stack이 섞여 복잡도가 올라간다
- 그래서 첫 버전보다는 "코딩 전용 하위 worker를 분리하고 싶을 때" 추천한다

---

## 7. 추천 시작안

가장 현실적인 시작안은 아래 둘 중 하나다.

### 시작안 A: 가장 추천

- 메인 AI: `Qwen3-14B`
- worker 1: `Qwen3-8B`
- worker 2: `Qwen3-4B`
- micro-worker: `Qwen3-1.7B`
- memory embedding: 별도 도입 시 `EmbeddingGemma`

장점:

- 한 계열로 시작해 운영 오버헤드를 낮출 수 있다
- 메인/보조/초경량 worker 역할 분리가 쉽다
- 나중에 `Qwen2.5-Coder-14B`를 code worker로 붙이기 쉽다

### 시작안 B: 비용 우선

- 메인 AI: `Gemma 3 12B`
- worker 1: `Gemma 3 4B`
- micro-worker: `Gemma 3 270M`
- memory embedding: `EmbeddingGemma`

장점:

- 소형 장치까지 포함한 long-running OS 실험에 유리하다
- "기능별 작은 모델 플릿" 전략과 잘 맞는다
- task-specific tuning 비용을 크게 낮출 수 있다

---

## 8. 학습 전략

처음부터 pretraining을 새로 하는 것은 비효율적이다.
AIOS 초기에는 다음 순서를 추천한다.

### 단계 1. Base model 고정

- 메인 AI와 worker 모델은 검증된 instruct checkpoint로 시작
- base weight는 당분간 고정

### 단계 2. Memory-first

- 장기기억은 event log + retrieval index + summary memory로 구축
- 실시간 변화는 memory write로 반영

### 단계 3. Role-specific adapter

- 메인 AI: self-model / governance / policy adapter
- worker SLM: routing / summarization / validation / coding adapter
- full fine-tuning 대신 LoRA/QLoRA 우선

### 단계 4. Offline merge

- 충분한 trace와 평가셋이 모이면 adapter merge 또는 소규모 재학습
- 실시간 운영 경로와 학습 경로는 분리

### 단계 5. Online update는 제한적으로

- online learning은 memory와 작은 adapter까지
- 메인 AI의 base model은 rollback 가능한 범위 안에서만 변경

---

## 9. AIOS와의 연결

이 모델 스택은 다음 커널 기능과 직접 연결된다.

- `runtime/slm_orchestrator.c`
  장치별 queue/poll/DMA 튜닝과 worker 모델 배치 정책
- `sched/ai_sched.c`
  메인 AI / worker AI / background learning 분리 스케줄링
- `mm/tensor_mm.c`
  model / inference / DMA / KV-cache 메모리 계층 분리
- `runtime/autonomy.c`
  모델 교체, adapter 적용, rollback 정책 검증

즉, 모델 선택은 단순 응답 품질 문제가 아니라
운영체제의 스케줄링, 메모리, I/O 정책과 연결된 설계 문제다.

---

## 10. 결론

지금 단계에서 가장 좋은 전략은 이렇다.

- 처음부터 학습하지 않는다
- 메인 AI는 14B~32B급 공개 instruct 모델로 시작한다
- 하위 모델은 0.27B~8B급으로 역할을 나눈다
- 같은 계열 모델을 우선 사용해 오버헤드를 줄인다
- 실시간 적응은 memory + LoRA로 처리한다
- 장기기억은 별도 embedding 모델과 retrieval 경로로 분리한다

현 시점의 제 1추천은:

- 메인: `Qwen3-14B`
- worker: `Qwen3-8B`, `Qwen3-4B`, `Qwen3-1.7B`
- memory: `EmbeddingGemma`
- 코드 전용 확장: `Qwen2.5-Coder-14B` 또는 `Devstral Small 1.1`

---

## 11. 참고 소스

- Qwen3 공식 발표: <https://qwenlm.github.io/blog/qwen3/>
- Qwen3-8B 공식 모델 카드: <https://huggingface.co/Qwen/Qwen3-8B>
- Qwen2.5-Coder 공식 발표: <https://qwenlm.github.io/blog/qwen2.5-coder-family/>
- Gemma 3 공식 개발자 가이드: <https://developers.googleblog.com/introducing-gemma3/>
- Gemma 3 공식 개요: <https://ai.google.dev/gemma/docs/core>
- Gemma 3 공식 모델 카드: <https://ai.google.dev/gemma/docs/core/model_card_3>
- Gemma 3 270M 공식 발표: <https://developers.googleblog.com/introducing-gemma-3-270m/>
- EmbeddingGemma 공식 모델 카드: <https://ai.google.dev/gemma/docs/embeddinggemma/model_card>
- Phi-4-mini-instruct 공식 모델 카드: <https://huggingface.co/microsoft/Phi-4-mini-instruct>
- Mistral Small 3.1 공식 발표: <https://mistral.ai/news/mistral-small-3-1>
- Devstral Small 1.1 공식 발표: <https://mistral.ai/news/devstral-2507>
