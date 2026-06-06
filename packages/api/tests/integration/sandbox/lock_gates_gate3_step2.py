"""D-12-12 Gate 3 step-2 probe — IMDSv2 GET-with-token decomposition.

Step-1 (the original Gate 3 diagnostic) confirmed Firecracker MMDS responds
to PUT /latest/api/token with a valid 48-byte token + IMDSv2-protocol
headers. The question this probe resolves is whether the token unlocks
exploitable metadata at the well-known sensitive paths, or whether the
sensitive paths return 401/404/empty (in which case Firecracker MMDS is
"open for token issuance but auth-gated above" — substrate-class limitation,
not disqualification).

**Per user spec:** PUT → GET within the same sandbox session (tokens may be
sandbox-scoped); four sensitive GETs, each with the token; print raw bytes;
NO classifier, NO interpretation; surface for human reading.

**Cost ceiling:** $0.005 hard stop. One sandbox, 5 HTTP calls. If anything
needs a second sandbox, stop and surface.

Methodology lesson surfaced by step-1 (folded into the audit trail):
the classifier in the step-1 script string-matched the body for the literal
word "token" to detect the IMDSv2 return — but the actual token is random
base64-shaped bytes that don't contain the word "token". The classifier
called it "ambiguous"; human re-read against the response **headers** (Server:
Firecracker API, X-Aws-Ec2-Metadata-Token-Ttl-Seconds, Content-Length: 48)
identified it unambiguously. **Pattern:** structured-shape interpretation
(headers + length + content-type triangulation) beats string-match against
expected response contents for protocol-shape detection in security probes.
This applies to any future gate-style probe in Spec 15+ provider tests.
"""

from __future__ import annotations

import contextlib
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(_REPO_ROOT / "packages/core/src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")


# Single probe script — runs inside one sandbox so the PUT-issued token is
# usable for the subsequent GETs. Prints structured lines per HTTP call.
PROBE_SCRIPT = r"""
import urllib.request
import urllib.error

def call(label, method, url, headers=None, timeout=10):
    req = urllib.request.Request(url, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        response = urllib.request.urlopen(req, timeout=timeout)
        status = response.status
        body = response.read()
        hdrs = dict(response.headers)
        print(f"PROBE:{label}::OUTCOME=ok")
        print(f"PROBE:{label}::STATUS={status}")
        print(f"PROBE:{label}::HEADERS={hdrs}")
        print(f"PROBE:{label}::BODY_LEN={len(body)}")
        print(f"PROBE:{label}::BODY_REPR={body!r}")
        try:
            decoded = body.decode("utf-8")
            print(f"PROBE:{label}::BODY_DECODED={decoded!r}")
        except UnicodeDecodeError:
            print(f"PROBE:{label}::BODY_DECODED=<binary; see BODY_REPR>")
        return status, body
    except urllib.error.HTTPError as e:
        body = e.read()
        hdrs = dict(e.headers)
        print(f"PROBE:{label}::OUTCOME=http_error")
        print(f"PROBE:{label}::STATUS={e.code}")
        print(f"PROBE:{label}::HEADERS={hdrs}")
        print(f"PROBE:{label}::BODY_LEN={len(body)}")
        print(f"PROBE:{label}::BODY_REPR={body!r}")
        try:
            decoded = body.decode("utf-8")
            print(f"PROBE:{label}::BODY_DECODED={decoded!r}")
        except UnicodeDecodeError:
            print(f"PROBE:{label}::BODY_DECODED=<binary; see BODY_REPR>")
        return e.code, body
    except Exception as e:
        print(f"PROBE:{label}::OUTCOME=exception")
        print(f"PROBE:{label}::EXC_TYPE={type(e).__name__}")
        print(f"PROBE:{label}::EXC_MSG={str(e)[:300]!r}")
        return None, None

# Step 1: PUT for token
print("=== STEP 1: PUT /latest/api/token ===")
status, token_bytes = call(
    "put_token",
    "PUT",
    "http://169.254.169.254/latest/api/token",
    headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
)

if not token_bytes:
    print("PROBE:fatal::NO_TOKEN_NO_FURTHER_PROBES")
    raise SystemExit(0)

token_str = token_bytes.decode("utf-8", errors="replace")
print(f"PROBE:put_token::TOKEN_REPR={token_str!r}")
print()

# Step 2: GET the four sensitive paths with the token
get_headers = {"X-aws-ec2-metadata-token": token_str}

print("=== STEP 2: GET /latest/meta-data/ (discovery root) ===")
call("get_discovery", "GET", "http://169.254.169.254/latest/meta-data/", headers=get_headers)
print()

print("=== STEP 3: GET /latest/meta-data/iam/security-credentials/ (LOAD-BEARING) ===")
call(
    "get_iam_creds",
    "GET",
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    headers=get_headers,
)
print()

print("=== STEP 4: GET /latest/meta-data/instance-id (informational) ===")
call(
    "get_instance_id",
    "GET",
    "http://169.254.169.254/latest/meta-data/instance-id",
    headers=get_headers,
)
print()

print("=== STEP 5: GET /latest/user-data (E2B-injected config) ===")
call(
    "get_user_data",
    "GET",
    "http://169.254.169.254/latest/user-data",
    headers=get_headers,
)
"""


PROBE_LABELS = (
    "put_token",
    "get_discovery",
    "get_iam_creds",
    "get_instance_id",
    "get_user_data",
)


def main() -> int:
    from e2b_code_interpreter import Sandbox  # type: ignore[import-not-found]

    print("=" * 70)
    print("D-12-12 GATE 3 STEP-2 PROBE — IMDSv2 GET-with-token")
    print("=" * 70)
    print()

    print("Creating sandbox with allow_internet_access=False (same as Gate 3)...")
    sandbox = Sandbox(allow_internet_access=False)
    print()

    try:
        t0 = time.perf_counter()
        result = sandbox.run_code(PROBE_SCRIPT, timeout=60)
        elapsed_s = time.perf_counter() - t0
        stdout_full = "".join(result.logs.stdout)
        stderr_full = "".join(result.logs.stderr)
        error = f"{result.error.name}: {result.error.value[:300]}" if result.error else ""
        # Print raw stdout
        print(stdout_full)
        if stderr_full.strip():
            print(f"STDERR: {stderr_full[:500]}")
        if error:
            print(f"RESULT_ERROR: {error}")

        # Parse PROBE: lines into structured per-label dicts
        per_label: dict[str, dict[str, str]] = {label: {} for label in PROBE_LABELS}
        for line in stdout_full.splitlines():
            if not line.startswith("PROBE:"):
                continue
            after = line[len("PROBE:") :]
            if "::" not in after:
                continue
            label, _, rest = after.partition("::")
            if label not in per_label or "=" not in rest:
                continue
            key, _, value = rest.partition("=")
            per_label[label][key] = value
    finally:
        with contextlib.suppress(Exception):
            sandbox.kill()

    # Write audit trail — raw bytes preserved per-path. NO classifier verdict.
    audit_dir = _REPO_ROOT / "docs/specs/phase2/spec_12/audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / "lock_gates_2026-06-05_gate3_step2.md"

    lines: list[str] = [
        "# D-12-12 Gate 3 step-2 probe — IMDSv2 GET-with-token decomposition — 2026-06-05",
        "",
        "**Status:** Surfaced for human verdict. NO classifier verdict in this file by design.",
        "**Substrate:** E2B Hobby tier (Firecracker microVM)",
        f"**Wall-clock for probe:** {elapsed_s:.1f} s",
        "",
        "## Predecessors",
        "",
        "- [`lock_gates_2_to_5_2026-06-05.md`](lock_gates_2_to_5_2026-06-05.md) — original Gate 3 "
        "run (5 BLOCKED + 3 ambiguous)",
        "- [`lock_gates_2026-06-05_gate3_diagnostic.md`]"
        "(lock_gates_2026-06-05_gate3_diagnostic.md) — step-1 diagnostic; "
        "resolved 2/3 ambiguities (loopback SSH documentable, v4-mapped v6 "
        "IMDS effectively safe); surfaced step-1 classifier miss on IMDSv2 PUT",
        "",
        "## Step-1 → step-2 carry-over (the classifier-miss lesson)",
        "",
        "**LF-12-2 (lesson, candidate carry-forward into D-12-16 amendment):** "
        "string-match heuristics on response bodies are unreliable for security "
        'probes. Step-1\'s classifier looked for the literal word `"token"` in '
        "the IMDSv2 PUT response body to detect a successful token return — but "
        "the actual token is 48 base64-shaped bytes "
        "(`4CPsMYQr5yZbR8x0p2dVosDIphj2HkmvbxPMKPdub8J1A/sz`) that do not "
        'contain the word "token". The classifier called the result "ambiguous"; '
        "human re-read against the response **headers** "
        "(`Server: Firecracker API`, `X-Aws-Ec2-Metadata-Token-Ttl-Seconds: "
        "21600`, `Content-Length: 48`, `Content-Type: text/plain`) identified "
        "the response unambiguously as a Firecracker IMDSv2 token.",
        "",
        "**Pattern:** structured-shape interpretation — **headers + length + content-type "
        "triangulation** — beats string-match against expected response contents for "
        "protocol-shape detection. Future gate-style probes (alternative substrate gates if "
        "D-12-12 reopens, Spec 15 image-gen provider gates, etc.) should rely on shape-detection "
        "from headers, not string-matches in bodies. This step-2 probe (below) applies the lesson: "
        "**NO classifier verdict in the audit; raw bytes surfaced for human reading.**",
        "",
        "## Methodology — what this probe tests",
        "",
        "Single sandbox, single script, PUT → 4 GETs back-to-back. Tokens may be sandbox-scoped; "
        "recreating the sandbox between PUT and GET would test a different threat model.",
        "",
        "The four GET paths probe whether the IMDSv2 token unlocks exploitable metadata at the "
        "well-known sensitive paths:",
        "",
        "1. `GET /latest/meta-data/` — discovery listing (canonical first call; tells us which "
        "other paths exist)",
        "2. `GET /latest/meta-data/iam/security-credentials/` — IAM role names (**LOAD-BEARING**: "
        "any non-401 response is the highest-risk path, instance-role STS credentials would be one "
        "GET away)",
        "3. `GET /latest/meta-data/instance-id` — instance identifier (informational; documentable "
        "if returned)",
        "4. `GET /latest/user-data` — user-data blob (where E2B might inject sandbox configuration "
        "secrets, if any)",
        "",
        "## Per-path responses (raw bytes preserved)",
        "",
    ]

    path_descriptions = {
        "put_token": "PUT /latest/api/token (step-1 reconfirm — same response as the diagnostic)",
        "get_discovery": "GET /latest/meta-data/ (discovery listing)",
        "get_iam_creds": "GET /latest/meta-data/iam/security-credentials/ (LOAD-BEARING)",
        "get_instance_id": "GET /latest/meta-data/instance-id (informational)",
        "get_user_data": "GET /latest/user-data (E2B-injected config)",
    }

    for label in PROBE_LABELS:
        lines.append(f"### {label} — {path_descriptions.get(label, '')}")
        lines.append("")
        d = per_label.get(label, {})
        if not d:
            lines.append("_(No output captured for this label — see stdout below.)_")
            lines.append("")
            continue
        outcome = d.get("OUTCOME", "?")
        status = d.get("STATUS", "?")
        body_len = d.get("BODY_LEN", "?")
        lines.append(f"- **Outcome:** `{outcome}`")
        lines.append(f"- **HTTP status:** `{status}`")
        lines.append(f"- **Body length:** `{body_len}`")
        lines.append("")
        if "HEADERS" in d:
            lines.append("**Headers:**")
            lines.append("")
            lines.append("```")
            lines.append(d["HEADERS"])
            lines.append("```")
            lines.append("")
        if "BODY_REPR" in d:
            lines.append("**Body bytes (repr):**")
            lines.append("")
            lines.append("```")
            lines.append(d["BODY_REPR"])
            lines.append("```")
            lines.append("")
        if "BODY_DECODED" in d:
            lines.append("**Body decoded (utf-8):**")
            lines.append("")
            lines.append("```")
            lines.append(d["BODY_DECODED"])
            lines.append("```")
            lines.append("")
        if "EXC_TYPE" in d:
            lines.append(f"**Exception:** `{d['EXC_TYPE']}: {d.get('EXC_MSG', '')}`")
            lines.append("")

    lines.append("## Full stdout (preserved verbatim)")
    lines.append("")
    lines.append("```")
    lines.append(stdout_full[:8000])
    lines.append("```")
    lines.append("")

    lines.append("## Decision tree (per user spec — verdict belongs to human)")
    lines.append("")
    lines.append("| `iam/security-credentials/` response | Verdict |")
    lines.append("|---|---|")
    lines.append(
        "| 401 / 404 / empty | Token issuance is open but sensitive paths are auth-gated above. "
        "**Effectively safe at v0.1.** Document Firecracker MMDS as substrate-class limitation "
        "alongside loopback:22 OpenSSH and 26-listening-port surface. Gate 3 PASSES with "
        "documented limitation; proceed to Gates 4 & 5. |"
    )
    lines.append(
        "| 200 with role names | **Real substrate disqualification.** Sandbox can enumerate IAM "
        "roles; STS credentials would be one GET away. D-12-12 reopens on security. Daytona "
        "(pending arch-doc verification) → self-Fly Machines. |"
    )
    lines.append(
        "| 200 with E2B user-data containing anything secret-shaped | Real substrate "
        "disqualification, lower severity than IAM. Document the exact bytes; D-12-12 reopens. |"
    )
    lines.append(
        "| 200 with E2B user-data containing benign config | Documentable; proceed with documented "
        "limitation. |"
    )
    lines.append("")
    lines.append(
        '**The Option-3 "Firecracker MMDS is architectural, not substrate" '
        "argument from the previous turn:** Firecracker MMDS is a documented "
        "feature, but it is **configurable by the substrate operator** — E2B "
        "chose to leave it enabled with open token issuance. Another "
        "Firecracker-based operator can disable it entirely. So this is a "
        "substrate configuration choice E2B made, not a Firecracker "
        "inevitability. Disqualifying E2B for this choice WOULD be "
        "substrate-disqualification IF the choice exposes exploitable "
        "metadata. The `iam/security-credentials/` response above resolves it."
    )
    lines.append("")
    lines.append(f"**Audit run timestamp:** {datetime.now(UTC).isoformat()}")

    audit_path.write_text("\n".join(lines))
    print()
    print(f"Audit trail written: {audit_path.relative_to(_REPO_ROOT)}")

    # Return 0 — no machine verdict; human reads the audit.
    return 0


if __name__ == "__main__":
    sys.exit(main())
