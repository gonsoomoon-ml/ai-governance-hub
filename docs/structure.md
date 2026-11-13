# 폴더와 파일 안내

README 다음에 읽는 문서입니다. 파일마다 역할을 한 줄로만 적었습니다.
자세한 설명은 각 파일 머리말(한국어·영어)에 있습니다.

## 한눈에

```text
ai-governance-hub/
├── hub/                          허브 앱 (FastAPI, 서버가 그리는 화면)
│   ├── main.py                   앱 조립: 설정·저장소·HTTP 클라이언트 연결
│   ├── __main__.py               컨테이너 시작점 (python -m hub, 포트 8080)
│   ├── config.py                 설정값 (환경 변수)
│   ├── auth.py                   Cognito 로그인·세션 쿠키·역할 판정
│   ├── routers/ui.py             화면 전부: 팀·정책·승인·배포·조직 설정
│   ├── routers/resolve.py        게이트웨이가 부르는 POST /v1/resolve
│   ├── fragment.py               정책 → 관리형 설정 조각, 검증
│   ├── store.py                  DynamoDB 표 하나 읽기·쓰기
│   ├── gateway_api.py            게이트웨이 admin-api 호출
│   ├── agentcore.py              Registry 승인, 도구 게이트웨이 대상
│   ├── marketplace.py            사내 저장소 marketplace.json 읽기
│   ├── diff.py                   승인 화면의 "바뀌는 것" 표
│   ├── summary.py                팀 화면의 요약 줄·진행 단계
│   ├── i18n.py                   화면 문구 (한국어·영어)
│   ├── timefmt.py                화면 시각 표기
│   └── templates/                화면 HTML (Jinja2) 15개
├── deploy/
│   ├── hub/                      허브 설치 스크립트 10 → 50
│   ├── helm/governance-hub/      허브 Helm 차트
│   ├── agentcore/                도구 게이트웨이·Registry 스크립트 10 → 40
│   └── demo/                     데모용 Cognito 그룹·사용자
├── agents/
│   ├── dev-guide-qa/             예시 AI 에이전트 (Strands, 도구 ask 하나)
│   ├── api-catalog/              예시 MCP 서버 (도구 4개, LLM 없음)
│   └── common/deploy_runtime.py  두 예시를 AgentCore Runtime 에 배포
├── tests/                        pytest (AWS 없이 돎, conftest.py = 가짜들)
├── docs/                         README 그림 생성기·영상 포스터
├── Dockerfile                    허브 이미지
└── pyproject.toml · uv.lock      의존성 (uv)
```

## 코드 읽는 순서

처음 읽는다면 이 순서가 쉽습니다. 괄호 안은 그 파일에서 먼저 볼 함수입니다.

1. `hub/main.py` (`create_app`) — 부품을 `app.state` 에 넣고 주소 묶음 셋(resolve · auth · ui)을
   붙입니다. 이후 코드의 `request.app.state.…` 는 모두 여기서 넣은 부품입니다.
2. `hub/config.py` (`Settings`) — 환경 변수를 담는 칸입니다. 비밀 값이 비면 거절하고,
   기능 값이 비면 그 기능이 꺼집니다.
3. `hub/store.py` (`get_policy` · `put_draft` · `publish`) — 표 하나에 `pk`(서랍) · `sk`(칸)로
   나눠 담습니다. 초안과 발행본이 따로 있고, PC 는 발행본만 받습니다.
4. `hub/fragment.py` (`build_fragment`) — 정책 문서를 Claude Code 키로 바꾸는 핵심입니다.
   잠금은 조직 고정 > 팀 > 엄격한 기본값 순이고, 모델 키는 내지 않습니다.
5. `hub/routers/resolve.py` (`resolve`) — 게이트웨이가 부르는 20줄입니다.
   그 팀의 발행본(없으면 조직 기본 정책)을 조각으로 돌려줍니다.
6. `hub/routers/ui.py` (`save_draft` · `_publish`) — 처리 함수는 모두
   권한 확인 → 꺼내기 → 일 → 화면 또는 다른 주소로 보내기 순서입니다.
   배포는 게이트웨이에 먼저 쓰고, 발행본 저장은 맨 뒤입니다.
7. `hub/auth.py` (`callback` · `derive_role`) → `gateway_api.py` → `agentcore.py` —
   로그인과 바깥 호출입니다.

## 허브가 하는 일 두 가지

### 1. 정책 배포 (사람 → 허브 → 게이트웨이)

1. 팀 리더가 팀 화면에서 정책을 고치고 승인을 요청합니다. `routers/ui.py` 가 초안을 `store.py` 에 씁니다.
2. 관리자가 승인 대기 화면에서 바뀌는 항목(`diff.py`)을 보고 승인합니다.
3. 배포는 `routers/ui.py` 의 `_publish` 가 이 순서로 합니다.
   1. 정책 검사 — `fragment.validate_policy`
   2. 게이트웨이의 지금 허용 모델을 다시 읽습니다. 허브 밖에서 누가 바꿨으면 멈춥니다.
   3. 팀 허용 모델을 게이트웨이 admin-api 에 써 넣습니다 — `gateway_api.py`
   4. 허용한 사내 에이전트가 있으면 도구 게이트웨이에 대상을 만듭니다 — `agentcore.py`
   5. 발행본과 활동 기록을 저장합니다 — `store.py`

   게이트웨이에 먼저 쓰는 이유: 순서가 반대면 PC 의 모델 목록과 서버의 차단이 잠시 어긋납니다.

### 2. 정책 응답 (PC → 게이트웨이 → 허브)

1. PC 의 policy-helper 가 게이트웨이 `GET /v1/policy` 를 부릅니다. 이 부분은 게이트웨이 저장소의 코드입니다.
2. 게이트웨이가 허브 `POST /v1/resolve` 에 팀 id 를 넘깁니다 — `routers/resolve.py`, 고정 토큰 검사
3. 허브는 표만 읽어 그 팀 발행본의 조각을 돌려줍니다. 발행본이 없으면 조직 기본 정책 조각입니다 — `fragment.build_fragment`
4. 게이트웨이가 자기 모델 조각을 합쳐 PC 로 보냅니다.

## 저장 표 (DynamoDB 하나)

키는 `pk` / `sk` 두 문자열이고, 항목 종류는 앞머리로 가릅니다.

```text
ORG / SETTINGS                 조직 고정 항목 + 조직 기본 정책
TEAM#<id> / META               관리할 팀 (이름·부서·팀 리더 이메일)
TEAM#<id> / POLICY#DRAFT       편집 중 정책 + 상태
TEAM#<id> / POLICY#PUBLISHED   발행본 (+ 버전 번호·해시)
TEAM#<id> / VERSION#<n>        발행 이력
AGENTS / <name>                승인된 사내 에이전트 목록 (Registry 에서 복사)
MARKETS / <name>               사내 플러그인 저장소 목록 (GitHub 에서 읽음)
AUDIT / <ns>#<uuid>            활동 기록
```

## 화면 주소

- 로그인 `/login` → Cognito `/auth/login` · `/auth/callback` · `/auth/logout`
- 팀 목록 `/teams` · 팀 정책 `/teams/{team_id}` (저장·요청·취소·승인·반려·배포·미리보기)
- 승인 대기 `/pending` · 조직 설정 `/org`
- 사내 저장소 `/plugins` · 사내 MCP 서버 `/mcp-servers` · 사내 에이전트 `/agents`
- 활동 기록 `/audit` · 도움말 `/help` · 상태 확인 `/healthz`

## 배포 스크립트 `deploy/`

- `hub/` — 번호 순서대로 실행합니다. 값은 `deploy/hub/.env` 에 둡니다(`env.example` 복사, 저장소에 안 올라감).
  - `10-table-and-role.py` — DynamoDB 표 + IAM 역할. `teardown --yes` 로 삭제
  - `20-cognito-callback.sh` — 허브 로그인 콜백을 게이트웨이 Cognito 앱 클라이언트에 등록(terraform 값 수정)
  - `30-service-token.sh` — admin-api 서비스 토큰 발급(VPN 필요)
  - `40-values.sh` — 허브 Helm values 생성(배포 기계에만 둠)
  - `50-gateway-values.sh` — 게이트웨이가 허브를 부르도록 값 두 개
- `helm/governance-hub/` — Deployment · Service · Ingress(ALB) · Secret · ServiceAccount
- `agentcore/` — `10` Claude Code 용 Cognito 공개 클라이언트 · `20` 도구 게이트웨이 + 대상 ·
  `30` Runtime 을 게이트웨이만 부르게 제한 · `40` Agent Registry · `teardown.sh` 전부 삭제
- `demo/` — 데모 팀 2개의 Cognito 그룹·사용자

## 사내 에이전트 예시 `agents/`

- `dev-guide-qa/` — 도구 `ask` 하나 안에서 Strands 에이전트가 `data/dev-guide.md`(사내 개발 가이드)를 읽고 답합니다.
- `api-catalog/` — 도구 4개(`list_apis` · `search_api` · `get_spec` · `get_example`)가 `data/apis.json`(가짜 사내 API 명세)을 돌려줍니다. LLM 을 쓰지 않습니다.
- `common/deploy_runtime.py` — 한 폴더를 묶어 AgentCore Runtime(MCP, V2)에 올립니다. 컨테이너와 zip 둘 다 됩니다.

에이전트 배포 순서: `agents/common/deploy_runtime.py` → `deploy/agentcore/` 10 → 40.

## 테스트 `tests/`

`uv run --extra hub --group dev pytest` — AWS 없이 돕니다.
`conftest.py` 가 DynamoDB(moto)·admin-api·AgentCore·GitHub 대신 가짜를 끼웁니다.

## 무엇을 고치려면

- 화면 문구 → `hub/i18n.py`
- 화면 모양 → `hub/templates/`
- 정책에 새 항목 → `fragment.py`(조각·검사) + `templates/_policy_form.html` + `diff.py`.
  게이트웨이가 받는 키 8개 밖이면 게이트웨이 쪽 허용 목록도 고쳐야 합니다
- 역할·로그인 → `hub/auth.py`
- 저장 모양 → `hub/store.py`
- README 그림 → `docs/diagram-src/overview.py` 를 실행해 다시 만듭니다
