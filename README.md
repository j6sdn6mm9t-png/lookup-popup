# LookUp Popup · Windows 上的 macOS 式查词

把鼠标停在任意英文单词上，按一下鼠标侧键，就会弹出一个仿 macOS「查询」（Look Up）的释义面板：

- **English**：英英释义，带音节划分、音标、用法标签、例句和词源（Wiktionary）
- **English Thesaurus**：同义词（Open English WordNet）
- **Simplified Chinese – English**：英汉释义（ECDICT）

每个分区默认只预览一条，点 **more** 展开。面板带有液态玻璃效果：从光标处的单词弹性展开，背景磨砂模糊，跟随系统深色/浅色模式。所有词典数据都在本地，离线可用。

## 系统要求

- Windows 11（Windows 10 应该也能用，但没有测试过）
- [Python](https://www.python.org/) 3.10–3.13
- [AutoHotkey](https://www.autohotkey.com/) v2
- Microsoft Edge WebView2 运行时（Windows 11 自带）

## 安装

1. 安装 Python 和 AutoHotkey（已经装过的跳过）：

   ```powershell
   winget install Python.Python.3.13
   winget install AutoHotkey.AutoHotkey
   ```

2. 下载本仓库（`git clone` 或下载 ZIP 解压），在文件夹里运行：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Startup
   ```

   脚本会创建 Python 虚拟环境、安装依赖、下载约 700MB 的词典原始数据并生成本地词典（约 220MB，需要 10–20 分钟），完成后自动启动。`-Startup` 表示同时设为开机自启，不需要可以去掉。下载中断的话重新运行即可，已下载的文件会跳过。

## 使用

| 操作 | 效果 |
|---|---|
| 鼠标停在单词上，按**前进侧键** | 弹出释义 |
| 点 **more** | 展开该分区的全部内容 |
| 点击面板外 / 按 `Esc` | 关闭 |
| **Shift + 前进侧键** | 原来的"前进"功能 |

优先通过 UI Automation 直接读取光标下的文字，不会改动选区；不支持的程序会自动改用"双击选中 + 复制"，查完恢复剪贴板。

**换按键**：编辑 `lookup.ahk` 开头的 `LOOKUP_KEY`，例如 `"XButton1"`（后退侧键）或 `"^!d"`（Ctrl+Alt+D），保存后双击 `lookup.ahk` 重新加载。

## 字体

界面会优先使用 SF Pro，没有安装时用 Segoe UI；词条正文用 Windows 自带的 Palatino Linotype 和思源宋体（Noto Serif SC）。本仓库不附带任何苹果字体。

## 工作原理

- `lookup.ahk`：监听按键，调用本地查词服务；必要时自动启动它
- `lookup.pyw`：查词服务（只监听 `127.0.0.1`）。用 UI Automation 取词，查本地 SQLite 词典，用 pywebview（WebView2）显示弹窗
- `ui/popup.html`：弹窗的排版和动画。Windows 不能让 WebView 窗口真正透明，所以弹出前先截下窗口将要覆盖的屏幕区域作为背景，在此基础上做模糊和展开动画
- `build_db.py`：下载词典原始数据并生成 `data/dict.db`

已知限制：玻璃背景是弹出那一刻的截图，背后如果在播放视频，弹窗打开期间玻璃里的画面不会跟着动。

## 卸载

退出托盘里的 AutoHotkey 图标，删除本文件夹；如果设了开机自启，再删除 `shell:startup` 里的 `LookUp Popup` 快捷方式。

## 许可

代码以 [MIT](LICENSE) 许可发布。词典数据在安装时从原始来源下载，不包含在本仓库中，使用时请遵守各自的许可：

- [ECDICT](https://github.com/skywind3000/ECDICT)：MIT
- [Wiktionary](https://en.wiktionary.org/)（经 [kaikki.org](https://kaikki.org/) 提取）：CC BY-SA 4.0 / GFDL
- [Open English WordNet](https://en-word.net/)：CC BY 4.0
