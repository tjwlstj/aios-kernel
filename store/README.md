# store/ — 부팅 후 온라인 배포 도메인

OS가 부팅된 뒤 **온라인에서 받아오는 드라이버 / 전용 프로그램 / 모델 가중치**의
카탈로그와 (향후) 다운로드 클라이언트를 담는 도메인이다.

> 상태: **스캐폴드**. 카탈로그 매니페스트 포맷과 보안 경계만 정의되어 있고,
> 실제 다운로드/설치 클라이언트는 유저스페이스 런타임(`os/runtime/services/`) 로드맵에 속한다.

## 왜 분리하나

베어메탈 커널(`kernel/`)에 모든 드라이버와 프로그램을 정적으로 넣지 않는다.
최소 부팅 경로만 커널에 두고, 그 외 드라이버·앱·모델은 부팅 후 정책 게이트를 통과해
**필요할 때 받아온다.** 이렇게 하면 커널 표면이 작아지고 배포가 분리된다.

## 목표 보안 경계 (`PLANNED`)

실제 다운로드·설치 클라이언트가 아직 없으므로 아래는 현재 실행 경로가 아니라
후속 구현이 만족해야 할 목표 계약이다. 카탈로그 필드만으로 authorization이
구현됐다고 보지 않는다.

1. **정본 identity** — caller principal과 대상 Node가 canonical Cell에 결속되고
   ownership/generation이 유효해야 한다.
2. **SLM policy 조회** — `SYS_SLM_NODEBIT_LOOKUP`(`0x725`)의 API/tool/device policy
   catalog를 참고한다. 이는 runtime `nodebit.c`와 다른 namespace이며, 조회 자체는
   최종 authorization이 아니다.
3. **Kernel Room Axis Gate** — canonical Cell/Node/NodeBit 상태를 소비하는 공통
   authorize 경로를 통과한다. 현재 gate descriptor는 분류 메타데이터뿐이고 이
   dispatcher-level enforcement는 아직 `PLANNED`다.
4. **무결성 검증** — 받은 아티팩트는 카탈로그의 `sha256`과 대조한 뒤에만 활성화한다.
5. **출처 신뢰** — 카탈로그 `source` URL은 신뢰 목록 기반으로만 허용한다.

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
`requires_gate`는 목표 의존성을 선언하는 스키마 필드이며, 현재 gate 통과 증거가 아니다.

## 향후 계획

- `os/runtime/services/`에 카탈로그 fetch + sha256 검증 클라이언트
- canonical Cell/Node binding과 카탈로그 대상 identity 연결
- SLM policy catalog, runtime NodeBit, 카탈로그 `risk`의 명시적 namespace adapter
- principal/ownership/generation을 확인하는 Kernel Room Axis Gate authorization
- 서명/신뢰 체인 설계 (드라이버는 ring0 영향이므로 별도 강한 게이트)
