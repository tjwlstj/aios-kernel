# OS Learning Tools

이 디렉토리의 도구는 메인 AI 실험과 학습 파이프라인 초안용이다.

도구:

- `score_static_chaos.py`
  메트릭 JSON과 메인 AI 프로필을 받아 `S_static`, `S_chaos`, `SCO`, 운용 모드를 계산
- `build_learning_dataset.py`
  trace JSONL을 memory journal용 JSONL과 adapter 후보 JSONL로 분리
- `summarize_learning_corpus.py`
  trace JSONL의 outcome, memory_type, 중요도 분포를 요약

예시:

```powershell
python .\os\tools\score_static_chaos.py `
  --metrics .\os\examples\static_chaos_metrics.sample.json `
  --profile .\os\main_ai\config\main_ai_profile.example.json

python .\os\tools\build_learning_dataset.py `
  --input .\os\examples\learning_trace.sample.jsonl `
  --memory-out .\build\memory_journal.jsonl `
  --adapter-out .\build\adapter_candidates.jsonl

python .\os\tools\summarize_learning_corpus.py `
  --input .\os\examples\learning_trace.sample.jsonl
```
