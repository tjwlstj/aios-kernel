# tools/ — 테스트툴 + 빌드 오케스트레이션 도메인

모든 도메인을 **빌드/검증**하는 도구가 모이는 곳이다. 다른 도메인은 `tools/`에 의존하지 않는다.
(도메인 경계는 [../PROJECT.md](../PROJECT.md) 참고)

## 구성

| 경로 | 내용 |
|---|---|
| `testkit/` | 모듈형 테스트 오케스트레이터 (커널 레인 + OS 레인 + 부팅 매트릭스/인벤토리/perf) |
| `testkit/aios-testkit.py` | 공통 엔트리포인트 |
| `testkit/kernel/build-windows.ps1` | Windows 커널 빌드/부팅 전용 스크립트 |
| `testkit/fixtures/` | 부팅 baseline fixture |
| `platform/` | Linux-hosted substrate resource manifest와 fail-closed guard |

## 사용 (저장소 루트에서)

```bash
python tools/testkit/aios-testkit.py all --strict          # 커널 + OS 스모크
python tools/testkit/aios-testkit.py kernel --target test --strict
python tools/testkit/aios-testkit.py os                     # OS 도구 스모크
python tools/testkit/aios-testkit.py boot-matrix --profiles full minimal storage-only --strict
```

루트 `Makefile`에도 단축 타깃이 있다: `make test`, `make os-smoke`, `make testkit`.

빌드 산출물은 커널 도메인의 `kernel/build/`에 모인다 (테스트 리포트 포함).
세부 사용법은 [testkit/README.md](testkit/README.md), 확장안은
[../docs/tools/boot_kernel_testkit_expansion_plan_ko.md](../docs/tools/boot_kernel_testkit_expansion_plan_ko.md).

## CI

`.github/workflows/linux-boot-check.yml`가 동일 엔트리포인트
(`python tools/testkit/aios-testkit.py all --strict`)를 Ubuntu/Windows에서 실행한다.

## Platform resource policy

`platform/`은 Linux-hosted substrate와 upstream 참고 자원을 기계 판독 가능한
manifest로 분류하고, 고정되지 않은 버전·비공식 출처·canonical identity 오염·코드
반입을 fail-closed로 거부한다. 사용법과 성숙도 경계는
[`platform/README.md`](platform/README.md)를 따른다.
