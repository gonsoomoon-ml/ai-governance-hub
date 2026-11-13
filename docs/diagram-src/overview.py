#!/usr/bin/env python3
"""README 의 구조 그림 생성기. 그림은 손으로 고치지 않고 여기서 다시 만든다.

Generator for the README overview diagram. Regenerate instead of editing the picture by hand:
  python3 docs/diagram-src/overview.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canvas import Canvas, boxed, dw, harrow, vrail  # noqa: E402

c = Canvas(96, 16)
BW = 26
X1, X2, X3 = 0, 35, 70
M1, M2, M3 = X1 + 13, X2 + 13, X3 + 13

boxed(c, 0, X1, 6, BW, 'Governance Hub', ['this repository', 'team policy, approval', 'activity log', 'agent approval'])
boxed(c, 0, X2, 6, BW, 'LLM gateway', ['GET /v1/policy', 'models: its own DB', 'other keys: ask hub', 'enforces models'])
boxed(c, 0, X3, 6, BW, 'Employee PC', ['Claude Code', 'policy-helper', 'baseline + fragment', '= managed settings'])
harrow(c, 2, X1 + BW - 1, X2, '(2)', reverse=True)
harrow(c, 2, X2 + BW - 1, X3, '(1)', reverse=True)

vrail(c, M1, 5, 10, ['(3)'])
vrail(c, M2, 5, 10, ['(4)'])
vrail(c, M3, 5, 10, ['(5)'])
boxed(c, 10, X1, 4, BW, 'gateway admin-api', ['team allowed models', 'users, teams, VK'])
boxed(c, 10, X2, 4, BW, 'Amazon Bedrock', ['inference'])
boxed(c, 10, X3, 4, BW, 'AgentCore Gateway', ['MCP, Cognito login', '-> Runtime (agents)'])

out = c.render().split('\n')
legend = [
    "",
    "  (1) PC 의 policy-helper 가 시작할 때와 5분마다 VK 로 정책을 묻습니다.",
    "  (2) 게이트웨이가 그 사용자의 팀을 허브에 넘겨(POST /v1/resolve)",
    "      플러그인·MCP·잠금 조각을 받습니다.",
    "  (3) 허브는 정책을 배포할 때 팀 허용 모델을 게이트웨이 admin-api 에 써 넣습니다.",
    "  (4) 추론. 허용 밖 모델은 게이트웨이가 거부합니다.",
    "  (5) 사내 에이전트·MCP 서버는 도구 게이트웨이(AgentCore Gateway)를 거쳐서만 호출됩니다.",
    "      허브가 승인된 에이전트를 이 게이트웨이의 대상으로 등록합니다.",
]
print("\n".join(out + legend))
print(f"[check] max width {max(dw(line) for line in out + legend)}", file=sys.stderr)
