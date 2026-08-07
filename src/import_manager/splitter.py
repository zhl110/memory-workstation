"""DocumentSplitter — 文档分割器

职责：将大文档按段落/章节分割为多个 chunk，供后续分类处理
策略：
  - Markdown 按标题分割
  - 古典小说按回目分割（第X回 / 第X章）
  - 无标题按段落分割
  - 超长段落按句号分割
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# 默认参数
MAX_CHUNK_SIZE = 8000    # 单 chunk 最大字符数
MIN_CHUNK_SIZE = 50      # 最小 chunk 字符数（过短合并到前一个）
OVERLAP_SIZE = 200       # chunk 间重叠字符数（保持上下文连续性）

# 古典小说回目正则：第[一二三四五六七八九十百千零〇0-9]+[回章节卷集部篇]
CHAPTER_PATTERN = re.compile(
    r'^第[一二三四五六七八九十百千零〇\d]+[回章节卷集部篇].*$',
    re.MULTILINE,
)


@dataclass
class Chunk:
    """分割后的文档片段"""
    index: int               # 在原文中的顺序（从 0 开始）
    content: str             # chunk 内容
    heading: str = ""        # 所属章节标题（如果有）
    start_offset: int = 0    # 在原文中的起始偏移
    end_offset: int = 0      # 在原文中的结束偏移


class DocumentSplitter:
    """文档分割器：大文档 → 多个 chunk"""

    def __init__(self, max_size: int = MAX_CHUNK_SIZE,
                 min_size: int = MIN_CHUNK_SIZE,
                 overlap: int = OVERLAP_SIZE):
        self.max_size = max_size
        self.min_size = min_size
        self.overlap = overlap

    def split(self, content: str, source_type: str = "text") -> list[Chunk]:
        """分割文档内容为多个 chunk

        Args:
            content: 原始文档内容
            source_type: 来源类型（markdown/text/json 等）

        Returns:
            Chunk 列表，按顺序排列
        """
        if not content or not content.strip():
            return []

        # 短文档不分割
        if len(content) <= self.max_size:
            return [Chunk(
                index=0,
                content=content.strip(),
                heading=self._extract_heading(content),
                start_offset=0,
                end_offset=len(content),
            )]

        # 按来源类型选择分割策略
        if source_type == "markdown":
            chunks = self._split_by_heading(content)
        elif source_type == "json":
            chunks = self._split_by_record(content)
        elif self._has_chapter_markers(content):
            # 古典小说回目分割（检测到"第X回"等标记时自动触发）
            chunks = self._split_by_chapter(content)
        else:
            chunks = self._split_by_paragraph(content)

        # 合并过短的 chunk
        chunks = self._merge_short_chunks(chunks)

        # 超长 chunk 二次分割
        final = []
        for chunk in chunks:
            if len(chunk.content) > self.max_size:
                sub = self._split_by_sentence(chunk)
                final.extend(sub)
            else:
                final.append(chunk)

        # 重新编号
        for i, c in enumerate(final):
            c.index = i

        return final

    def _split_by_heading(self, content: str) -> list[Chunk]:
        """Markdown 按标题分割（# / ## / ###）"""
        lines = content.split("\n")
        chunks = []
        current_heading = ""
        current_lines = []
        current_start = 0

        for line in lines:
            if re.match(r'^#{1,3}\s+', line):
                # 遇到标题，保存当前 chunk
                if current_lines:
                    text = "\n".join(current_lines).strip()
                    if text:
                        chunks.append(Chunk(
                            index=len(chunks),
                            content=text,
                            heading=current_heading,
                            start_offset=current_start,
                            end_offset=current_start + len(text),
                        ))
                current_heading = line.strip("# ").strip()
                current_lines = [line]
                current_start = content.find(line)
            else:
                current_lines.append(line)

        # 最后一个 chunk
        if current_lines:
            text = "\n".join(current_lines).strip()
            if text:
                chunks.append(Chunk(
                    index=len(chunks),
                    content=text,
                    heading=current_heading,
                    start_offset=current_start,
                    end_offset=current_start + len(text),
                ))

        return chunks

    def _split_by_paragraph(self, content: str) -> list[Chunk]:
        """按段落分割（空行分隔）"""
        paragraphs = re.split(r'\n\s*\n', content)
        chunks = []
        offset = 0

        for para in paragraphs:
            para = para.strip()
            if para:
                chunks.append(Chunk(
                    index=len(chunks),
                    content=para,
                    start_offset=offset,
                    end_offset=offset + len(para),
                ))
            offset += len(para) + 2  # +2 for \n\n

        return chunks

    def _has_chapter_markers(self, content: str) -> bool:
        """检测是否包含古典小说回目标记（第X回/第X章等）"""
        matches = CHAPTER_PATTERN.findall(content[:5000])  # 只检查前 5000 字
        return len(matches) >= 3  # 至少 3 个回目标记才认为是古典小说

    def _split_by_chapter(self, content: str) -> list[Chunk]:
        """古典小说回目分割：按「第X回」「第X章」等标记切分

        支持格式：
        - 第一回 甄士隐梦幻识通灵
        - 第XX回 xxxxx
        - 第1回 xxxxx
        - 第一章 xxxxx
        - 第三卷 xxxxx
        """
        # 用回目正则切分
        parts = re.split(r'(?=第[一二三四五六七八九十百千零〇\d]+[回章节卷集部篇])', content)
        chunks = []
        offset = 0

        for part in parts:
            part = part.strip()
            if not part:
                offset += len(part) + 1
                continue

            # 提取回目标题（第一行）
            first_line = part.split("\n", 1)[0].strip()
            heading = first_line if len(first_line) < 100 else first_line[:100]

            chunks.append(Chunk(
                index=len(chunks),
                content=part,
                heading=heading,
                start_offset=offset,
                end_offset=offset + len(part),
            ))
            offset += len(part) + 1

        return chunks

    def _split_by_record(self, content: str) -> list[Chunk]:
        """JSON/JSONL 按记录分割"""
        lines = content.strip().split("\n")
        chunks = []
        offset = 0

        for line in lines:
            line = line.strip()
            if line:
                chunks.append(Chunk(
                    index=len(chunks),
                    content=line,
                    start_offset=offset,
                    end_offset=offset + len(line),
                ))
            offset += len(line) + 1

        return chunks

    def _split_by_sentence(self, chunk: Chunk) -> list[Chunk]:
        """超长 chunk 按句号二次分割"""
        sentences = re.split(r'(?<=[。！？.!?])\s*', chunk.content)
        sub_chunks = []
        current = []
        current_len = 0

        for sent in sentences:
            if current_len + len(sent) > self.max_size and current:
                text = " ".join(current).strip()
                sub_chunks.append(Chunk(
                    index=len(sub_chunks),
                    content=text,
                    heading=chunk.heading,
                    start_offset=chunk.start_offset + current_len,
                    end_offset=chunk.start_offset + current_len + len(text),
                ))
                # 保留 overlap
                if self.overlap > 0 and current:
                    overlap_text = current[-1][-self.overlap:] if current else ""
                    current = [overlap_text, sent] if overlap_text else [sent]
                    current_len = len(overlap_text) + len(sent)
                else:
                    current = [sent]
                    current_len = len(sent)
            else:
                current.append(sent)
                current_len += len(sent)

        if current:
            text = " ".join(current).strip()
            sub_chunks.append(Chunk(
                index=len(sub_chunks),
                content=text,
                heading=chunk.heading,
                start_offset=chunk.start_offset + current_len,
                end_offset=chunk.start_offset + current_len + len(text),
            ))

        return sub_chunks

    def _merge_short_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """合并过短的 chunk 到前一个"""
        if not chunks:
            return []

        merged = [chunks[0]]
        for chunk in chunks[1:]:
            if len(chunk.content) < self.min_size and merged:
                # 合并到前一个
                prev = merged[-1]
                prev.content = prev.content + "\n\n" + chunk.content
                prev.end_offset = chunk.end_offset
                prev.heading = prev.heading or chunk.heading
            else:
                merged.append(chunk)

        return merged

    def _extract_heading(self, content: str) -> str:
        """提取第一个标题"""
        match = re.search(r'^#{1,3}\s+(.+)$', content, re.MULTILINE)
        return match.group(1).strip() if match else ""
