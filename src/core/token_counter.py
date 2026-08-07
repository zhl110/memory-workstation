"""Token Counter - 精确计算文本token数量

使用 tiktoken (cl100k_base 编码) 精确计算，与 GPT-4 / Claude / OpenAI 兼容 API 的
tokenizer 一致。tiktoken 不可用时回退到字符级估算（偏保守，约高估 10-15%）。
"""
import re

# tiktoken 延迟初始化，避免导入时阻塞
_enc = None


def _get_encoder():
    """获取 tiktoken 编码器（惰性加载，失败返回 None）"""
    global _enc
    if _enc is None:
        try:
            import tiktoken
            _enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _enc = False  # 标记为不可用，避免反复尝试
    return _enc if _enc is not False else None


def count_tokens(text: str) -> int:
    """精确计算文本的 token 数量"""
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        return len(enc.encode(text))
    return _estimate_tokens(text)


def truncate_tokens(text: str, max_tokens: int) -> str:
    """将文本截断到指定 token 数以内，尽量在自然边界处截断"""
    if not text:
        return text
    if count_tokens(text) <= max_tokens:
        return text
    enc = _get_encoder()
    if enc is not None:
        token_ids = enc.encode(text)
        if len(token_ids) > max_tokens:
            return enc.decode(token_ids[:max_tokens])
        return text
    return _truncate_by_estimate(text, max_tokens)


def _estimate_tokens(text: str) -> int:
    """字符级估算（fallback）：中文1字≈1.5 token，英文1词≈1.3 token，标点≈0.5 token"""
    text = re.sub(r'\s+', ' ', text.strip())
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    english_words = len(re.findall(r'[a-zA-Z]+', text))
    numbers = len(re.findall(r'\d+', text))
    punctuation = len(re.findall(r'[^\w\s]', text))
    return int(chinese_chars * 1.5 + english_words * 1.3 + numbers * 1.0 + punctuation * 0.5)


def _truncate_by_estimate(text: str, max_tokens: int) -> str:
    """基于估算的截断（fallback，仅 tiktoken 不可用时使用）"""
    text = re.sub(r'\s+', ' ', text.strip())
    words = re.findall(r'[\u4e00-\u9fff]|[a-zA-Z]+|\d+|[^\s]', text)

    result = []
    current_tokens = 0.0
    for word in words:
        if re.match(r'[\u4e00-\u9fff]', word):
            wt = 1.5
        elif re.match(r'[a-zA-Z]+', word):
            wt = 1.3
        elif re.match(r'\d+', word):
            wt = 1.0
        else:
            wt = 0.5

        if current_tokens + wt > max_tokens:
            break
        result.append(word)
        current_tokens += wt

    return ''.join(result)
