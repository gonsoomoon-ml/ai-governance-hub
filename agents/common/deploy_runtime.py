#!/usr/bin/env python3
"""deploy_runtime.py — package one agents/<name>/ directory, upload the artifact and create/update the AgentCore
Runtime (MCP protocol, IAM inbound, platform version V2) with plain boto3.

Usage:  uv run agents/common/deploy_runtime.py <agent-dir> [--artifact container|zip]
   e.g. uv run agents/common/deploy_runtime.py agents/api-catalog
Artifact (default container, or RUNTIME_ARTIFACT in deploy/agentcore/.env):
  container  docker buildx arm64 image from agents/<name>/Dockerfile -> ECR bedrock-agentcore-<name>. The same
             image runs on AgentCore Runtime or on any container host (on-premise / EKS). Needs Docker + buildx.
  zip        direct code deployment: `uv pip install --python-platform aarch64-manylinux2014 --only-binary :all:`
             + server.py + data/ zipped -> S3 <prefix>-code-<account>-<region>, PYTHON_3_13. No Docker, AgentCore-only.
Platform version V2 is set at CreateAgentRuntime (RUNTIME_PLATFORM_VERSION, default V2) and works with both artifacts.
Changing the artifact type later is not an update ("Agent artifact type cannot be updated"): the script deletes and
recreates the runtime (new ARN -> re-run 20-gateway.py / 30-restrict-runtime.py / 40-registry.py, which detect it).

Model examples: official awslabs/agentcore-samples 01-features/02-host-your-agent/01-runtime/02-hosting-tools/
01-mcp-server-basics/deploy.py (MCP server on Runtime V2 via boto3, own execution role, confirm platformVersion with
get_agent_runtime after READY, tools/list smoke test; zip artifact) and aiops-multi-agent-workshop
agents/*/runtime (Dockerfile layout). Docs: devguide getting-started-custom (buildx arm64 -> ECR -> CreateAgentRuntime),
runtime-get-started-code-deploy-python (zip layout, 250 MB zipped / 750 MB unzipped), runtime-permissions (execution
role), runtime-how-it-works "Platform versions" (V1 default, V2 snapshot start), runtime-mcp.
Why not the starter toolkit (used by the workshop): 0.3.14 (latest, 2026-11) has no platformVersion option -> V1 only.
Re-running rebuilds the artifact and calls UpdateAgentRuntime (new immutable runtime version; DEFAULT endpoint follows,
gateway target URL unchanged). Writes <AGENT>_RUNTIME_ARN / _ID / _MCP_URL to deploy/agentcore/.env.
Smoke test: direct tools/list while the runtime has no resource policy; after 30-restrict-runtime.py, a gateway target
re-sync instead.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "deploy" / "agentcore" / ".env"
load_dotenv(ENV_FILE, override=True)
REGION = os.environ.get("AWS_REGION", "us-west-2")
PREFIX = os.environ.get("DEMO_PREFIX", "ai-governance-demo")
PLATFORM = os.environ.get("RUNTIME_PLATFORM_VERSION", "V2")
ARTIFACT = os.environ.get("RUNTIME_ARTIFACT", "container")   # container | zip (CLI --artifact overrides)
PYTHON_RUNTIME = "PYTHON_3_13"          # zip: runtime-code-deploy-supported-runtimes
PYTHON_VERSION = "3.13"
ENTRY_POINT = "server.py"
BUNDLE = ["server.py", "data"]          # zip: copied to the archive root next to the vendored dependencies

iam = boto3.client("iam")
s3 = boto3.client("s3", region_name=REGION)
ecr = boto3.client("ecr", region_name=REGION)
ctl = boto3.client("bedrock-agentcore-control", region_name=REGION)
ACCOUNT = boto3.client("sts").get_caller_identity()["Account"]
BUCKET = f"{PREFIX}-code-{ACCOUNT}-{REGION}"


def agent_name_for(agent_dir: Path) -> str:
    """디렉터리 이름을 Runtime 이름으로 바꾼다('-' -> '_'; 허용 형식 [a-zA-Z][a-zA-Z0-9_]{0,47}).

    Turn the directory name into the Runtime name ('-' -> '_'; allowed form [a-zA-Z][a-zA-Z0-9_]{0,47}).
    """
    return agent_dir.name.replace("-", "_")  # [a-zA-Z][a-zA-Z0-9_]{0,47}


def ensure_bucket() -> None:
    """zip 아티팩트용 S3 버킷이 없으면 만든다(퍼블릭 접근 전부 차단).

    Create the S3 bucket for zip artifacts if it does not exist (all public access blocked).
    """
    try:
        s3.head_bucket(Bucket=BUCKET); return
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
            raise
    kw = {} if REGION == "us-east-1" else {"CreateBucketConfiguration": {"LocationConstraint": REGION}}
    s3.create_bucket(Bucket=BUCKET, **kw)
    s3.put_public_access_block(Bucket=BUCKET, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    print(f"bucket created: s3://{BUCKET}")


def ensure_ecr(repo: str) -> str:
    """ECR 저장소가 없으면 만들고(푸시 때 스캔) 저장소 URI 를 돌려준다.

    Create the ECR repository if it does not exist (scan on push) and return the repository URI.
    """
    try:
        return ecr.describe_repositories(repositoryNames=[repo])["repositories"][0]["repositoryUri"]
    except ecr.exceptions.RepositoryNotFoundException:
        uri = ecr.create_repository(repositoryName=repo, imageScanningConfiguration={"scanOnPush": True})["repository"]["repositoryUri"]
        print(f"ecr created: {uri}"); return uri


def build_and_push(agent_dir: Path, name: str) -> str:
    """agents/<name>/Dockerfile 을 docker buildx 로 arm64 이미지로 빌드해 ECR 에 올린다. 이미지 URI 를 돌려준다(태그 = UTC 시각).

    Build agents/<name>/Dockerfile with docker buildx as an arm64 image and push it to ECR. Returns the image URI (tag = UTC time).
    """
    uri = ensure_ecr(f"bedrock-agentcore-{name}")
    auth = ecr.get_authorization_token()["authorizationData"][0]
    user, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
    subprocess.run(["docker", "login", "--username", user, "--password-stdin", auth["proxyEndpoint"]],
                   input=password.encode(), check=True, capture_output=True)
    image = f"{uri}:{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    t0 = time.monotonic()
    subprocess.run(["docker", "buildx", "build", "--platform", "linux/arm64", "-t", image, "--push", str(agent_dir)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    print(f"image pushed in {time.monotonic() - t0:.0f}s: {image}")
    return image


def package_and_upload(agent_dir: Path, name: str) -> str:
    """uv 로 받은 arm64 의존성 + server.py + data/ 를 압축 파일 루트에 담아 s3://BUCKET/<name>/<timestamp>.zip 으로 올린다. 객체 키를 돌려준다.

    Put the uv-fetched arm64 dependencies + server.py + data/ at the archive root and upload it as s3://BUCKET/<name>/<timestamp>.zip. Returns the object key.
    """
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "pkg"
        subprocess.run(["uv", "pip", "install", "--python-platform", "aarch64-manylinux2014", "--python-version", PYTHON_VERSION,
                        "--target", str(pkg), "--only-binary", ":all:", "-r", str(agent_dir / "requirements.txt")],
                       check=True, capture_output=True)
        zip_path = Path(tmp) / "deployment_package.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(pkg.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    z.write(f, f.relative_to(pkg))
            for item in BUNDLE:
                src = agent_dir / item
                for f in ([src] if src.is_file() else sorted(src.rglob("*"))):
                    if f.is_file() and "__pycache__" not in f.parts:
                        z.write(f, f.relative_to(agent_dir))
        key = f"{name}/{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.zip"
        size = zip_path.stat().st_size / 1e6
        s3.upload_file(str(zip_path), BUCKET, key)
    print(f"package uploaded in {time.monotonic() - t0:.0f}s: s3://{BUCKET}/{key} ({size:.1f} MB zipped)")
    return key


def ensure_role(name: str) -> str:
    """devguide runtime-permissions 페이지의 실행 역할을 만들거나 갱신한다(region/account/agentName 치환). 컨테이너 아티팩트용
    ECR pull 문장을 포함한다(zip 아티팩트는 서비스가 직접 가져온다).

    Create or update the execution role from the devguide runtime-permissions page (region/account/agentName substituted).
    Includes the ECR pull statements for the container artifact (a zip artifact is fetched by the service itself).
    """
    role = f"{PREFIX}-runtime-{name}-role"
    trust = {"Version": "2012-10-17", "Statement": [{"Sid": "AssumeRolePolicy", "Effect": "Allow",
        "Principal": {"Service": "bedrock-agentcore.amazonaws.com"}, "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT},
                      "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:*"}}}]}
    logs = f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group"
    wid = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:workload-identity-directory/default"
    policy = {"Version": "2012-10-17", "Statement": [
        {"Sid": "ECRImageAccess", "Effect": "Allow", "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
         "Resource": [f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/bedrock-agentcore-{name}"]},
        {"Sid": "ECRTokenAccess", "Effect": "Allow", "Action": ["ecr:GetAuthorizationToken"], "Resource": "*"},
        {"Effect": "Allow", "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup"], "Resource": [f"{logs}:/aws/bedrock-agentcore/runtimes/*"]},
        {"Effect": "Allow", "Action": ["logs:PutResourcePolicy"], "Resource": [f"{logs}:/aws/bedrock-agentcore/runtimes/{name}-*"]},
        {"Effect": "Allow", "Action": ["logs:DescribeLogGroups"], "Resource": [f"{logs}:*"]},
        {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": [f"{logs}:/aws/bedrock-agentcore/runtimes/*:log-stream:*"]},
        {"Effect": "Allow", "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"], "Resource": ["*"]},
        {"Effect": "Allow", "Action": "cloudwatch:PutMetricData", "Resource": "*", "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}}},
        {"Sid": "GetAgentAccessToken", "Effect": "Allow",
         "Action": ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForJWT", "bedrock-agentcore:GetWorkloadAccessTokenForUserId"],
         "Resource": [wid, f"{wid}/workload-identity/{name}-*"]},
        {"Sid": "BedrockModelInvocation", "Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
         "Resource": ["arn:aws:bedrock:*::foundation-model/*", f"arn:aws:bedrock:{REGION}:{ACCOUNT}:*"]},
    ]}
    try:
        arn = iam.get_role(RoleName=role)["Role"]["Arn"]
        iam.update_assume_role_policy(RoleName=role, PolicyDocument=json.dumps(trust))
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(RoleName=role, AssumeRolePolicyDocument=json.dumps(trust),
                              Description=f"AgentCore Runtime execution role for {name} (ai-governance-hub demo)")["Role"]["Arn"]
        print(f"role created: {arn} (waiting 10s for IAM propagation)"); time.sleep(10)
    iam.put_role_policy(RoleName=role, PolicyName="runtime-execution", PolicyDocument=json.dumps(policy))
    return arn


def find_runtime(name: str) -> dict | None:
    """이름이 같은 Runtime 요약을 목록에서 찾아 돌려준다(페이지 넘김 포함). 없으면 None.

    Find and return the Runtime summary with this name from the list (paginated). None if absent.
    """
    token = None
    while True:
        resp = ctl.list_agent_runtimes(maxResults=50, **({"nextToken": token} if token else {}))
        for r in resp.get("agentRuntimes", []):
            if r["agentRuntimeName"] == name:
                return r
        token = resp.get("nextToken")
        if not token:
            return None


def wait_ready(agent_id: str) -> dict:
    """Runtime 이 READY 가 될 때까지 최대 15분 기다리고 platformVersion 이 원하는 값인지 확인한다. FAILED 나 시간 초과면 종료.

    Wait up to 15 minutes for the Runtime to become READY and check that platformVersion is the wanted value. Exit on FAILED or timeout.
    """
    deadline = time.monotonic() + 900  # V2 prepares + snapshots the environment at create: minutes, not seconds
    while time.monotonic() < deadline:
        time.sleep(10)
        g = ctl.get_agent_runtime(agentRuntimeId=agent_id)
        if g["status"] == "READY":
            if g.get("platformVersion") != PLATFORM:
                sys.exit(f"runtime {agent_id}: platformVersion={g.get('platformVersion')} (wanted {PLATFORM})")
            return g
        if g["status"].endswith("FAILED"):
            sys.exit(f"runtime {agent_id} {g['status']}: {g.get('failureReason', '')}")
    sys.exit(f"runtime {agent_id} not READY in time")


def artifact_for(ref: str) -> dict:
    """RUNTIME_ARTIFACT 에 따라 agentRuntimeArtifact 를 만든다: container 면 이미지 URI, zip 이면 S3 코드 설정(런타임·진입점).

    Build agentRuntimeArtifact according to RUNTIME_ARTIFACT: the image URI for container, the S3 code configuration (runtime, entry point) for zip.
    """
    if ARTIFACT == "container":
        return {"containerConfiguration": {"containerUri": ref}}
    return {"codeConfiguration": {"code": {"s3": {"bucket": BUCKET, "prefix": ref}}, "runtime": PYTHON_RUNTIME, "entryPoint": [ENTRY_POINT]}}


def _transient(e: ClientError) -> bool:
    """역할이나 그 ECR 문장을 막 쓴 직후의 IAM 전파 지연: 서비스가 실행 역할로 아티팩트를 검증하면서 약 15~30초 동안
    'Access denied while validating ECR URI' 를 낸다(2026-11-04 실측).

    IAM propagation lag right after the role or its ECR statements were written: while validating the artifact with the
    execution role, the service reports 'Access denied while validating ECR URI' for about 15-30 s (seen 2026-11-04).
    """
    code, msg = e.response["Error"]["Code"], e.response["Error"]["Message"]
    return code == "AccessDeniedException" or (code == "ValidationException" and "Access denied" in msg)


def create_or_update(name: str, ref: str, role_arn: str) -> dict:
    """Runtime 을 만들거나 갱신하고 READY 를 기다린다. 갱신 = 새 런타임 버전(ARN 유지); 아티팩트 종류(container<->zip)가 바뀌면
    갱신이 안 되므로 지우고 다시 만든다(ARN 변경). IAM 전파 오류는 15초 간격으로 최대 4회 재시도.

    Create or update the Runtime and wait for READY. Update = new runtime version (ARN kept); when the artifact type changes
    (container<->zip) an update is impossible, so delete and recreate (ARN changes). IAM propagation errors are retried up to
    4 times at 15 s intervals.
    """
    cfg = dict(agentRuntimeArtifact=artifact_for(ref),
               roleArn=role_arn, networkConfiguration={"networkMode": "PUBLIC"},
               protocolConfiguration={"serverProtocol": "MCP"}, platformVersion=PLATFORM)
    existing = find_runtime(name)
    while existing and existing["status"] == "DELETING":
        time.sleep(5); existing = find_runtime(name)
    if existing:
        for attempt in range(1, 5):
            try:
                ctl.update_agent_runtime(agentRuntimeId=existing["agentRuntimeId"], **cfg)
                print(f"runtime update: {existing['agentRuntimeId']} platform={PLATFORM}")
                return wait_ready(existing["agentRuntimeId"])
            except ClientError as e:
                msg = e.response["Error"]["Message"]
                if "artifact type cannot be updated" in msg:
                    # container <-> zip is not an update: recreate. The ARN changes, so re-run
                    # 20-gateway.py / 30-restrict-runtime.py / 40-registry.py (they detect the new endpoint URL).
                    print(f"artifact type changed: deleting {existing['agentRuntimeId']} and recreating (new ARN)")
                    ctl.delete_agent_runtime(agentRuntimeId=existing["agentRuntimeId"])
                    while find_runtime(name):
                        time.sleep(5)
                    break
                if attempt == 4 or not _transient(e):
                    raise
                print(f"update retry {attempt}: {msg[:120]}"); time.sleep(15)
    for attempt in range(1, 5):
        try:
            r = ctl.create_agent_runtime(agentRuntimeName=name, description=f"{name} MCP server (ai-governance-hub demo)", **cfg)
            break
        except ClientError as e:
            if attempt == 4 or not _transient(e):
                raise
            print(f"create retry {attempt}: {e.response['Error']['Message'][:120]}"); time.sleep(15)
    print(f"runtime create: {r['agentRuntimeId']} platform={PLATFORM}")
    return wait_ready(r["agentRuntimeId"])


def smoke_test(g: dict, mcp_url: str) -> None:
    """tools/list 를 한 번 부른다: 런타임이 아직 열려 있으면 직접, 30-restrict 뒤에는 게이트웨이 대상을 통해.

    Call tools/list once: directly while the runtime is still open, through the gateway target after 30-restrict.
    """
    try:
        restricted = bool(ctl.get_resource_policy(resourceArn=g["agentRuntimeArn"]).get("policy"))
    except ClientError:
        restricted = False
    if not restricted:
        data = boto3.client("bedrock-agentcore", region_name=REGION)
        payload = json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 1}).encode()
        last = None
        for attempt in range(5):
            try:
                resp = data.invoke_agent_runtime(agentRuntimeArn=g["agentRuntimeArn"], payload=payload,
                                                 contentType="application/json", accept="application/json, text/event-stream")
                raw = resp["response"].read().decode("utf-8").strip()
                # FastMCP streamable HTTP answers as SSE ("data: {...}") unless the client asks for JSON only
                body = json.loads(raw) if raw.startswith("{") else [json.loads(l[5:]) for l in raw.splitlines() if l.startswith("data:")][-1]
                print(f"smoke test ok (direct): tools={[t['name'] for t in body['result']['tools']]}"); return
            except Exception as e:  # noqa: BLE001 — transient right after READY
                last = e; time.sleep(5 * (attempt + 1))
        sys.exit(f"smoke test failed: READY but not serving MCP ({last}); see /aws/bedrock-agentcore/runtimes/{g['agentRuntimeId']}-DEFAULT")
    gw_id = os.environ.get("GATEWAY_ID")
    if not gw_id:
        print("smoke test skipped: runtime restricted and no GATEWAY_ID in .env"); return
    for t in ctl.list_gateway_targets(gatewayIdentifier=gw_id).get("items", []):
        tg = ctl.get_gateway_target(gatewayIdentifier=gw_id, targetId=t["targetId"])
        if tg.get("targetConfiguration", {}).get("mcp", {}).get("mcpServer", {}).get("endpoint") == mcp_url:
            ctl.synchronize_gateway_targets(gatewayIdentifier=gw_id, targetIdList=[t["targetId"]])
            for _ in range(40):
                time.sleep(3)
                st = ctl.get_gateway_target(gatewayIdentifier=gw_id, targetId=t["targetId"])["status"]
                if st == "READY":
                    print(f"smoke test ok (gateway target {t['name']} re-synced tools/list)"); return
                if st.endswith("FAILED"):
                    sys.exit(f"smoke test failed: gateway target {t['name']} {st}")
            sys.exit(f"smoke test: gateway target {t['name']} not READY in time")
    print("smoke test skipped: runtime restricted and no gateway target points at it yet (20-gateway.py adds it)")


def save_env(name: str, g: dict) -> str:
    """<NAME>_RUNTIME_ARN / _ID / _MCP_URL 을 deploy/agentcore/.env 에 쓴다(같은 키는 교체). MCP URL 을 돌려준다.

    Write <NAME>_RUNTIME_ARN / _ID / _MCP_URL into deploy/agentcore/.env (same keys replaced). Returns the MCP URL.
    """
    key = name.upper()
    mcp_url = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{quote(g['agentRuntimeArn'], safe='')}/invocations?qualifier=DEFAULT"
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    lines = [l for l in lines if not l.startswith(f"{key}_RUNTIME_")]
    lines += [f"{key}_RUNTIME_ARN={g['agentRuntimeArn']}", f"{key}_RUNTIME_ID={g['agentRuntimeId']}", f"{key}_RUNTIME_MCP_URL={mcp_url}"]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return mcp_url


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if "--artifact" in sys.argv:
        ARTIFACT = sys.argv[sys.argv.index("--artifact") + 1]
    if ARTIFACT not in ("container", "zip"):
        sys.exit(f"unknown artifact {ARTIFACT!r} (container | zip)")
    agent_dir = (ROOT / sys.argv[1]).resolve()
    name = agent_name_for(agent_dir)
    if ARTIFACT == "container":
        ref = build_and_push(agent_dir, name)
    else:
        ensure_bucket(); ref = package_and_upload(agent_dir, name)
    g = create_or_update(name, ref, ensure_role(name))
    url = save_env(name, g)
    print(f"READY {name}: id={g['agentRuntimeId']} platform={g.get('platformVersion')} version={g['agentRuntimeVersion']}\n  mcp_url={url}")
    smoke_test(g, url)
