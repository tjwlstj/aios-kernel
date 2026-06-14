# models/ — AI/SLM 모델 도메인

AIOS에서 실행할 LLM/SLM 모델의 **아티팩트와 매니페스트**를 관리하는 도메인이다.

> 상태: **스캐폴드**. 매니페스트 스키마와 디렉토리 규약만 정의되어 있고, 실제 로더는
> 유저스페이스 런타임(`os/runtime/`) 로드맵에 속한다.

## 경계 (다른 도메인과의 관계)

- **커널(`kernel/`)은 모델 파일 포맷을 모른다.** 커널은 텐서 메모리(`kernel/mm/tensor_mm.c`)와
  `SYS_MODEL_LOAD` / `SYS_MODEL_UNLOAD` ABI(`0x100-0x1FF`)만 노출한다.
- **유저스페이스 런타임(`os/`)** 이 이 도메인의 매니페스트를 읽어 가중치를 메모리에 올리고,
  텐서 풀에 등록한 뒤 커널 모델 레지스트리에 핸들을 만든다.
- **온라인 배포는 `store/`** 가 담당한다. 대용량 가중치는 이 repo에 커밋하지 않고
  `store/` 카탈로그를 통해 받아 `models/weights/`(gitignore)에 둔다.

## 디렉토리 규약

| 경로 | 용도 | git 추적 |
|---|---|---|
| `manifests/` | 모델별 메타데이터(JSON). 이름/양자화/해시/출처/시스콜 매핑 | ✅ 추적 |
| `weights/` | 실제 가중치 바이너리(`.gguf`, `.safetensors` 등) | ❌ gitignore |

## 매니페스트 스키마 (manifests/*.manifest.json)

```jsonc
{
  "schema": "aios.model.manifest/v0",
  "id": "slm-genesis-0.5b",        // 고유 ID (파일명과 일치)
  "family": "slm",                  // slm | llm | embedding | reranker
  "quantization": "q4_k_m",         // 양자화 포맷
  "params": "0.5B",
  "context_window": 4096,
  "artifact": {
    "file": "weights/slm-genesis-0.5b.q4_k_m.gguf",
    "bytes": 0,                      // 0 = 아직 미배포
    "sha256": "",                    // 무결성 검증용 (store 다운로드 시 대조)
    "source": "store://catalog/slm-genesis-0.5b"  // store 카탈로그 참조
  },
  "runtime": {
    "tensor_pool": "Model",         // kernel/mm/tensor_mm.c 풀 이름
    "alignment": 64,                 // AVX-512 불변식 (64바이트 정렬)
    "lifetime": "LONG_TERM"
  },
  "policy": {
    "nodebit": "observe"            // SYS_SLM_NODEBIT_LOOKUP 기본 정책
  }
}
```

`manifests/example-slm.manifest.json`에 작성 예시가 있다.

## 향후 계획

- `os/runtime/`에 매니페스트 → 텐서 풀 로더 구현
- `SYS_MODEL_LOAD` ABI와 매니페스트 `runtime` 블록 정합성 검증 도구(`tools/testkit`)
- `store/`와 sha256 무결성 파이프라인 연결
