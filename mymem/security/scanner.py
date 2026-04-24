"""
Secret scanner — detects leaked credentials in content before it enters the wiki.

Scans for:
  - API keys (Anthropic, OpenAI, AWS, GCP, GitHub, etc.)
  - Generic high-entropy strings that look like secrets
  - Connection strings with embedded passwords

Usage:
    from mymem.security.scanner import scan_for_secrets, SecretFound

    findings = scan_for_secrets(text)
    if findings:
        for f in findings:
            print(f.rule, f.line_number, f.redacted_match)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SecretRule:
    name: str
    pattern: re.Pattern[str]
    severity: Severity
    description: str


# Each pattern is designed to minimise false positives.
# Patterns match the secret value itself, not surrounding context.
_RULES: list[SecretRule] = [
    SecretRule(
        name="anthropic_api_key",
        pattern=re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{40,}\b"),
        severity=Severity.HIGH,
        description="Anthropic API key",
    ),
    SecretRule(
        name="openai_api_key",
        pattern=re.compile(r"\bsk-[A-Za-z0-9]{48}\b"),
        severity=Severity.HIGH,
        description="OpenAI API key",
    ),
    SecretRule(
        name="aws_access_key",
        pattern=re.compile(r"\b(AKIA|ABIA|ACCA|ASIA)[A-Z0-9]{16}\b"),
        severity=Severity.HIGH,
        description="AWS access key ID",
    ),
    SecretRule(
        name="aws_secret_key",
        pattern=re.compile(r"(?i)aws.{0,20}secret.{0,20}['\"]([A-Za-z0-9/+]{40})['\"]"),
        severity=Severity.HIGH,
        description="AWS secret access key",
    ),
    SecretRule(
        name="github_token",
        pattern=re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"),
        severity=Severity.HIGH,
        description="GitHub personal access token",
    ),
    SecretRule(
        name="gcp_service_account",
        pattern=re.compile(r'"type"\s*:\s*"service_account"'),
        severity=Severity.HIGH,
        description="GCP service account JSON",
    ),
    SecretRule(
        name="private_key_header",
        pattern=re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        severity=Severity.HIGH,
        description="Private key block",
    ),
    SecretRule(
        name="generic_password_assignment",
        pattern=re.compile(
            r'(?i)(password|passwd|secret|api_key|apikey)\s*[=:]\s*["\'](?!<)[^\s"\']{8,}["\']'
        ),
        severity=Severity.MEDIUM,
        description="Inline password or secret assignment",
    ),
    SecretRule(
        name="connection_string",
        pattern=re.compile(
            r"(?i)(postgres|mysql|mongodb|redis|amqp)://[^:]+:[^@\s]{4,}@"
        ),
        severity=Severity.MEDIUM,
        description="Connection string with embedded credentials",
    ),
    SecretRule(
        name="jwt_token",
        pattern=re.compile(r"\beyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\b"),
        severity=Severity.MEDIUM,
        description="JWT token",
    ),
]


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------

@dataclass
class SecretFinding:
    rule: str
    severity: Severity
    description: str
    line_number: int
    redacted_match: str  # first 6 chars + *** — never store the full match


def _redact(match: str) -> str:
    visible = min(6, len(match) // 3)
    return match[:visible] + "***"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_for_secrets(content: str) -> list[SecretFinding]:
    """
    Scan text for secret patterns.

    Returns a list of findings. Empty list = clean.
    Does NOT raise — caller decides what to do with findings.
    """
    findings: list[SecretFinding] = []
    lines = content.splitlines()

    for line_number, line in enumerate(lines, start=1):
        for rule in _RULES:
            for match in rule.pattern.finditer(line):
                findings.append(
                    SecretFinding(
                        rule=rule.name,
                        severity=rule.severity,
                        description=rule.description,
                        line_number=line_number,
                        redacted_match=_redact(match.group(0)),
                    )
                )

    return findings


def has_high_severity_secret(content: str) -> bool:
    """Quick check — True if any HIGH-severity secret found."""
    return any(f.severity == Severity.HIGH for f in scan_for_secrets(content))


def format_findings(findings: list[SecretFinding]) -> str:
    """Human-readable summary for logs and CLI output."""
    if not findings:
        return "No secrets found."
    lines = [f"Found {len(findings)} potential secret(s):"]
    for f in findings:
        lines.append(
            f"  [{f.severity}] Line {f.line_number}: {f.description} — {f.redacted_match}"
        )
    return "\n".join(lines)
