# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tools/async_file_writer.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import asyncio
import csv
import json
import os
import pathlib
from typing import Dict, List
import aiofiles
import config
from tools.utils import utils
from tools.words import AsyncWordCloudGenerator


# 模块级全局缓存，所有 AsyncFileWriter 实例共享同一份文件路径
_FILE_PATH_GLOBAL_CACHE: Dict[str, str] = {}


class AsyncFileWriter:
    def __init__(self, platform: str, crawler_type: str):
        self.lock = asyncio.Lock()
        self.platform = platform
        self.crawler_type = crawler_type
        self._file_path_cache: Dict[str, str] = {}
        self.wordcloud_generator = AsyncWordCloudGenerator() if config.ENABLE_GET_WORDCLOUD else None

    def _get_file_path(self, file_type: str, item_type: str) -> str:
        cache_key = f"{file_type}_{item_type}"
        if cache_key in _FILE_PATH_GLOBAL_CACHE:
            return _FILE_PATH_GLOBAL_CACHE[cache_key]

        if config.SAVE_DATA_PATH:
            base_path = f"{config.SAVE_DATA_PATH}/{self.platform}/{file_type}"
        else:
            base_path = f"data/{self.platform}/{file_type}"
        pathlib.Path(base_path).mkdir(parents=True, exist_ok=True)

        # 拼接文件名，加入所有关键词，用下划线连接；过滤非法文件名字符
        keyword = ""
        if hasattr(config, "KEYWORDS") and config.KEYWORDS:
            raw_kws = [kw.strip() for kw in str(config.KEYWORDS).split(",") if kw.strip()]
            safe_kws = []
            for kw in raw_kws:
                for ch in ["\\", "/", ":", "*", "?", '"', "<", ">", "|"]:
                    kw = kw.replace(ch, "_")
                if kw:
                    safe_kws.append(kw)
            keyword = f"_{'_'.join(safe_kws)}" if safe_kws else ""

        base_name = f"{self.crawler_type}_{item_type}_{utils.get_current_date()}{keyword}"
        file_name = f"{base_name}.{file_type}"
        file_path = f"{base_path}/{file_name}"

        # 如果文件名已存在，加 (1)(2)... 后缀
        if os.path.exists(file_path):
            counter = 1
            while True:
                file_name = f"{base_name}({counter}).{file_type}"
                file_path = f"{base_path}/{file_name}"
                if not os.path.exists(file_path):
                    break
                counter += 1

        _FILE_PATH_GLOBAL_CACHE[cache_key] = file_path
        return file_path

    async def write_to_csv(self, item: Dict, item_type: str):
        file_path = self._get_file_path('csv', item_type)
        async with self.lock:
            file_exists = os.path.exists(file_path)
            async with aiofiles.open(file_path, 'a', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=item.keys())
                if not file_exists or await f.tell() == 0:
                    await writer.writeheader()
                await writer.writerow(item)

    async def write_to_jsonl(self, item: Dict, item_type: str):
        file_path = self._get_file_path('jsonl', item_type)
        async with self.lock:
            async with aiofiles.open(file_path, 'a', encoding='utf-8') as f:
                await f.write(json.dumps(item, ensure_ascii=False) + '\n')

    async def write_single_item_to_json(self, item: Dict, item_type: str):
        file_path = self._get_file_path('json', item_type)
        async with self.lock:
            existing_data = []
            if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    try:
                        content = await f.read()
                        if content:
                            existing_data = json.loads(content)
                        if not isinstance(existing_data, list):
                            existing_data = [existing_data]
                    except json.JSONDecodeError:
                        existing_data = []

            existing_data.append(item)

            async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(existing_data, ensure_ascii=False, indent=4))

    async def generate_wordcloud_from_comments(self):
        """
        Generate wordcloud from comments data
        Only works when ENABLE_GET_WORDCLOUD and ENABLE_GET_COMMENTS are True
        """
        if not config.ENABLE_GET_WORDCLOUD or not config.ENABLE_GET_COMMENTS:
            return

        if not self.wordcloud_generator:
            return

        try:
            # Read comments from JSON or JSONL file
            comments_data = []
            jsonl_file_path = self._get_file_path('jsonl', 'comments')
            json_file_path = self._get_file_path('json', 'comments')

            if os.path.exists(jsonl_file_path) and os.path.getsize(jsonl_file_path) > 0:
                async with aiofiles.open(jsonl_file_path, 'r', encoding='utf-8') as f:
                    async for line in f:
                        line = line.strip()
                        if line:
                            try:
                                comments_data.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            elif os.path.exists(json_file_path) and os.path.getsize(json_file_path) > 0:
                async with aiofiles.open(json_file_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    if content:
                        comments_data = json.loads(content)
                        if not isinstance(comments_data, list):
                            comments_data = [comments_data]

            if not comments_data:
                utils.logger.info(f"[AsyncFileWriter.generate_wordcloud_from_comments] No comments data found")
                return

            # Filter comments data to only include 'content' field
            # Handle different comment data structures across platforms
            filtered_data = []
            for comment in comments_data:
                if isinstance(comment, dict):
                    # Try different possible content field names
                    content_text = comment.get('content') or comment.get('comment_text') or comment.get('text') or ''
                    if content_text:
                        filtered_data.append({'content': content_text})

            if not filtered_data:
                utils.logger.info(f"[AsyncFileWriter.generate_wordcloud_from_comments] No valid comment content found")
                return

            # Generate wordcloud
            if config.SAVE_DATA_PATH:
                words_base_path = f"{config.SAVE_DATA_PATH}/{self.platform}/words"
            else:
                words_base_path = f"data/{self.platform}/words"
            pathlib.Path(words_base_path).mkdir(parents=True, exist_ok=True)
            words_file_prefix = f"{words_base_path}/{self.crawler_type}_comments_{utils.get_current_date()}"

            utils.logger.info(f"[AsyncFileWriter.generate_wordcloud_from_comments] Generating wordcloud from {len(filtered_data)} comments")
            await self.wordcloud_generator.generate_word_frequency_and_cloud(filtered_data, words_file_prefix)
            utils.logger.info(f"[AsyncFileWriter.generate_wordcloud_from_comments] Wordcloud generated successfully at {words_file_prefix}")

        except Exception as e:
            utils.logger.error(f"[AsyncFileWriter.generate_wordcloud_from_comments] Error generating wordcloud: {e}")
