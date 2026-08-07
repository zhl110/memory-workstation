"""test_security.py — 安全功能：密钥检测 / 脱敏"""
import pytest
from mw_sdk.security import detect_secrets, redact_secrets


class TestDetectSecrets:
    def test_detect_openai_key(self):
        text = "sk-1234567890abcdef1234567890abcdef1234567890abcdef12"
        findings = detect_secrets(text)
        assert len(findings) > 0
        assert findings[0]['type'] == 'OPENAI_KEY'

    def test_detect_aws_key(self):
        text = "AKIAIOSFODNN7EXAMPLE"
        findings = detect_secrets(text)
        assert len(findings) > 0
        assert findings[0]['type'] == 'AWS_KEY'

    def test_detect_no_secret(self):
        text = "这是一段普通的文本，没有密钥"
        assert detect_secrets(text) == []

    def test_detect_multiple(self):
        text = "key1: sk-1234567890abcdef1234567890abcdef1234567890abcdef12 and key2: AKIAIOSFODNN7EXAMPLE"
        findings = detect_secrets(text)
        assert len(findings) >= 2


class TestRedactSecrets:
    def test_redact_openai(self):
        text = "API key is sk-1234567890abcdef1234567890abcdef1234567890abcdef12 here"
        redacted = redact_secrets(text)
        assert "sk-" not in redacted
        assert "[REDACTED_OPENAI_KEY]" in redacted

    def test_redact_preserves_normal(self):
        text = "normal text without secrets"
        assert redact_secrets(text) == text
