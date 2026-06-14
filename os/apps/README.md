# os/apps/ — 전용 프로그램 도메인

AIOS 위에서 도는 **전용 프로그램(에이전트 앱)** 들이 사는 곳이다.
`os/` 유저스페이스 도메인의 하위 영역으로, 런타임/정책/서비스와 구분된다.

> 상태: **스캐폴드**. 앱 배치 규약만 정의되어 있다. 전체 ELF 로더와 유저스페이스
> handoff는 아직 계획 단계라(README 루트 참고), 현재는 앱을 실제로 적재하지 못한다.

## 경계

- 앱은 **ring3 유저스페이스**에서 동작하며, 커널과는 AI 시스콜 표면(`kernel/runtime/ai_syscall.c`)으로만 통신한다.
- 하드웨어 직접 접근은 없다. 모델/추론은 `SYS_MODEL_*` / `SYS_INFER_*`, 정책 확인은 `SYS_SLM_NODEBIT_LOOKUP`을 쓴다.
- 온라인으로 받는 앱은 `store/`(`kind: program`)를 통해 정책 게이트를 통과한 뒤 이 디렉토리에 설치된다.

## 디렉토리 규약 (제안)

```
os/apps/
├── <app-id>/
│   ├── app.manifest.json   # 진입점, 필요한 시스콜, NodeBit 정책 요구
│   └── ...                  # 앱 자원
```

## 향후 계획

- 앱 매니페스트 스키마 정의 (`aios.app.manifest/v0`)
- `os/runtime/`의 ELF 로더 + ring3 handoff 완성 후 첫 데모 앱 적재
- `store/` program 카탈로그와 설치 경로 연결
