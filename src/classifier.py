"""Dynamic Classification System - 多维度分类+AI自扩展"""
import json
import logging
import re
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CATEGORIES = {
    "技术类": ["代码", "配置", "架构", "调试"],
    "业务类": ["需求", "规格", "决策"],
    "个人类": ["偏好", "习惯", "风格"],
    "参考类": ["事实", "数据", "规格"],
    "AI专属类.Skill开发": ["创建", "优化", "调试"],
    "AI专属类.Agent配置": ["部署", "模型", "API"],
    "AI专属类.Prompt工程": ["提示词", "指令"],
    "AI专属类.工具链": ["开发工具", "自动化"],
    "AI专属类.调试经验": ["排错", "问题", "解决"],
    "流程类": ["工作流", "检查清单", "步骤"],
    "知识类": ["原理", "概念", "教程"],
    "交互类": ["用户偏好", "决策记录", "反馈"],
    "日常类.健康": ["运动", "饮食", "睡眠"],
    "日常类.日程": ["会议", "安排", "提醒"],
    "日常类.财务": ["预算", "账单", "投资"],
    "日常类.购物": ["购买", "比价", "评价"],
    "日常类.旅行": ["攻略", "行程", "酒店"],
    "日常类.娱乐": ["电影", "游戏", "书籍"],
    "日常类.学习": ["课程", "笔记", "证书"],
    "日常类.社交": ["人脉", "活动", "关系"],
}

DEFAULT_KEYWORDS = {
    # 规则类
    "AI专属类.Agent配置": ["agent", "mcp", "配置", "CLAUDE.md", "rules.md", "constitution",
                          "必须", "禁止", "不得", "规定", "决定", "规则", "记住", "以后", "每次", "永远", "不要"],
    "AI专属类.Skill开发": ["skill", "SKILL.md", "触发词", "skill-creator", "skill配置",
                          "Skill", "Agent", "MCP", "Prompt", "脚本", "自动"],
    "AI专属类.Prompt工程": ["prompt", "提示词", "system prompt", "指令", "输出格式"],
    # 技术类
    "技术类.工具链": ["API", "架构", "配置", "依赖", "部署", "版本", "协议", "端口", "路径", "格式", "参数",
                    "playwright", "自动化", "脚本", "工具"],
    "技术类.调试经验": ["报错", "error", "修复", "fix", "问题", "解决",
                    "Git", "Docker", "数据库", "缓存", "队列", "日志", "调试", "优化"],
    # 流程类
    "流程类.工作流": ["流程", "规范", "标准", "方案", "计划", "验收", "测试", "打包", "发布", "迁移"],
    # 知识类
    "知识类": ["原理", "概念", "教程"],
    # 日常类
    "日常类.健康": ["运动", "健身", "饮食", "睡眠", "体重", "卡路里"],
    "日常类.日程": ["会议", "约会", "安排", "提醒", "日历"],
    "日常类.财务": ["预算", "账单", "工资", "投资", "理财"],
    "日常类.购物": ["购买", "比价", "评价", "推荐", "优惠"],
    "日常类.旅行": ["攻略", "行程", "酒店", "机票", "签证"],
    "日常类.娱乐": ["电影", "游戏", "书籍", "音乐", "动漫"],
}


class DynamicClassifier:
    def __init__(self, config=None):
        self.config = config
        self.categories = dict(DEFAULT_CATEGORIES)
        self.keywords = dict(DEFAULT_KEYWORDS)
        self.custom_categories = {}
        self.custom_keywords = {}
        self._load_custom_rules()
    
    @staticmethod
    def _rules_path() -> Path:
        from .core.config import _MEMORY_HOME
        return Path(_MEMORY_HOME) / "category_rules.json"

    def _load_custom_rules(self):
        rules_path = self._rules_path()
        if rules_path.exists():
            try:
                with open(rules_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.custom_categories = data.get("categories", {})
                    self.custom_keywords = data.get("keywords", {})
            except Exception as e:
                logger.warning("Failed to load custom rules: %s", e)
    
    def _save_custom_rules(self):
        rules_path = self._rules_path()
        rules_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(rules_path, "w", encoding="utf-8") as f:
                json.dump({
                    "categories": self.custom_categories,
                    "keywords": self.custom_keywords
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Failed to save custom rules: %s", e)
    
    def classify(self, content: str, filepath: str = "") -> dict:
        result = {
            "category": "未分类",
            "sub_category": "",
            "tags": [],
            "confidence": 0.0,
            "needs_review": False
        }
        
        all_categories = {**self.categories, **self.custom_categories}
        all_keywords = {**self.keywords, **self.custom_keywords}
        
        scores = {}
        for category, kws in all_keywords.items():
            score = 0
            matched_kws = []
            for kw in kws:
                if kw.lower() in content.lower():
                    score += 1
                    matched_kws.append(kw)
            if score > 0:
                scores[category] = {"score": score, "keywords": matched_kws}
        
        if scores:
            best = max(scores, key=lambda x: scores[x]["score"])
            best_score = scores[best]["score"]
            
            if best_score >= 2:
                parts = best.split(".", 1)
                result["category"] = parts[0]
                result["sub_category"] = parts[1] if len(parts) > 1 else ""
                result["tags"] = scores[best]["keywords"]
                result["confidence"] = min(best_score / 5.0, 1.0)
            elif best_score == 1:
                parts = best.split(".", 1)
                result["category"] = parts[0]
                result["sub_category"] = parts[1] if len(parts) > 1 else ""
                result["tags"] = scores[best]["keywords"]
                result["confidence"] = 0.4
                result["needs_review"] = True
        
        if filepath:
            fp = filepath.lower()
            if any(x in fp for x in ["skill", "prompt", "agent", "mcp"]):
                if result["confidence"] < 0.6:
                    result["category"] = "AI专属类"
                    result["sub_category"] = "通用"
                    result["needs_review"] = True
        
        if result["confidence"] < 0.3:
            result["needs_review"] = True
        
        return result
    
    def add_category(self, name: str, parent: str = "", keywords: list = None):
        if parent:
            full_name = f"{parent}.{name}"
        else:
            full_name = name
        
        self.custom_categories[full_name] = []
        if keywords:
            self.custom_keywords[full_name] = keywords
        
        self._save_custom_rules()
        logger.info("Added category: %s", full_name)
        return full_name
    
    def learn_from_feedback(self, category: str, content: str, filepath: str = ""):
        all_keywords = {**self.keywords, **self.custom_keywords}
        
        new_keywords = self._extract_keywords(content)
        
        if category not in self.custom_keywords:
            self.custom_keywords[category] = []
        
        for kw in new_keywords:
            if kw not in self.custom_keywords[category]:
                self.custom_keywords[category].append(kw)
        
        self._save_custom_rules()
        logger.info("Learned %d new keywords for %s", len(new_keywords), category)
    
    def _extract_keywords(self, content: str) -> list:
        words = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', content)
        
        word_freq = {}
        for w in words:
            if len(w) >= 2:
                w_lower = w.lower()
                word_freq[w_lower] = word_freq.get(w_lower, 0) + 1
        
        sorted_words = sorted(word_freq.items(), key=lambda x: -x[1])
        return [w for w, f in sorted_words[:10]]
    
    def get_all_categories(self) -> list:
        all_cats = {**self.categories, **self.custom_categories}
        return sorted(all_cats.keys())
    
    def suggest_category(self, content: str) -> list:
        suggestions = []
        all_categories = self.get_all_categories()
        
        for cat in all_categories:
            all_kw = {**self.keywords, **self.custom_keywords}
            if cat in all_kw:
                for kw in all_kw[cat]:
                    if kw.lower() in content.lower():
                        suggestions.append(cat)
                        break
        
        return suggestions if suggestions else ["未分类"]
    
    def auto_extract_keywords(self, db_conn):
        try:
            rows = db_conn.execute('''
                SELECT d.file_path, d.raw_text_snippet, c.content_category, c.sub_category
                FROM document_files d
                JOIN memory_classify c ON d.id = c.doc_id
                WHERE d.is_deleted = 0 AND c.content_category != ''
            ''').fetchall()
            
            category_words = {}
            for r in rows:
                cat = r['content_category']
                sub = r['sub_category'] or '通用'
                full_cat = f"{cat}.{sub}" if sub != '通用' else cat
                
                content = r['raw_text_snippet'] or ''
                filepath = r['file_path'] or ''
                
                words = set()
                words.update(re.findall(r'[\u4e00-\u9fff]{2,}', content))
                words.update(re.findall(r'[a-zA-Z]{3,}', content))
                
                path_words = re.findall(r'[a-zA-Z]{3,}', filepath)
                words.update(path_words)
                
                if full_cat not in category_words:
                    category_words[full_cat] = {}
                
                for w in words:
                    w_lower = w.lower()
                    if w_lower not in category_words[full_cat]:
                        category_words[full_cat][w_lower] = 0
                    category_words[full_cat][w_lower] += 1
            
            for cat, words in category_words.items():
                sorted_words = sorted(words.items(), key=lambda x: -x[1])
                top_words = [w for w, f in sorted_words[:20] if f >= 2]
                
                if cat not in self.custom_keywords:
                    self.custom_keywords[cat] = []
                
                for w in top_words:
                    if w not in self.custom_keywords[cat] and w not in self.keywords.get(cat, []):
                        self.custom_keywords[cat].append(w)
            
            self._save_custom_rules()
            logger.info("Auto-extracted keywords for %d categories", len(category_words))
            return len(category_words)
            
        except Exception as e:
            logger.error("Auto-extract keywords failed: %s", e)
            return 0
