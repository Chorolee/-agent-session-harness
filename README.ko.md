# agent-session-harness

언어: [English](README.md) | 한국어

Claude/Codex 워크플로에서 안전한 worker continuity를 위한 binding-first session runtime입니다.

bounded resume, task identity, task-bound worker launch에 집중한 작고 audit-friendly한 reference harness입니다.

이 저장소는 바로 꽂아 쓰는 완제품이라기보다, 다른 워크스페이스에 이식해서 쓰는 portable reference harness에 가깝습니다.
포함된 문서, 라우팅, worker wrapper는 각자의 canonical workspace contract에 맞게 조정하는 것을 전제로 합니다.

## 왜 만들었나

대부분의 에이전트 워크플로는 대화를 이어 붙이는 데는 강하지만, executable continuity를 증명하는 데는 약합니다.
이 하네스는 worker resume를 단순 transcript replay보다 더 엄격하게 다룹니다.

대표적인 실패 경로는 이렇습니다.
- 헤드 세션이 유용한 문맥은 남기지만, 그 문맥만으로 executable continuity를 신뢰하기는 어렵습니다.
- worker 세션이 검증된 task binding이 아니라 "현재 프로젝트에서 가장 최근 상태" 추측으로 이어지기 쉽습니다.

이 하네스는 그 두 경로를 분리하기 위해 만들어졌습니다.
- 헤드 세션은 얇고 대화 중심으로 유지
- task-bound worker 세션은 실행 가능한 resume 상태로 취급되기 전에 더 강한 증명을 요구

목표는 에이전트 오케스트레이션을 완전 자동화하는 것이 아닙니다. resume과 worker launch를 더 안전하고, 더 명시적이고, 더 추적 가능하게 만드는 것이 목표입니다.

이 하네스가 하는 일:
- hook/journal 메타데이터 기록
- bounded, metadata-first resume 상태 유지
- binding-first wrapper를 통한 task-bound worker session launch
- 얇은 head session과 executable worker continuity 분리

왜 binding-first가 중요한가:
- 얇거나 모호한 session start를 executable continuity로 취급하면 안 되기 때문입니다.
- worker session은 실제 구현 작업을 이어가기 전에 task identity를 먼저 증명해야 하기 때문입니다.

이 공개용 export는 publishing에 맞게 generic하게 정리되어 있습니다.
- workspace 고유 프로젝트 이름은 top-level 문서에서 제거
- launcher가 doc basis를 검증할 수 있도록 최소 canonical docs 포함
- monorepo 전용 테스트와 trigger map은 제외

## 무엇이 다른가

이 저장소는 올인원 agent platform을 지향하지 않습니다.

대신 더 좁고 깊은 축에 집중합니다.
- transcript를 무겁게 다시 읽는 대신 bounded, metadata-first resume
- 느슨한 "최근 프로젝트 상태" 추측 대신 binding-first worker launch
- explicit task identity, session lineage, document basis 추적
- `claude`와 `codex` 둘 다에 적용 가능한 작고 audit-friendly한 runtime core

요약하면:
- thin session은 가볍게
- executable worker continuity는 명시적이고, 검증 가능하고, bounded하게

## Claude와 Codex 둘 다 지원

런타임 코드는 두 vendor CLI를 모두 지원합니다.
- `claude`
- `codex`

포함된 `.claude/skills/worker-launch/SKILL.md`는 선택적인 Claude adapter일 뿐입니다. 런타임 자체는 Claude 전용이 아니며, Codex도 동일한 Python/shell entrypoint를 직접 사용합니다.

## 어디까지 자동인가

하네스를 설치하면 자동으로 되는 것:
- hook event를 normalize해서 journal에 append
- resume 상태를 bounded, metadata-first로 유지
- `start_worker_session`이 caller cwd를 기준으로 `--session-cwd` 고정
- `start_worker_session`이 canonical handoff store 자동 선택
- 안전하지 않은 cross-checkout `--worker-cwd`는 추측하지 않고 거부

의도적으로 여전히 명시적이거나 수동인 것:
- head session에서 계속할지, 새 worker를 띄울지 결정
- task-bound worker session의 task id 선택
- `--docs-revision` 승인
- worker의 document basis를 정의하는 `--doc-basis-path` 선택
- `scripts/ai_worker` 같은 상위 UX wrapper 추가

요약하면:
- head session continuation은 가볍게 유지
- executable worker continuity는 더 엄격하게 관리하며 일부러 "마법처럼" 동작하지 않게 설계

## 운영상 장점

단순히 worker launch를 더 안전하게 만드는 것 외에도, 이 하네스는 에이전트 실행을 더 운영하기 쉽게 만듭니다.
- session id, task identity, worker lineage가 느슨한 대화 기록에 흩어지지 않고 inspectable metadata로 함께 남습니다.
- head와 worker 경계를 넘나드는 session trace가 쉬움
- resume 판단이 느슨한 "최근 프로젝트 상태" 추측이 아니라 명시적 task/session identity에 묶임
- document basis가 worker launch와 함께 남아서 review와 audit trail이 더 명확해짐
- launcher가 unsafe cross-checkout 가정을 거부하므로 worktree/cwd 실수가 줄어듦
- 동일한 runtime model이 `claude`와 `codex` 둘 다에 적용되어 특정 vendor CLI에 정책이 묶이지 않음
- head session은 대화 중심으로 유지하고, worker session은 더 엄격한 execution proof를 가질 수 있음

## 빠른 시작

헤드 세션 이어가기:

헤드 세션은 planning, review, routing 같은 판단 작업에 씁니다.

```bash
claude
# 또는
codex
```

binding-first wrapper를 통한 task-bound worker session:

worker 세션은 느슨한 대화 재로딩이 아니라 task-bound executable continuity가 필요할 때 씁니다.

```bash
"$(git rev-parse --show-toplevel)/scripts/start_worker_session" codex task-slug \
  --docs-revision <approved-token> \
  --doc-basis-path docs/specs/project-roadmap/decision-log.md \
  --doc-basis-path docs/specs/task-spec.md \
  -- --model gpt-5.4
```

Claude worker session:

```bash
"$(git rev-parse --show-toplevel)/scripts/start_worker_session" claude task-slug \
  --docs-revision <approved-token> \
  --doc-basis-path docs/specs/project-roadmap/decision-log.md \
  --doc-basis-path docs/specs/task-spec.md \
  -- --model claude-sonnet-4-6
```

## 포함된 것

- `tools/harness/`
- `scripts/start_worker_session`
- `.claude/skills/worker-launch/SKILL.md`
- 최소 generic `AGENTS.md`, `CLAUDE.md`, `AI_INDEX.md`
- doc-basis validation에 쓰이는 최소 `docs/specs/`, `docs/ops/` scaffold

## 제외된 것

- 프로젝트 전용 trigger map
- monorepo 전용 테스트와 fixture
- evidence, design asset, 기타 workspace 작업 파일

## 메모

- `start_worker_session`은 Claude와 Codex worker session을 위한 저수준 safe entrypoint입니다.
- 나중에 `scripts/ai_worker` 같은 상위 UX wrapper를 그 위에 추가할 수 있습니다.
- 자신의 canonical contract로 공개하려면 generic docs를 검토하고 조정하는 것이 좋습니다.

## 아직 직접 추가해야 하는 것

이 저장소는 runtime core를 제공하지만, 팀별 최종 제품까지 대신해 주지는 않습니다.

보통은 여전히 아래를 직접 얹어야 합니다.
- `docs_revision` 승인 흐름과 canonical docs
- workspace 라우팅 규칙과 task naming convention
- 더 짧은 worker launch 명령을 원할 때의 상위 UX wrapper
- trigger map, fixture, workspace 전용 policy layer
