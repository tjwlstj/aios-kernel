# store/ — 부팅 후 온라인 배포 도메인

OS가 부팅된 뒤 **온라인에서 받아오는 드라이버 / 전용 프로그램 / 모델 가중치**의
카탈로그와 (향후) 다운로드 클라이언트를 담는 도메인이다.

> 상태: **스캐폴드**. 카탈로그 매니페스트 포맷과 보안 경계만 정의되어 있고,
> 실제 다운로드/설치 클라이언트는 유저스페이스 런타임(`os/runtime/services/`) 로드맵에 속한다.

## 왜 분리하나

베어메탈 커널(`kernel/`)에 모든 드라이버와 프로그램을 정적으로 넣지 않는다.
최소 부팅 경로만 커널에 두고, 그 외 드라이버·앱·모델은 부팅 후 정책 게이트를 통과해
**필요할 때 받아온다.** 이렇게 하면 커널 표면이 작아지고 배포가 분리된다.

## 보안 경계 (가장 중요)

모든 다운로드·설치 행위는 실행 전에 반드시 게이트를 통과한다.

1. **NodeBit 정책 조회** — `SYS_SLM_NODEBIT_LOOKUP`(`0x720-0x725`)로 해당 action이
   `allow / observe / risky` 중 무엇인지 먼저 확인한다.
2. **Kernel Room gate** — IO/네트워크 경로는 `kernel/core/kernel_room.c`의 게이트 분류를 거친다.
3. **무결성 검증** — 받은 아티팩트는 카탈로그의 `sha256`과 대조한 뒤에만 활성화한다.
4. **출처 신뢰** — 카탈로그 `source` URL은 신뢰 목록 기반으로만 허용한다.

> 임의 URL에서 코드를 받아 ring0에 로드하지 않는다. 커널 드라이버 교체는 별도의
> 높은 위험 등급(risky)으로 분류하고, 기본은 거부(observe-only)다.

## 디렉토리 규약

| 경로 | 용도 | git 추적 |
|---|---|---|
| `catalog/` | 받을 수 있는 항목의 카탈로그 매니페스트(JSON) | ✅ 추적 |
| `cache/` | 다운로드 캐시 (런타임 생성) | ❌ gitignore |

## 카탈로그 스키마 (catalog/*.catalog.json)

```jsonc
{
  "schema": "aios.store.catalog/v0",
  "id": "example-driver",
  "kind": "driver",                 // driver | program | model
  "title": "Example NIC driver",
  "version": "0.1.0",
  "artifact": {
    "url": "https://example.invalid/aios/example-driver-0.1.0.bin",
    "bytes": 0,
    "sha256": ""                     // 다운로드 후 반드시 대조
  },
  "install": {
    "target": "os/apps",            // driver | os/apps | models/weights
    "risk": "risky",                 // NodeBit 기본 등급
    "requires_gate": ["nodebit", "kernel_room"]
  }
}
```

`catalog/example-driver.catalog.json`에 작성 예시가 있다.

## 향후 계획

- `os/runtime/services/`에 카탈로그 fetch + sha256 검증 클라이언트
- NodeBit 정책 테이블과 카탈로그 `risk` 등급 연동
- 서명/신뢰 체인 설계 (드라이버는 ring0 영향이므로 별도 강한 게이트)
