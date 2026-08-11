# AIOS platform resource tools

이 디렉터리는 AIOS가 외부 실행 substrate와 upstream 자료를 사용할 때 출처와
경계를 기계 판독 가능한 형태로 고정한다. 현재 첫 계약은 Linux-hosted substrate
resource manifest v1이다.

## 실행

```powershell
py -3 tools/platform/linux_resource_guard.py
py -3 tools/platform/linux_resource_guard.py --json
py -3 -m unittest discover -s tools/platform/tests -p "test_*.py" -v
```

정상 terminal verdict는 다음 한 행이다.

```text
[LINUX-RESOURCE] PASS schema=1 resources=13 host_only=3 interface_only=5 reference_only=4 blocked_import=1 import_candidate=0 code_import=0 hosted_backend=PLANNED
```

## 계약 경계

- 정본 문서: `docs/os/linux_hosted_substrate_and_resource_policy_ko.md`
- 정본 manifest: `tools/platform/resources/linux_substrate_resources.json`
- `CURRENT`: manifest schema, 분류 정책, fail-closed host guard
- `PLANNED`: Linux-hosted collector, binding reconciler, KVM lane, resource apply
- `RESEARCH`: VirtIO 1.4, seL4, Zircon, Unikraft 비교 자원

guard는 JSON 문법, exact field set, 필수 resource, 고정 version/reference,
공식 HTTPS 출처, source-only identity, 코드 반입 금지, maturity를 검사한다. 네트워크로
자료를 내려받거나 라이선스 호환성을 판정하거나 runtime 지원을 증명하지는 않는다.
schema v1의 모든 `code_import`는 `false`여야 하고, `import_candidate`는 root
LICENSE/COPYING/NOTICE 파일 존재 여부와 무관하게 전면 거부한다. 향후 import 검토에는
명시적인 repository license/SPDX/provenance 정책과 새 schema 승인이 모두 필요하다.
같은 guard와 host negative tests는 `.github/workflows/linux-boot-check.yml`의
Ubuntu/Windows `os-tools-matrix`에서도 실행한다.
