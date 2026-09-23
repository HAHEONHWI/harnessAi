# AI Harness

여러 코딩 에이전트(Codex, OpenCode Kimi, Claude Code, Google Antigravity)를 한 명령으로 병렬 실행하는 macOS 하네스입니다. 모델이 사용량 한도에 걸리면 다음 모델로 자동 전환합니다.

- **엔진** `engine/harness.py`: Python 표준 라이브러리만 사용. 어떤 Git 프로젝트에서도 동작.
- **앱** `AI Harness.app`: SwiftUI. 프로젝트 선택, 작업 지시, 모델 on/off, 진행 상황·로그·patch 확인, 적용.

## 동작 방식

```
명령 → 스냅샷 → Claude 계획 → 워커 병렬 실행 (격리된 worktree) → (보안 리뷰) → Claude 리뷰 → 통합 → 반복 → final.patch → Apply
```

1. 현재 작업 트리(커밋 안 된 파일 포함)를 스냅샷으로 뜹니다. HEAD, index, stash는 건드리지 않습니다.
2. 조율자(`claude`)가 작업을 1~4개로 나누고, 워커마다 겹치지 않는 수정 가능 경로를 정합니다.
3. 워커는 각자 별도 `git worktree`에서 병렬로 실행됩니다. 허용 경로 밖을 수정한 patch는 보류됩니다.
4. 보안 관련 변경이면 Claude가 patch를 리뷰합니다.
5. 조율자가 승인한 patch만 전용 통합 worktree에 반영하고, 필요하면 다음 라운드를 진행합니다(기본 최대 3라운드).
6. 결과는 `final.patch`로 남습니다. **Apply를 눌러야만 프로젝트 파일이 바뀝니다.**

## 역할과 fallback

| 역할 | 기본 모델 | 용도 | 한도 초과 시 fallback |
|---|---|---|---|
| `sol` | Codex `gpt-5.6-sol` (effort high) | 고난도 구현 | kimi → claude → antigravity |
| `luna` | Codex `gpt-5.6-luna` (effort max) | 빠른 구현, QA | kimi → antigravity → claude |
| `kimi` | OpenCode `kimi-for-coding` | 저비용 반복 작업, 문서 | luna → antigravity → claude |
| `claude` | Claude Code `opus` (effort high) | 계획·리뷰 (조율자), 보안 리뷰 | sol → luna → kimi → antigravity |
| `antigravity` | `agy` | UI·대안 구현 | kimi → luna → claude |

- 다음 모델로 넘어가는 조건: 사용량·쿼터·rate limit 오류, 모델 off, CLI 미설치.
- 한도에 걸린 모델은 그 run이 끝날 때까지 다시 시도하지 않습니다.
- 설정 파일은 `~/.ai-harness/config.json`입니다. 모델, on/off, fallback 순서, `max_rounds`, `max_workers`, `call_timeout_minutes`를 여기서 바꿉니다.

## 설치

필요 조건:
- macOS 14 이상, Git, Python 3.9 이상
- 사용할 에이전트 CLI 중 최소 하나 (로그인된 상태): `codex`, `opencode`, `claude`, `agy`

**Release에서 설치**

```bash
curl -fsSL https://raw.githubusercontent.com/HAHEONHWI/harnessAi/main/scripts/install.sh | bash
```

**소스에서 빌드** (Xcode 또는 Command Line Tools 필요)

```bash
scripts/build.sh
```

앱은 ad-hoc 서명만 되어 있고 공증(notarization)은 받지 않았습니다. zip을 직접 받았다면 먼저 `xattr -dr com.apple.quarantine "/Applications/AI Harness.app"`을 실행하세요.

## 사용

**앱**
1. 툴바 폴더 버튼으로 프로젝트를 선택합니다.
2. New Task(⌘N)를 누르고 명령을 입력합니다. 필요하면 대상 파일·폴더를 지정합니다.
3. 진행 상황을 지켜보고 Apply to project로 적용합니다.

**CLI**

```bash
harness=~/.ai-harness/bin/harness.py
$harness start --repo . --path src/api --path README.md "API 응답 캐싱 추가"
$harness status --repo .
$harness apply --repo . <run-id>
$harness config kimi off
```

| 명령 | 설명 |
|---|---|
| `start` / `run` | 백그라운드 / 포그라운드 실행 |
| `status`, `list` | 진행 상태, run 목록 |
| `stop <id>` | 실행 중지 |
| `apply <id>` | `final.patch`를 프로젝트에 적용 |
| `cleanup [--delete] <id>` | worktree 정리 (`--delete`면 run 폴더도 삭제) |
| `config [provider on\|off]` | 설정 보기, 모델 on/off |
| `init` | 기본 설정과 OpenCode 에이전트 설치 |

## 프로젝트별 설정

프로젝트에 `.ai-harness/config.json`을 두면 에이전트가 수정할 수 없는 경로를 추가할 수 있습니다.

```json
{ "protected_paths": ["templates", "output", ".local-settings.json"] }
```

`.git`, `.env*`, `.ai-harness`, `.ssh`, `.aws`, `node_modules` 등은 항상 보호됩니다. run 기록은 `.ai-harness/runs/`에 저장되며, 엔진이 이 경로를 `.git/info/exclude`에 자동으로 추가합니다.

## 배포

`v*` 태그를 push하면 `.github/workflows/release.yml`이 universal 앱을 빌드해 GitHub Release에 `AI-Harness-macOS.zip`을 올립니다.

```bash
git tag v0.1.0
git push origin v0.1.0
```

## 주의

- 에이전트는 각자의 CLI 계정과 요금제로 실행됩니다. Claude 호출에는 `max_budget_usd`(기본 $5)가 적용됩니다.
- 명령과 프롬프트는 외부 모델 API로 전송됩니다. 비밀 정보나 개인정보를 명령에 넣지 마세요.
