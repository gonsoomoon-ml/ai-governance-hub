"""허브 설정값. 환경 변수(또는 작업 폴더의 .env)로 들어온다. 비밀값은 기본값을 두지 않는다.

Hub settings, read from environment variables (or a .env in the working directory). Secrets
have no defaults.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """허브 한 벌의 설정. 이름은 Helm values / .env 의 환경 변수 이름과 같다(대소문자 무관).

    One deployment's settings. Names match the environment variable names used in Helm values
    and .env (case-insensitive).
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- gateway-proxy -> hub (POST /v1/resolve) ---
    hub_token: str = ""  # static bearer the gateway presents; empty = resolve refuses (503)

    hub_lang: str = "ko"  # screen language: ko | en
    hub_tz: str = "Asia/Seoul"  # screen time zone for timestamps (IANA name)

    # --- storage ---
    ddb_table: str = "ai-governance-hub"
    ddb_endpoint_url: str = ""  # local DynamoDB for development; empty = real AWS
    aws_region: str = "us-west-2"

    # --- web session + login (Cognito, authorization code + PKCE) ---
    session_secret: str = ""
    oidc_issuer: str = ""  # https://cognito-idp.<region>.amazonaws.com/<pool id>
    oidc_client_id: str = ""
    oidc_client_secret: str = ""  # empty for a public client
    oidc_authorize_url: str = ""  # hosted UI: https://<domain>.auth.<region>.amazoncognito.com/oauth2/authorize
    oidc_token_url: str = ""  # hosted UI: .../oauth2/token
    oidc_redirect_uri: str = ""  # https://hub-dev.example.com/auth/callback
    oidc_scopes: str = "openid email profile"
    admin_group: str = "ClaudeAdmin"  # cognito:groups value that makes a system administrator

    # --- LLM gateway admin-api (team tree, model list, allowed models) ---
    admin_api_url: str = ""  # in-cluster, e.g. http://llm-gateway-admin-api.llm-gateway.svc:8000
    admin_api_token: str = ""  # service token (svc-...)

    # --- AgentCore (in-house agents: Registry records -> gateway targets -> managed MCP entry) ---
    agentcore_gateway_id: str = ""
    agentcore_gateway_url: str = ""  # https://<id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
    agentcore_gateway_role_arn: str = ""  # outbound IAM role the gateway uses to call runtimes
    agentcore_registry_id: str = ""
    mcp_oauth_client_id: str = ""  # Cognito public client Claude Code logs in with
    mcp_oauth_callback_port: int = 8094
    mcp_oauth_metadata_url: str = ""  # Cognito .../.well-known/openid-configuration
    mcp_oauth_scopes: str = "openid email"

    # --- company plugin marketplace: one GitHub repository; administrators approve plugins, teams pick them ---
    company_marketplace_repo: str = ""  # owner/repo on GitHub; empty = the plugins page says "not configured"
    company_marketplace_name: str = ""  # marketplace name Claude Code shows; empty = the manifest's own name
    company_marketplace_ref: str = ""  # tag/branch offered by default on the plugins page

    def oauth(self) -> dict | None:
        """Claude Code 가 tool-gateway 에 로그인할 때 쓸 oauth 블록. 클라이언트 ID 가 없으면 None.

        The oauth block Claude Code uses to log in to tool-gateway; None without a client id.
        """
        if not self.mcp_oauth_client_id:
            return None
        return {
            "client_id": self.mcp_oauth_client_id,
            "callback_port": self.mcp_oauth_callback_port,
            "metadata_url": self.mcp_oauth_metadata_url,
            "scopes": self.mcp_oauth_scopes,
        }
