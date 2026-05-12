# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/zhihu/core.py
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


# -*- coding: utf-8 -*-
import asyncio
import os
# import random  # Removed as we now use fixed config.CRAWLER_MAX_SLEEP_SEC intervals
from asyncio import Task
from typing import Dict, List, Optional, Tuple, cast

from playwright.async_api import (
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    async_playwright,
)

import config
from constant import zhihu as constant
from base.base_crawler import AbstractCrawler
from model.m_zhihu import ZhihuContent, ZhihuCreator
from proxy.proxy_ip_pool import IpInfoModel, create_ip_pool
from store import zhihu as zhihu_store
from tools import utils
from tools.cdp_browser import CDPBrowserManager
from var import crawler_type_var, source_keyword_var

from .client import ZhiHuClient
from .exception import DataFetchError
from .help import ZhihuExtractor, judge_zhihu_url
from .login import ZhiHuLogin


class ZhihuCrawler(AbstractCrawler):
    context_page: Page
    zhihu_client: ZhiHuClient
    browser_context: BrowserContext
    cdp_manager: Optional[CDPBrowserManager]

    def __init__(self) -> None:
        self.index_url = "https://www.zhihu.com"
        self.cookie_urls = [self.index_url]
        # self.user_agent = utils.get_user_agent()
        self.user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        self._extractor = ZhihuExtractor()
        self.cdp_manager = None
        self.ip_proxy_pool = None  # Proxy IP pool for automatic proxy refresh

    async def start(self) -> None:
        """
        Start the crawler
        Returns:

        """
        playwright_proxy_format, httpx_proxy_format = None, None
        if config.ENABLE_IP_PROXY:
            self.ip_proxy_pool = await create_ip_pool(
                config.IP_PROXY_POOL_COUNT, enable_validate_ip=True
            )
            ip_proxy_info: IpInfoModel = await self.ip_proxy_pool.get_proxy()
            playwright_proxy_format, httpx_proxy_format = utils.format_proxy_info(
                ip_proxy_info
            )

        async with async_playwright() as playwright:
            # Choose launch mode based on configuration
            if config.ENABLE_CDP_MODE:
                utils.logger.info("[ZhihuCrawler] Launching browser in CDP mode")
                self.browser_context = await self.launch_browser_with_cdp(
                    playwright,
                    playwright_proxy_format,
                    self.user_agent,
                    headless=config.CDP_HEADLESS,
                )
            else:
                utils.logger.info("[ZhihuCrawler] Launching browser in standard mode")
                # Launch a browser context.
                chromium = playwright.chromium
                self.browser_context = await self.launch_browser(
                    chromium, None, self.user_agent, headless=config.HEADLESS
                )
                # stealth.min.js is a js script to prevent the website from detecting the crawler.
                await self.browser_context.add_init_script(path="libs/stealth.min.js")

            self.context_page = await self.browser_context.new_page()
            await self.context_page.goto(self.index_url, wait_until="domcontentloaded")

            # Create a client to interact with the zhihu website.
            self.zhihu_client = await self.create_zhihu_client(httpx_proxy_format)
            if not await self.zhihu_client.pong():
                login_obj = ZhiHuLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",  # input your phone number
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                await self.zhihu_client.update_cookies(
                    browser_context=self.browser_context,
                    urls=self.cookie_urls,
                )

            # Zhihu's search API requires opening the search page first to access cookies, homepage alone won't work
            utils.logger.info(
                "[ZhihuCrawler.start] Zhihu navigating to search page to get search page cookies, this process takes about 5 seconds"
            )
            await self.context_page.goto(
                f"{self.index_url}/search?q=python&search_source=Guess&utm_content=search_hot&type=content"
            )
            await asyncio.sleep(5)
            await self.zhihu_client.update_cookies(
                browser_context=self.browser_context,
                urls=self.cookie_urls,
            )

            crawler_type_var.set(config.CRAWLER_TYPE)
            if config.CRAWLER_TYPE == "search":
                # Search for notes and retrieve their comment information.
                await self.search()
            elif config.CRAWLER_TYPE == "detail":
                # Get the information and comments of the specified post
                await self.get_specified_notes()
            elif config.CRAWLER_TYPE == "creator":
                # Get creator's information and their notes and comments
                await self.get_creators_and_notes()
            else:
                pass

            utils.logger.info("[ZhihuCrawler.start] Zhihu Crawler finished ...")

    async def search(self) -> None:
        """Search for notes and retrieve their comment information."""
        utils.logger.info("[ZhihuCrawler.search] Begin search zhihu keywords")
        zhihu_limit_count = 20
        if config.CRAWLER_MAX_NOTES_COUNT < zhihu_limit_count:
            config.CRAWLER_MAX_NOTES_COUNT = zhihu_limit_count
        start_page = config.START_PAGE
        answers_per_question = getattr(config, 'CRAWLER_ZHIHU_ANSWERS_PER_QUESTION', 1)
        question_count_limit = getattr(config, 'CRAWLER_ZHIHU_QUESTION_COUNT', 0)
        for keyword in config.KEYWORDS.split(","):
            source_keyword_var.set(keyword)
            utils.logger.info(f"[ZhihuCrawler.search] Current search keyword: {keyword}")
            page = 1
            collected_questions: Dict[str, str] = {}
            search_saved_count = 0
            while page <= 10:
                try:
                    utils.logger.info(f"[ZhihuCrawler.search] 搜索: {keyword}, page: {page}")
                    content_list: List[ZhihuContent] = (
                        await self.zhihu_client.get_note_by_keyword(keyword=keyword, page=page)
                    )
                    if not content_list:
                        utils.logger.info("没有更多搜索结果!")
                        break
                    for content in content_list:
                        if content.question_id:
                            if content.question_id not in collected_questions:
                                collected_questions[content.question_id] = content.title or ""
                                utils.logger.info(f"[ZhihuCrawler.search] 发现问题 {content.question_id}: {content.title}")
                            utils.logger.info(f"[ZhihuCrawler.search] 保存搜索结果回答: {content.content_id}")
                            await zhihu_store.update_zhihu_content(content)
                            search_saved_count += 1
                    utils.logger.info(f"[ZhihuCrawler.search] 已收集 {len(collected_questions)} 个问题，已保存 {search_saved_count} 条回答")
                    if question_count_limit > 0 and len(collected_questions) >= question_count_limit and search_saved_count >= question_count_limit:
                        utils.logger.info(f"[ZhihuCrawler.search] 已收集够 {question_count_limit} 个问题，停止翻页")
                        break
                    page += 1
                    await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                except DataFetchError:
                    utils.logger.error("[ZhihuCrawler.search] 搜索失败")
                    break
            utils.logger.info(f"[ZhihuCrawler.search] 第一阶段完成: {len(collected_questions)} 个问题, {search_saved_count} 条回答")
            for idx, (question_id, question_title) in enumerate(collected_questions.items()):
                if answers_per_question > 1:
                    utils.logger.info(f"[ZhihuCrawler.search] [{idx+1}/{len(collected_questions)}] 获取问题 {question_id} 的更多回答(目标{answers_per_question}个)...")
                    all_answers = await self.get_question_all_answers(question_id, answers_per_question)
                    for answer in all_answers:
                        if not answer.title and question_title:
                            answer.title = question_title
                        await zhihu_store.update_zhihu_content(answer)
                        if config.ENABLE_GET_COMMENTS:
                            await self.get_comments(answer, asyncio.Semaphore(1))
                        await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                else:
                    utils.logger.info(f"[ZhihuCrawler.search] 单回答模式，跳过额外获取")

    async def get_question_all_answers(self, question_id: str, max_answers: int = 3) -> List[ZhihuContent]:
        """获取问题的多个回答（极简版 - 只采集内容文本）"""
        import re
        def strip_html(text: str) -> str:
            if not text:
                return ""
            text = re.sub(r'<[^>]+>', '', text)
            text = re.sub(r'\s+', ' ', text).strip()
            return text
        all_answers: List[ZhihuContent] = []
        question_url = f"https://www.zhihu.com/question/{question_id}"
        question_title = ""
        try:
            page = await self.browser_context.new_page()
            # 设置更真实的用户代理和视口
            await page.set_viewport_size({"width": 1920, "height": 1080})
            
            # 先访问知乎首页获取cookie
            try:
                await page.goto("https://www.zhihu.com", wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(1)
            except:
                pass
            
            # 再访问问题页面
            try:
                await page.goto(question_url, wait_until="networkidle", timeout=30000)
            except Exception as e:
                if "TargetClosedError" in str(type(e).__name__) or "TargetClosedError" in str(e):
                    utils.logger.error(f"[采集] 浏览器已关闭，跳过问题 {question_id}")
                    await page.close()
                    return []
                utils.logger.warning(f"[采集] 页面加载超时，继续尝试: {e}")
            
            # 等待页面完全加载
            await asyncio.sleep(3)
            
            # 检查是否有"加载失败"，如果有则刷新
            for retry in range(3):
                try:
                    error_elem = await page.query_selector("text=加载失败了")
                    if error_elem:
                        utils.logger.info(f"[采集] 检测到加载失败，第{retry+1}次刷新...")
                        await page.reload(wait_until="networkidle", timeout=30000)
                        await asyncio.sleep(3)
                    else:
                        break
                except:
                    break
            
            try:
                title_elem = await page.query_selector("h1.QuestionHeader-title")
                if title_elem:
                    question_title = await title_elem.inner_text()
                    utils.logger.info(f"[采集] 问题: {question_title}")
            except:
                pass
            
            # 尝试点击"查看全部回答"或刷新回答列表
            view_all_selectors = [
                "button:has-text('查看全部回答')",
                "button:has-text('查看详情')",
                "button[class*='Button']:near(h1)",
                "text=再试试",  # 加载失败后的重试按钮
            ]
            for selector in view_all_selectors:
                try:
                    btn = await page.query_selector(selector)
                    if btn:
                        await btn.click()
                        utils.logger.info(f"[采集] 点击了: {selector}")
                        await asyncio.sleep(3)
                        break
                except:
                    continue
            
            # 等待回答加载
            await asyncio.sleep(2)
            
            scroll_attempts = 0
            max_scroll_attempts = 10
            while len(all_answers) < max_answers and scroll_attempts < max_scroll_attempts:
                scroll_attempts += 1
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
                
                # 尝试多种选择器
                answer_items = await page.query_selector_all(".List-item")
                if not answer_items:
                    answer_items = await page.query_selector_all(".ContentItem.AnswerItem")
                if not answer_items:
                    answer_items = await page.query_selector_all("[data-zop-question] .ContentItem")
                if not answer_items:
                    # 尝试更通用的选择器
                    answer_items = await page.query_selector_all("div[class*='AnswerItem']")
                
                utils.logger.info(f"[采集] 找到 {len(answer_items)} 个回答容器")
                for item in answer_items:
                    if len(all_answers) >= max_answers:
                        break
                    content_elem = None
                    for sel in ["[itemprop='text']", "[class*='RichText']", "[class*='ztext']"]:
                        content_elem = await item.query_selector(sel)
                        if content_elem:
                            break
                    if not content_elem:
                        continue
                    content_text = await content_elem.inner_text()
                    content_text = strip_html(content_text)
                    if len(content_text) < 50:
                        continue
                    content = ZhihuContent()
                    content.content_id = str(hash(content_text))[:12]
                    content.question_id = question_id
                    content.content_type = "answer"
                    content.content_text = content_text
                    content.title = question_title
                    content.content_url = question_url
                    content.source_keyword = source_keyword_var.get()
                    content.voteup_count = 0
                    content.comment_count = 0
                    all_answers.append(content)
                    utils.logger.info(f"[采集] 第{len(all_answers)}条: {content_text[:50]}...")
            await page.close()
        except Exception as e:
            utils.logger.error(f"[采集] 异常: {e}")
        utils.logger.info(f"[采集] 完成，共 {len(all_answers)} 条")
        return all_answers

    async def batch_get_content_comments(self, content_list: List[ZhihuContent]):
        """
        Batch get content comments
        Args:
            content_list:

        Returns:

        """
        if not config.ENABLE_GET_COMMENTS:
            utils.logger.info(
                f"[ZhihuCrawler.batch_get_content_comments] Crawling comment mode is not enabled"
            )
            return

        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list: List[Task] = []
        for content_item in content_list:
            task = asyncio.create_task(
                self.get_comments(content_item, semaphore), name=content_item.content_id
            )
            task_list.append(task)
        await asyncio.gather(*task_list)

    async def get_comments(
        self, content_item: ZhihuContent, semaphore: asyncio.Semaphore
    ):
        """
        Get note comments with keyword filtering and quantity limitation
        Args:
            content_item:
            semaphore:

        Returns:

        """
        async with semaphore:
            utils.logger.info(
                f"[ZhihuCrawler.get_comments] Begin get note id comments {content_item.content_id}"
            )

            # Sleep before fetching comments
            await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
            utils.logger.info(f"[ZhihuCrawler.get_comments] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds before fetching comments for content {content_item.content_id}")

            await self.zhihu_client.get_note_all_comments(
                content=content_item,
                crawl_interval=config.CRAWLER_MAX_SLEEP_SEC,
                callback=zhihu_store.batch_update_zhihu_note_comments,
            )

    async def get_creators_and_notes(self) -> None:
        """
        Get creator's information and their notes and comments
        Returns:

        """
        utils.logger.info(
            "[ZhihuCrawler.get_creators_and_notes] Begin get xiaohongshu creators"
        )
        for user_link in config.ZHIHU_CREATOR_URL_LIST:
            utils.logger.info(
                f"[ZhihuCrawler.get_creators_and_notes] Begin get creator {user_link}"
            )
            user_url_token = user_link.split("/")[-1]
            # get creator detail info from web html content
            createor_info: ZhihuCreator = await self.zhihu_client.get_creator_info(
                url_token=user_url_token
            )
            if not createor_info:
                utils.logger.info(
                    f"[ZhihuCrawler.get_creators_and_notes] Creator {user_url_token} not found"
                )
                continue

            utils.logger.info(
                f"[ZhihuCrawler.get_creators_and_notes] Creator info: {createor_info}"
            )
            await zhihu_store.save_creator(creator=createor_info)

            # By default, only answer information is extracted, uncomment below if articles and videos are needed

            # Get all anwser information of the creator
            all_content_list = await self.zhihu_client.get_all_anwser_by_creator(
                creator=createor_info,
                crawl_interval=config.CRAWLER_MAX_SLEEP_SEC,
                callback=zhihu_store.batch_update_zhihu_contents,
            )

            # Get all articles of the creator's contents
            # all_content_list = await self.zhihu_client.get_all_articles_by_creator(
            #     creator=createor_info,
            #     crawl_interval=config.CRAWLER_MAX_SLEEP_SEC,
            #     callback=zhihu_store.batch_update_zhihu_contents
            # )

            # Get all videos of the creator's contents
            # all_content_list = await self.zhihu_client.get_all_videos_by_creator(
            #     creator=createor_info,
            #     crawl_interval=config.CRAWLER_MAX_SLEEP_SEC,
            #     callback=zhihu_store.batch_update_zhihu_contents
            # )

            # Get all comments of the creator's contents
            await self.batch_get_content_comments(all_content_list)

    async def get_note_detail(
        self, full_note_url: str, semaphore: asyncio.Semaphore
    ) -> Optional[ZhihuContent]:
        """
        Get note detail
        Args:
            full_note_url: str
            semaphore:

        Returns:

        """
        async with semaphore:
            utils.logger.info(
                f"[ZhihuCrawler.get_specified_notes] Begin get specified note {full_note_url}"
            )
            # Judge note type
            note_type: str = judge_zhihu_url(full_note_url)
            if note_type == constant.ANSWER_NAME:
                question_id = full_note_url.split("/")[-3]
                answer_id = full_note_url.split("/")[-1]
                utils.logger.info(
                    f"[ZhihuCrawler.get_specified_notes] Get answer info, question_id: {question_id}, answer_id: {answer_id}"
                )
                result = await self.zhihu_client.get_answer_info(question_id, answer_id)

                # Sleep after fetching answer details
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                utils.logger.info(f"[ZhihuCrawler.get_note_detail] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after fetching answer details {answer_id}")

                return result

            elif note_type == constant.ARTICLE_NAME:
                article_id = full_note_url.split("/")[-1]
                utils.logger.info(
                    f"[ZhihuCrawler.get_specified_notes] Get article info, article_id: {article_id}"
                )
                result = await self.zhihu_client.get_article_info(article_id)

                # Sleep after fetching article details
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                utils.logger.info(f"[ZhihuCrawler.get_note_detail] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after fetching article details {article_id}")

                return result

            elif note_type == constant.VIDEO_NAME:
                video_id = full_note_url.split("/")[-1]
                utils.logger.info(
                    f"[ZhihuCrawler.get_specified_notes] Get video info, video_id: {video_id}"
                )
                result = await self.zhihu_client.get_video_info(video_id)

                # Sleep after fetching video details
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                utils.logger.info(f"[ZhihuCrawler.get_note_detail] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after fetching video details {video_id}")

                return result

    async def get_specified_notes(self):
        """
        Get the information and comments of the specified post
        Returns:

        """
        get_note_detail_task_list = []
        for full_note_url in config.ZHIHU_SPECIFIED_ID_LIST:
            # remove query params
            full_note_url = full_note_url.split("?")[0]
            crawler_task = self.get_note_detail(
                full_note_url=full_note_url,
                semaphore=asyncio.Semaphore(config.MAX_CONCURRENCY_NUM),
            )
            get_note_detail_task_list.append(crawler_task)

        need_get_comment_notes: List[ZhihuContent] = []
        note_details = await asyncio.gather(*get_note_detail_task_list)
        for index, note_detail in enumerate(note_details):
            if not note_detail:
                utils.logger.info(
                    f"[ZhihuCrawler.get_specified_notes] Note {config.ZHIHU_SPECIFIED_ID_LIST[index]} not found"
                )
                continue

            note_detail = cast(ZhihuContent, note_detail)  # only for type check
            need_get_comment_notes.append(note_detail)
            await zhihu_store.update_zhihu_content(note_detail)

        await self.batch_get_content_comments(need_get_comment_notes)

    async def create_zhihu_client(self, httpx_proxy: Optional[str]) -> ZhiHuClient:
        """Create zhihu client"""
        utils.logger.info(
            "[ZhihuCrawler.create_zhihu_client] Begin create zhihu API client ..."
        )
        cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
            self.browser_context,
            urls=self.cookie_urls,
        )
        zhihu_client_obj = ZhiHuClient(
            proxy=httpx_proxy,
            headers={
                "accept": "*/*",
                "accept-language": "zh-CN,zh;q=0.9",
                "cookie": cookie_str,
                "priority": "u=1, i",
                "referer": "https://www.zhihu.com/search?q=python&time_interval=a_year&type=content",
                "user-agent": self.user_agent,
                "x-api-version": "3.0.91",
                "x-app-za": "OS=Web",
                "x-requested-with": "fetch",
                "x-zse-93": "101_3_3.0",
            },
            playwright_page=self.context_page,
            cookie_dict=cookie_dict,
            proxy_ip_pool=self.ip_proxy_pool,  # Pass proxy pool for automatic refresh
        )
        return zhihu_client_obj

    async def launch_browser(
        self,
        chromium: BrowserType,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """Launch browser and create browser context"""
        utils.logger.info(
            "[ZhihuCrawler.launch_browser] Begin create browser context ..."
        )
        if config.SAVE_LOGIN_STATE:
            # feat issue #14
            # we will save login state to avoid login every time
            user_data_dir = os.path.join(
                os.getcwd(), "browser_data", config.USER_DATA_DIR % config.PLATFORM
            )  # type: ignore
            browser_context = await chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                accept_downloads=True,
                headless=headless,
                proxy=playwright_proxy,  # type: ignore
                viewport={"width": 1920, "height": 1080},
                user_agent=user_agent,
                channel="chrome",  # Use system Chrome stable version
            )
            return browser_context
        else:
            browser = await chromium.launch(headless=headless, proxy=playwright_proxy, channel="chrome")  # type: ignore
            browser_context = await browser.new_context(
                viewport={"width": 1920, "height": 1080}, user_agent=user_agent
            )
            return browser_context

    async def launch_browser_with_cdp(
        self,
        playwright: Playwright,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """
        Launch browser using CDP mode
        """
        try:
            self.cdp_manager = CDPBrowserManager()
            browser_context = await self.cdp_manager.launch_and_connect(
                playwright=playwright,
                playwright_proxy=playwright_proxy,
                user_agent=user_agent,
                headless=headless,
            )

            # Display browser information
            browser_info = await self.cdp_manager.get_browser_info()
            utils.logger.info(f"[ZhihuCrawler] CDP browser info: {browser_info}")

            return browser_context

        except Exception as e:
            utils.logger.error(f"[ZhihuCrawler] CDP mode launch failed, falling back to standard mode: {e}")
            # Fall back to standard mode
            chromium = playwright.chromium
            return await self.launch_browser(
                chromium, playwright_proxy, user_agent, headless
            )

    async def close(self):
        """Close browser context"""
        # Special handling if using CDP mode
        if self.cdp_manager:
            await self.cdp_manager.cleanup()
            self.cdp_manager = None
        else:
            await self.browser_context.close()
        utils.logger.info("[ZhihuCrawler.close] Browser context closed ...")
