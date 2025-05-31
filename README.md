# WPS Version Tracker

一个用于自动追踪和下载 WPS Office 最新版本的 Python 工具。支持 Windows 和 macOS 平台，提供多线程下载、版本历史记录和自动化更新功能。

## 🌟 功能特性

- **多平台支持**
  - Windows 版本自动检测和下载
  - macOS 版本自动检测和下载
  - 支持 32 位和 64 位 Windows 版本

- **高效下载**
  - 多线程并发下载（默认 16 线程）
  - 支持断点续传
  - 智能分块下载（6MB 块大小）
  - 自动重试机制

- **版本管理**
  - 自动记录版本历史
  - 保存版本详细信息（版本号、构建号、发布日期等）
  - 文件完整性校验（SHA256）
  - 标准化的文件命名

- **自动化功能**
  - GitHub Actions 自动运行
  - 每日自动检查更新
  - 自动提交新版本到仓库
  - 自动生成更新日志

## 📋 系统要求

- Python 3.8 或更高版本
- Windows 10/11 或 macOS 10.15+
- 网络连接（用于下载和检查更新）

## 🚀 快速开始

1. **克隆仓库**
   ```bash
   git clone https://github.com/your-username/wps-version-tracker.git
   cd wps-version-tracker
   ```

2. **安装依赖**
   ```bash
   pip install -r requirements.txt
   ```

3. **运行程序**
   ```bash
   python wps_version_crawler.py
   ```

## 📁 目录结构

```
wps-version-tracker/
├── wps_version_crawler.py    # 主程序
├── requirements.txt          # 依赖列表
├── .github/                  # GitHub 配置
│   └── workflows/           # GitHub Actions 工作流
├── versions/                 # 版本信息存储
│   ├── windows/             # Windows 版本信息
│   └── macos/               # macOS 版本信息
├── downloads/                # 下载文件存储
│   ├── windows/             # Windows 安装包
│   └── macos/               # macOS 安装包
└── logs/                    # 日志文件
```

## ⚙️ 配置说明

### 环境变量

创建 `.env` 文件进行配置：

```env
# 下载设置
DOWNLOAD_THREADS=16          # 下载线程数
CHUNK_SIZE=6291456          # 下载块大小（字节）
MAX_RETRIES=5               # 最大重试次数

# 自动化设置
AUTO_UPDATE=true            # 启用自动更新
UPDATE_INTERVAL=24          # 更新检查间隔（小时）
```

### GitHub Actions 配置

工作流文件位于 `.github/workflows/auto-update.yml`，主要功能：

1. 每日自动运行检查更新
2. 发现新版本时自动下载
3. 自动提交更新到仓库
4. 生成更新日志

## 🔄 自动化流程

1. **定时检查**
   - 每天 UTC 时间 00:00 自动运行
   - 检查 Windows 和 macOS 版本更新

2. **版本检测**
   - 获取最新版本信息
   - 与本地记录比对
   - 发现新版本时触发下载

3. **自动下载**
   - 多线程下载新版本
   - 验证文件完整性
   - 更新版本历史记录

4. **提交更新**
   - 自动提交新版本文件
   - 更新版本信息文件
   - 生成更新日志

## 📝 更新日志

更新日志自动生成在 `CHANGELOG.md` 文件中，包含：
- 版本更新记录
- 更新日期
- 版本号变更
- 文件哈希值

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支
3. 提交更改
4. 推送到分支
5. 创建 Pull Request

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

## 🙏 致谢

- [Playwright](https://playwright.dev/) - 用于网页自动化
- [Requests](https://requests.readthedocs.io/) - HTTP 客户端
- [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) - HTML 解析
- [tqdm](https://tqdm.github.io/) - 进度条显示 