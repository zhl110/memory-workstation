"""密钥脱敏模块 - 检测并脱敏API密钥"""

import re

SECRET_PATTERNS = [
    (r'AKIA[0-9A-Z]{16}', 'AWS_KEY'),
    (r'sk-[a-zA-Z0-9]{48}', 'OPENAI_KEY'),
    (r'(?:api[_-]?key|auth[_-]?token|access[_-]?token)\s*[:=]\s*[\'"]?[a-zA-Z0-9]{20,}', 'TOKEN'),
    (r'(?:password|passwd|pwd)\s*[:=]\s*[\'"]?.{8,}', 'PASSWORD'),
]


def detect_secrets(text: str) -> list[dict[str, str | int]]:
    """检测文本中的密钥

    Args:
        text: 要检测的文本

    Returns:
        密钥列表，每项包含type和count
    """
    findings: list[dict[str, str | int]] = []
    for pattern, name in SECRET_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            findings.append({'type': name, 'count': len(matches)})
    return findings


def redact_secrets(text: str) -> str:
    """脱敏文本中的密钥

    Args:
        text: 要脱敏的文本

    Returns:
        脱敏后的文本
    """
    for pattern, name in SECRET_PATTERNS:
        text = re.sub(pattern, f'[REDACTED_{name}]', text, flags=re.IGNORECASE)
    return text
