# tools/ — 테스트툴 + 빌드 오케스트레이션 도메인

> 문서 역할: tools 도메인 진입 문서
>
> 검증 판정 정본은
> [검증 툴링 진화 설계](../docs/tools/verification_tooling_evolution_design_ko.md),
> 실제 명령·산출물 가이드는
> [Testkit 가이드](../docs/tools/testkit_guide_ko.md)다.

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

문서는 다음 순서로 확인한다.

1. [통합 작업 가이드](../docs/meta/integrated_work_guide_ko.md) — 변경 종류와 필요한 검증 lane 선택
2. [검증 툴링 진화 설계](../docs/tools/verification_tooling_evolution_design_ko.md) — evidence/verdict, fail-closed 계약 정본
3. [Testkit 가이드](../docs/tools/testkit_guide_ko.md) — 실제 명령, profile, 산출물
4. [testkit/README.md](testkit/README.md) — 현재 디렉터리와 도구 구성

[초기 부팅 커널 테스트 확장안](../docs/tools/boot_kernel_testkit_expansion_plan_ko.md)은
`OLD/REVIEW` 역사 기록이다. 현재 구현 상태, 다음 작업 또는 검증 계약의 정본으로
사용하지 않는다.

## CI

`.github/workflows/linux-boot-check.yml`가 동일 엔트리포인트
(`python tools/testkit/aios-testkit.py all --strict`)를 Ubuntu/Windows에서 실행한다.

## Platform resource policy

`platform/`은 Linux-hosted substrate와 upstream 참고 자원을 기계 판독 가능한
manifest로 분류하고, 고정되지 않은 버전·비공식 출처·canonical identity 오염·코드
반입을 fail-closed로 거부한다. 사용법과 성숙도 경계는
[`platform/README.md`](platform/README.md)를 따른다.
