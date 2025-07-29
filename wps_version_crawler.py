#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import json
import yaml
import requests
import re
import shutil
from datetime import datetime
from bs4 import BeautifulSoup
from typing import Dict, List, Optional, Tuple, Set
import logging
from playwright.sync_api import sync_playwright, Route, Request
from tqdm import tqdm
import hashlib
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import aiohttp
import asyncio
from urllib.parse import urlparse

# 配置日志
# 确保logs目录存在
os.makedirs('logs', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/wps_crawler.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class Downloader:
    def __init__(self, num_threads=16, chunk_size=1024*1024*6):  # 16线程，4MB块大小
        self.num_threads = num_threads
        self.chunk_size = chunk_size
        self.session = requests.Session()
        # 配置会话，增加并发连接数
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=num_threads,
            pool_maxsize=num_threads,
            max_retries=5
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        # 配置会话
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Keep-Alive': 'timeout=60, max=1000'
        })

    def _download_chunk(self, url: str, start: int, end: int, file_path: str, pbar: tqdm) -> bool:
        """下载文件块"""
        headers = {
            'Range': f'bytes={start}-{end}',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive'
        }
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                response = self.session.get(url, headers=headers, stream=True, timeout=30)
                if response.status_code in (200, 206):
                    with open(file_path, 'rb+') as f:
                        f.seek(start)
                        for chunk in response.iter_content(chunk_size=16384):  # 增加读取块大小到16KB
                            if chunk:
                                f.write(chunk)
                                pbar.update(len(chunk))
                    return True
            except Exception as e:
                logger.warning(f"下载块 {start}-{end} 失败 (第 {retry_count + 1} 次): {str(e)}")
                retry_count += 1
                if retry_count < max_retries:
                    time.sleep(0.5)  # 减少重试等待时间到0.5秒
                    continue
                logger.error(f"下载块 {start}-{end} 最终失败: {str(e)}")
        return False

    async def _async_download_chunk(self, session: aiohttp.ClientSession, url: str, start: int, end: int, 
                                  file_path: str, pbar: tqdm) -> bool:
        """异步下载文件块"""
        headers = {
            'Range': f'bytes={start}-{end}',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive'
        }
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                async with session.get(url, headers=headers, timeout=30) as response:
                    if response.status in (200, 206):
                        with open(file_path, 'rb+') as f:
                            f.seek(start)
                            async for chunk in response.content.iter_chunked(16384):  # 增加读取块大小到16KB
                                if chunk:
                                    f.write(chunk)
                                    pbar.update(len(chunk))
                        return True
            except Exception as e:
                logger.warning(f"异步下载块 {start}-{end} 失败 (第 {retry_count + 1} 次): {str(e)}")
                retry_count += 1
                if retry_count < max_retries:
                    await asyncio.sleep(0.5)  # 减少重试等待时间到0.5秒
                    continue
                logger.error(f"异步下载块 {start}-{end} 最终失败: {str(e)}")
        return False

    def download_file(self, url: str, save_path: str) -> bool:
        """使用多线程下载文件"""
        try:
            # 获取文件大小
            response = self.session.head(url, allow_redirects=True, timeout=30)
            if response.status_code != 200:
                return False
            
            total_size = int(response.headers.get('content-length', 0))
            if total_size == 0:
                # 如果无法获取文件大小，使用单线程下载
                return self._download_single(url, save_path)
            
            # 创建空文件
            with open(save_path, 'wb') as f:
                f.truncate(total_size)
            
            # 计算分块，确保每个块至少1MB
            min_chunk_size = 1024 * 1024  # 1MB
            chunk_size = max(min_chunk_size, min(self.chunk_size, total_size // self.num_threads))
            chunks = []
            for start in range(0, total_size, chunk_size):
                end = min(start + chunk_size - 1, total_size - 1)
                chunks.append((start, end))
            
            # 使用进度条
            with tqdm(
                total=total_size,
                unit='iB',
                unit_scale=True,
                desc=os.path.basename(save_path)
            ) as pbar:
                # 使用线程池并发下载
                with ThreadPoolExecutor(max_workers=self.num_threads) as executor:
                    futures = []
                    for start, end in chunks:
                        future = executor.submit(
                            self._download_chunk, url, start, end, save_path, pbar
                        )
                        futures.append(future)
                    
                    # 等待所有下载完成
                    results = [f.result() for f in futures]
                    return all(results)
                
        except Exception as e:
            logger.error(f"下载文件失败 {url}: {str(e)}")
            if os.path.exists(save_path):
                os.remove(save_path)
            return False

    async def _async_download_file(self, url: str, save_path: str) -> bool:
        """异步下载文件"""
        try:
            # 配置 aiohttp 客户端会话
            timeout = aiohttp.ClientTimeout(total=300, connect=30)
            conn = aiohttp.TCPConnector(
                limit=self.num_threads,
                ttl_dns_cache=300,
                force_close=False,
                enable_cleanup_closed=True
            )
            
            async with aiohttp.ClientSession(
                timeout=timeout,
                connector=conn,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': '*/*',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive'
                }
            ) as session:
                async with session.head(url) as response:
                    if response.status != 200:
                        return False
                    total_size = int(response.headers.get('content-length', 0))
                
                if total_size == 0:
                    # 如果无法获取文件大小，使用单线程下载
                    return self._download_single(url, save_path)
                
                # 创建空文件
                with open(save_path, 'wb') as f:
                    f.truncate(total_size)
                
                # 计算分块，确保每个块至少1MB
                min_chunk_size = 1024 * 1024  # 1MB
                chunk_size = max(min_chunk_size, min(self.chunk_size, total_size // self.num_threads))
                chunks = []
                for start in range(0, total_size, chunk_size):
                    end = min(start + chunk_size - 1, total_size - 1)
                    chunks.append((start, end))
                
                # 使用进度条
                with tqdm(
                    total=total_size,
                    unit='iB',
                    unit_scale=True,
                    desc=os.path.basename(save_path)
                ) as pbar:
                    # 并发下载所有块
                    tasks = []
                    for start, end in chunks:
                        task = asyncio.create_task(
                            self._async_download_chunk(session, url, start, end, save_path, pbar)
                        )
                        tasks.append(task)
                    
                    results = await asyncio.gather(*tasks)
                    return all(results)
                
        except Exception as e:
            logger.error(f"异步下载文件失败 {url}: {str(e)}")
            if os.path.exists(save_path):
                os.remove(save_path)
            return False

    def _download_single(self, url: str, save_path: str) -> bool:
        """单线程下载文件（用于无法获取文件大小的情况）"""
        try:
            response = self.session.get(url, stream=True, timeout=30)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            with open(save_path, 'wb') as f, tqdm(
                desc=os.path.basename(save_path),
                total=total_size if total_size > 0 else None,
                unit='iB',
                unit_scale=True,
                unit_divisor=1024,
            ) as pbar:
                for chunk in response.iter_content(chunk_size=16384):  # 增加读取块大小到16KB
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))
            return True
        except Exception as e:
            logger.error(f"单线程下载文件失败 {url}: {str(e)}")
            if os.path.exists(save_path):
                os.remove(save_path)
            return False

class WPSVersionCrawler:
    def __init__(self):
        self.windows_download_base_url = "https://official-package.wpscdn.cn/wps/download"
        self.mac_download_base_url = "https://package.mac.wpscdn.cn/mac_wps_pkg/wps_installer"
        self.mac_url = "https://mac.wps.cn"
        self.versions_dir = "versions"
        self.downloads_dir = "downloads"
        self.history_file = "version_history.json"
        self._ensure_dirs()
        self._load_history()
        
        # 初始化下载器，使用更激进的设置
        self.downloader = Downloader(num_threads=16, chunk_size=1024*1024*4)  # 16线程，4MB块大小
        
        # 存储捕获的下载链接
        self.captured_urls: Set[str] = set()
        
        # 平台特定的请求头
        self.platform_headers = {
            "Windows": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive"
            },
            "macOS": {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive"
            }
        }

    def _ensure_dirs(self):
        """确保必要的目录存在"""
        # 创建版本信息目录
        if not os.path.exists(self.versions_dir):
            os.makedirs(self.versions_dir)
        
        # 创建下载目录
        if not os.path.exists(self.downloads_dir):
            os.makedirs(self.downloads_dir)
        
        # 为每个平台创建子目录
        for platform in ["windows", "macos"]:
            # 版本信息子目录
            platform_versions_dir = os.path.join(self.versions_dir, platform)
            if not os.path.exists(platform_versions_dir):
                os.makedirs(platform_versions_dir)
            
            # 下载子目录
            platform_downloads_dir = os.path.join(self.downloads_dir, platform)
            if not os.path.exists(platform_downloads_dir):
                os.makedirs(platform_downloads_dir)

    def _load_history(self):
        """加载历史版本记录"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    self.version_history = json.load(f)
            except Exception as e:
                logger.error(f"加载历史记录失败: {str(e)}")
                self.version_history = {"windows": [], "macos": []}
        else:
            self.version_history = {"windows": [], "macos": []}

    def _save_history(self):
        """保存历史版本记录"""
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.version_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存历史记录失败: {str(e)}")

    def _update_history(self, platform: str, version_info: Dict):
        """更新历史版本记录"""
        platform_key = platform.lower()
        if platform_key not in self.version_history:
            self.version_history[platform_key] = []
        
        # 检查是否已存在该版本
        for record in self.version_history[platform_key]:
            if record.get("version") == version_info.get("version"):
                # 更新现有记录
                record.update(version_info)
                break
        else:
            # 添加新记录
            self.version_history[platform_key].append(version_info)
        
        # 按更新时间排序
        self.version_history[platform_key].sort(
            key=lambda x: x.get("update_time", ""),
            reverse=True
        )
        
        self._save_history()

    def _generate_filename(self, platform: str, version: str, build_number: Optional[str] = None, 
                          release_date: Optional[str] = None) -> str:
        """生成标准化的文件名"""
        if platform.lower() == "windows":
            if release_date:
                return f"WPS_Office_{version}_{release_date}.exe"
            return f"WPS_Office_{version}.exe"
        elif platform.lower() == "macos":
            if release_date:
                return f"WPS_Office_{version}_{build_number}_{release_date}.zip"
            return f"WPS_Office_{version}_{build_number}.zip"
        return ""

    def _calculate_file_hash(self, file_path: str) -> str:
        """计算文件的 SHA256 哈希值"""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _handle_route(self, route: Route, request: Request):
        """处理网络请求，捕获下载链接"""
        if any(ext in request.url for ext in ['.exe', '.zip']):
            self.captured_urls.add(request.url)
        route.continue_()

    def _get_windows_version(self) -> Dict:
        """获取 Windows 版本信息"""
        try:
            # 尝试获取最新版本
            latest_version = None
            download_url = None
            downloaded_file = None
            release_date = None
            
            # 检查本地版本信息
            local_version_file = os.path.join(self.versions_dir, "windows", "windows.yaml")
            local_info = None
            if os.path.exists(local_version_file):
                try:
                    with open(local_version_file, 'r', encoding='utf-8') as f:
                        local_info = yaml.safe_load(f)
                        if local_info and "version" in local_info:
                            logger.info(f"本地已存在版本: {local_info['version']}")
                except Exception as e:
                    logger.warning(f"读取本地版本信息失败: {str(e)}")
            
            # 从360软件宝库获取最新版本号
            logger.info("从360软件宝库获取WPS最新版本信息")
            try:
                headers = self.platform_headers["Windows"]
                response = requests.get("https://baoku.360.cn/soft/show/appid/104693057", headers=headers, timeout=10)
                if response.status_code == 200:
                    # 使用正则表达式匹配版本号
                    version_pattern = r'版本\s+(\d+\.\d+\.\d+\.(\d+))'
                    match = re.search(version_pattern, response.text)
                    if match:
                        full_version = match.group(1)  # 完整版本号，如 12.1.0.21915
                        latest_version = match.group(2)  # 提取版本号后缀，如 21915
                        logger.info(f"从360软件宝库获取到WPS版本: {full_version}, 提取版本号: {latest_version}")
                        
                        # 优先尝试64位版本，再尝试32位版本
                        bit_versions = ["X64_", ""]
                        for bit_prefix in bit_versions:
                            test_url = f"{self.windows_download_base_url}/WPS_Setup_{bit_prefix}{latest_version}.exe"
                            if self._verify_download_url(test_url):
                                download_url = test_url
                                logger.info(f"验证下载链接成功: {download_url}")
                                break
                    else:
                        logger.warning("未在360软件宝库页面找到WPS版本信息")
            except Exception as e:
                logger.error(f"从360软件宝库获取版本信息失败: {str(e)}")
            
            # 如果从360软件宝库未获取到版本，尝试从中文官网获取
            if not latest_version:
                logger.info("从360软件宝库未获取到版本，尝试从中文官网获取")
                try:
                    with sync_playwright() as p:
                        browser = p.chromium.launch(headless=True)
                        context = browser.new_context(
                            viewport={'width': 1920, 'height': 1080},
                            user_agent=self.platform_headers["Windows"]["User-Agent"]
                        )
                        
                        page = context.new_page()
                        # 减少超时时间
                        page.set_default_timeout(15000)
                        page.set_default_navigation_timeout(30000)
                        
                        # 设置请求拦截
                        self.captured_urls.clear()
                        page.route("**/*", self._handle_route)
                        
                        # 访问中文官网
                        response = page.goto("https://www.wps.cn/", wait_until="networkidle")
                        if response and response.status == 200:
                            # 等待页面加载
                            page.wait_for_load_state("domcontentloaded")
                            page.wait_for_load_state("networkidle")
                            
                            # 尝试点击下载按钮
                            try:
                                download_button = page.locator("text=立即下载").first
                                if download_button:
                                    logger.info("找到下载按钮，点击下载")
                                    download_button.click()
                                    page.wait_for_timeout(2000)  # 等待下载链接生成
                                    
                                    # 从捕获的URL中查找下载链接
                                    for url in self.captured_urls:
                                        if url.endswith('.exe') and 'wpscdn.cn' in url:
                                            download_url = url
                                            logger.info(f"找到下载链接: {url}")
                                            
                                            # 尝试从URL提取版本号
                                            version_match = re.search(r'WPS_Setup_(\d+)', url)
                                            if version_match:
                                                latest_version = version_match.group(1)
                                                logger.info(f"从下载链接提取版本号: {latest_version}")
                                            break
                            except Exception as e:
                                logger.warning(f"点击下载按钮失败: {str(e)}")
                        
                        browser.close()
                except Exception as e:
                    logger.warning(f"从中文官网获取版本失败: {str(e)}")
            
            # 如果仍未获取到版本，使用已知的最新版本作为备选
            if not latest_version:
                logger.info("未能获取最新版本，使用已知的最新版本作为备选")
                latest_version = "21915"  # 已知的最新版本
                download_url = f"{self.windows_download_base_url}/WPS_Setup_{latest_version}.exe"
                
                # 验证下载链接是否有效
                if not self._verify_download_url(download_url):
                    # 尝试64位版本
                    download_url = f"{self.windows_download_base_url}/WPS_Setup_X64_{latest_version}.exe"
                    if not self._verify_download_url(download_url):
                        logger.error("备选版本下载链接无效")
                        return {
                            "platform": "Windows",
                            "version": "Unknown",
                            "download_url": None,
                            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "error": "无法获取有效的下载链接"
                        }
                    
                    # 检查版本是否需要更新
                    if local_info and local_info.get("version") == latest_version:
                        logger.info(f"Windows 版本 {latest_version} 已是最新，跳过下载")
                        return local_info
                    
                    # 下载文件
                    filename = self._generate_filename("Windows", latest_version, release_date=release_date)
                    save_path = os.path.join(self.downloads_dir, "windows", filename)
                    
                    logger.info(f"发现新版本 {latest_version}，开始下载文件: {filename}")
                    if self.downloader.download_file(url_64, save_path):
                        logger.info(f"文件下载完成: {filename}")
                        downloaded_file = save_path
                elif self._verify_download_url(url_32):
                    latest_version = str(version)
                    download_url = url_32
                    
                    # 检查版本是否需要更新
                    if local_info and local_info.get("version") == latest_version:
                        logger.info(f"Windows 版本 {latest_version} 已是最新，跳过下载")
                        return local_info
                    
                    # 下载文件
                    filename = self._generate_filename("Windows", latest_version, release_date=release_date)
                    save_path = os.path.join(self.downloads_dir, "windows", filename)
                    
                    logger.info(f"发现新版本 {latest_version}，开始下载文件: {filename}")
                    if self.downloader.download_file(url_32, save_path):
                        logger.info(f"文件下载完成: {filename}")
                        downloaded_file = save_path
            
            result = {
                "platform": "Windows",
                "version": latest_version or "Unknown",
                "download_url": download_url,
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
            if release_date:
                result["release_date"] = release_date
            
            if downloaded_file:
                result["local_file"] = downloaded_file
                result["file_hash"] = self._calculate_file_hash(downloaded_file)
            
            # 更新历史记录
            self._update_history("Windows", result)
            
            return result
        except Exception as e:
            logger.error(f"获取 Windows 版本信息失败: {str(e)}")
            return {
                "platform": "Windows",
                "version": "Unknown",
                "download_url": None,
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "error": str(e)
            }

    def _get_macos_version(self) -> Dict:
        """获取 macOS 版本信息"""
        max_retries = 3
        retry_count = 0
        
        # 检查本地版本信息
        local_version_file = os.path.join(self.versions_dir, "macos", "macos.yaml")
        local_info = None
        if os.path.exists(local_version_file):
            try:
                with open(local_version_file, 'r', encoding='utf-8') as f:
                    local_info = yaml.safe_load(f)
                    if local_info and "version" in local_info:
                        logger.info(f"本地已存在版本: {local_info['version']}")
            except Exception as e:
                logger.warning(f"读取本地版本信息失败: {str(e)}")
        
        while retry_count < max_retries:
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        timeout=60000,
                        headless=True
                    )
                    context = browser.new_context(
                        viewport={'width': 1920, 'height': 1080},
                        user_agent=self.platform_headers["macOS"]["User-Agent"]
                    )
                    
                    page = context.new_page()
                    # 减少超时时间，避免资源浪费
                    page.set_default_timeout(15000)
                    page.set_default_navigation_timeout(30000)
                    
                    # 设置请求拦截
                    self.captured_urls.clear()
                    page.route("**/*", self._handle_route)
                    
                    logger.info(f"尝试获取 macOS 版本信息 (第 {retry_count + 1} 次)")
                    
                    # 访问页面
                    response = page.goto(self.mac_url, wait_until="networkidle")
                    if not response:
                        raise Exception("页面加载失败")
                    
                    if response.status != 200:
                        raise Exception(f"页面返回状态码: {response.status}")
                    
                    # 等待页面加载完成
                    page.wait_for_load_state("domcontentloaded")
                    page.wait_for_load_state("networkidle")
                    
                    # 获取版本信息
                    logger.info("正在查找版本信息...")
                    
                    # 尝试多种方式获取版本信息
                    version_text = None
                    release_date = None
                    selectors = [
                        # 匹配当前格式 "12.1.21861/2025.06.20"
                        "text=/\\d+\\.\\d+\\.\\d+\\/\\d+\\.\\d+\\.\\d+/",
                        # 匹配旧格式 "7.5.1(8994)"
                        "text=/\\d+\\.\\d+\\.\\d+\\(\\d+\\)/",
                        # 通用选择器
                        "//div[contains(text(), '/20')]",
                        "//span[contains(text(), '/20')]",
                        "//div[contains(@class, 'version')]",
                        "//span[contains(text(), '版本')]",
                        "//div[contains(@class, 'download')]//span[contains(text(), '.')]"
                    ]
                    
                    for selector in selectors:
                        try:
                            element = page.locator(selector).first
                            if element:
                                version_text = element.text_content().strip()
                                logger.info(f"使用选择器 {selector} 找到版本信息: {version_text}")
                                
                                # 尝试从版本信息中提取日期
                                date_match = re.search(r'[/\s](\d{4}\.\d{2}\.\d{2})', version_text)
                                if date_match:
                                    release_date = date_match.group(1).replace('.', '-')
                                    logger.info(f"从版本信息中提取到发布日期: {release_date}")
                                break
                        except Exception as e:
                            logger.warning(f"使用选择器 {selector} 查找版本信息失败: {str(e)}")
                            continue
                    
                    if version_text:
                        # 尝试多种版本号格式
                        # 尝试匹配 "12.1.21861/2025.06.20" 格式
                        version_match = re.search(r'(\d+\.\d+\.\d+)\/(\d+\.\d+\.\d+)', version_text)
                        if version_match:
                            version = version_match.group(1)
                            release_date = version_match.group(2).replace('.', '-')
                            build_number = "0"  # 如果没有构建号，使用0
                            logger.info(f"解析到版本号: {version}, 发布日期: {release_date}")
                        else:
                            # 尝试匹配 "7.5.1(8994)" 格式
                            version_match = re.search(r'(\d+\.\d+\.\d+)\((\d+)\)', version_text)
                            if version_match:
                                version = version_match.group(1)
                                build_number = version_match.group(2)
                                logger.info(f"解析到版本号: {version}, 构建号: {build_number}")
                            else:
                                # 尝试匹配任何版本号格式
                                version_match = re.search(r'(\d+\.\d+\.\d+)', version_text)
                                if version_match:
                                    version = version_match.group(1)
                                    build_number = "0"  # 如果没有构建号，使用0
                                    logger.info(f"解析到版本号: {version}")
                                else:
                                    raise Exception(f"无法解析版本号: {version_text}")
                        
                        # 检查版本是否需要更新
                        if local_info and local_info.get("version") == version and local_info.get("build_number") == build_number:
                            logger.info(f"macOS 版本 {version}({build_number}) 已是最新，跳过下载")
                            browser.close()
                            return local_info
                        
                        # 获取下载链接
                        logger.info("正在查找下载按钮...")
                        download_button = None
                        button_selectors = [
                            "text=立即下载",
                            "//a[contains(@class, 'download')]",
                            "//button[contains(text(), '下载')]",
                            "//div[contains(@class, 'download')]//button"
                        ]
                        
                        for selector in button_selectors:
                            try:
                                button = page.locator(selector).first
                                if button:
                                    download_button = button
                                    logger.info(f"找到下载按钮: {selector}")
                                    break
                            except Exception as e:
                                logger.warning(f"使用选择器 {selector} 查找下载按钮失败: {str(e)}")
                                continue
                        
                        if download_button:
                            # 点击下载按钮，触发下载链接生成
                            logger.info("点击下载按钮...")
                            download_button.click()
                            page.wait_for_timeout(2000)
                            
                                                            # 从捕获的URL中查找下载链接
                            download_url = None
                            for url in self.captured_urls:
                                # 选择完整的安装包链接，而不是目录链接
                                if url.endswith('.zip') and 'wpscdn.cn' in url:
                                    download_url = url
                                    logger.info(f"找到下载链接: {url}")
                                    break
                            
                            if not download_url:
                                # 如果没有找到完整的安装包链接，尝试使用目录链接
                                for url in self.captured_urls:
                                    if url.endswith('/') and 'wpscdn.cn' in url:
                                        # 构建完整的下载链接
                                        download_url = f"{url.rstrip('/')}/WPS_Office_Installer.zip"
                                        logger.info(f"构建下载链接: {download_url}")
                                        break
                            
                            if download_url:
                                # 下载文件
                                filename = self._generate_filename("macOS", version, build_number, release_date)
                                save_path = os.path.join(self.downloads_dir, "macos", filename)
                                
                                logger.info(f"发现新版本 {version}({build_number})，开始下载文件: {filename}")
                                if self.downloader.download_file(download_url, save_path):
                                    logger.info(f"文件下载完成: {filename}")
                                    downloaded = True
                                else:
                                    logger.error(f"文件下载失败: {filename}")
                                    downloaded = False
                                
                                result = {
                                    "platform": "macOS",
                                    "version": version,
                                    "build_number": build_number,
                                    "download_url": download_url,
                                    "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                }
                                
                                if release_date:
                                    result["release_date"] = release_date
                                
                                if downloaded:
                                    result["local_file"] = save_path
                                    result["file_hash"] = self._calculate_file_hash(save_path)
                                
                                # 更新历史记录
                                self._update_history("macOS", result)
                                
                                browser.close()
                                return result
                            else:
                                logger.warning("未找到下载链接")
                        else:
                            logger.warning("未找到下载按钮")
                    else:
                        logger.warning("未找到版本信息")
                    
                    browser.close()
                    retry_count += 1
                    if retry_count < max_retries:
                        logger.info(f"将在 5 秒后重试...")
                        time.sleep(5)
                        continue
                    
                    return {
                        "platform": "macOS",
                        "version": "Unknown",
                        "download_url": None,
                        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "error": "无法获取版本信息或下载链接"
                    }
                    
            except Exception as e:
                logger.error(f"获取 macOS 版本信息失败 (第 {retry_count + 1} 次): {str(e)}")
                retry_count += 1
                if retry_count < max_retries:
                    logger.info(f"将在 5 秒后重试...")
                    time.sleep(5)
                    continue
                
                return {
                    "platform": "macOS",
                    "version": "Unknown",
                    "download_url": None,
                    "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "error": str(e)
                }

    def _verify_download_url(self, url: str) -> bool:
        """验证下载链接是否有效"""
        try:
            response = requests.head(url, allow_redirects=True, timeout=5)
            return response.status_code == 200
        except:
            return False

    def save_version_info(self, platform: str, data: Dict):
        """保存版本信息到 YAML 文件"""
        platform_key = platform.lower()
        filename = os.path.join(self.versions_dir, platform_key, f"{platform_key}.yaml")
        with open(filename, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)
        logger.info(f"已保存 {platform} 版本信息到 {filename}")

    def crawl_all_versions(self):
        """抓取所有平台的版本信息"""
        platforms = {
            "Windows": self._get_windows_version,
            "macOS": self._get_macos_version
        }

        for platform, crawler_func in platforms.items():
            try:
                logger.info(f"开始抓取 {platform} 版本信息...")
                version_info = crawler_func()
                # 确保版本信息有效再保存
                if version_info and "version" in version_info and version_info["version"] != "Unknown":
                    self.save_version_info(platform, version_info)
                else:
                    logger.error(f"{platform} 版本信息无效或获取失败，跳过保存")
            except Exception as e:
                logger.error(f"抓取 {platform} 版本信息时发生错误: {str(e)}")

def main():
    crawler = WPSVersionCrawler()
    crawler.crawl_all_versions()

if __name__ == "__main__":
    main() 