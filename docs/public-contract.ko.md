# 공개 계약

언어: [English](public-contract.md) | 한국어

이 문서는 portable harness의 외부 계약을 설명합니다.

`docs/ops/` 안의 내부 구현 메모보다 범위가 더 좁습니다.
다음이 궁금할 때 이 문서를 봐야 합니다.
- 이 하네스가 무엇을 executable worker continuity로 취급하는가
- 무엇이 metadata-only 또는 preflight-only로 남는가
- consumer workspace가 무엇을 직접 정의해야 하는가

## 목적

이 하네스는 두 종류의 continuity를 분리합니다.
- head-session continuity: 대화 중심이고 가벼움
- worker-session continuity: task-bound이고 실행 가능

이 계약에서 "resume"은 "가능한 많은 transcript를 다시 읽는다"는 뜻이 아닙니다.
대신 task identity와 launch proof가 충분히 강할 때만 executable continuity를 부여합니다.

## 범위

이 저장소는 바로 꽂아 쓰는 완제품이 아니라 portable reference harness입니다.

제공하는 것:
- hook/journal runtime code
- bounded, metadata-first resume 처리
- binding-first worker launch
- doc-basis validation을 위한 최소 문서 scaffold

제공하지 않는 것:
- 팀의 canonical docs나 승인 워크플로
- task naming convention
- 전체 trigger map이나 fixture set
- 팀별 최종 UX wrapper

## Launch 모델

### Head session

Head session은 얇고 대화 중심으로 유지하는 것을 전제로 합니다.

적합한 용도:
- planning
- review
- routing
- 새 worker가 필요한지 결정

Head session continuity는 유용한 metadata와 context를 남길 수 있지만, 그 자체로 executable worker continuity를 증명하지는 않습니다.

### Worker session

Worker session은 `scripts/start_worker_session`으로 시작하는 task-bound session입니다.

다음이 필요할 때 적합합니다.
- explicit task identity
- explicit document basis
- 더 강한 resume correctness
- 실제 구현 작업을 위한 executable continuity

## Executable Continuity Authority

Executable worker continuity는 의도적으로 아주 좁게 정의됩니다.

현재 session state가 아래 전부로 뒷받침될 때만 executable continuity를 유효하다고 봅니다.
- latest validated `SessionStart`
- matching durable `IdentityAcknowledged`
- worker launch에 대한 binding proof
- 현재 git/doc-basis freshness check

이 중 하나라도 없거나 stale이면, 그 session은 metadata나 preflight context를 만들 수는 있어도 executable continuity로 취급되면 안 됩니다.

## Authority가 아닌 입력

다음 입력들은 routing, display, selection에는 도움이 될 수 있지만, 단독으로 executable continuity를 승인하지는 못합니다.
- 최신 project recency
- prompt/assistant excerpt
- rendered context 단독
- project scope 단독
- selection clue 단독
- thin session start
- one-shot invocation 단독

실무적으로 말하면:
- metadata는 과거 실행을 inspect하는 데 도움을 줄 수 있음
- metadata만으로 executable worker resume이 조용히 승인되면 안 됨

## Resume 상태

높은 수준에서는 consumer가 다음 세 가지 결과를 예상하면 됩니다.
- executable continuity: task-bound worker execution을 계속해도 안전함
- chooser/preflight 경로: inspect/select는 가능하지만 실행은 불가
- unavailable/stale: worker session으로 계속하기에 안전하지 않음

정확한 내부 shape는 바뀔 수 있지만, authority boundary 자체는 안정적으로 유지되어야 합니다.

## Launch 불변식

공개 launch 계약은 다음을 전제로 합니다.
- worker는 target checkout/worktree 안에서 시작되어야 함
- `start_worker_session`이 `--session-cwd`를 통제함
- `start_worker_session`이 canonical handoff store를 선택함
- unsafe cross-checkout `--worker-cwd`는 추측하지 않고 거부함
- worker launch가 승인된 document basis를 선언함

이 계약은 의도적으로 보수적입니다.
잘못 executable continuity를 부여하는 것보다 preflight-only로 떨어지는 편이 낫습니다.

## Worktree / CWD 안전성

이 하네스는 multi-repo, worktree-heavy 환경에서의 모호성을 줄이도록 설계되어 있습니다.

공개 기대 사항:
- sibling worktree가 executable worker continuity를 조용히 공유하면 안 됨
- caller cwd와 worker cwd가 명시적 처리 없이 drift하면 안 됨
- launch-time worktree hint는 routing에는 도움을 줄 수 있어도, 실행 권한을 단독으로 만들 수는 없음

## Consumer 책임

이 하네스를 자신의 workspace에 도입하더라도, 아래는 여전히 직접 정의해야 합니다.
- canonical docs와 document approval policy
- task naming rule
- `start_worker_session` 위에 둘 high-level UX wrapper
- 설치/bootstrap 흐름
- workspace-specific trigger map과 policy layer

## 안정성 기대치

이 문서는 저장소의 runtime model에 대한 공개 계약으로 취급해야 합니다.

안정적으로 유지되어야 하는 생각:
- head vs worker 분리
- binding-first worker launch
- bounded, metadata-first resume
- explicit task identity와 lineage
- doc-basis-aware executable continuity

상대적으로 덜 안정적인 구현 세부:
- 내부 helper/module 이름
- private reducer/helper shape
- `docs/ops/` 내부 구조
