# LookUp Popup · Windows 上的 macOS 式查词

![演示：鼠标停在单词上，按一下侧键弹出释义面板](docs/demo.gif)

把鼠标停在任意英文单词上，按一下鼠标侧键，就会弹出一个仿 macOS「查询」（Look Up）的释义面板：

- **English**：英英释义，带音节划分、音标、用法标签、例句和词源（Wiktionary）
- **English Thesaurus**：同义词（Open English WordNet）
- **Simplified Chinese – English**：英汉释义（ECDICT）

每个分区默认只预览一条，点 **more** 展开。面板带有液态玻璃效果：从光标处的单词弹性展开，背景磨砂模糊，跟随系统深色/浅色模式。所有词典数据都在本地，离线可用。

## 下载即用（推荐）

不需要安装 Python 或任何其他软件：

1. 到 [Releases](https://github.com/j6sdn6mm9t-png/lookup-popup/releases/latest) 下载 `LookUp-Popup-v*-win64.zip`（约 90MB，已包含词典数据）。
2. 解压，把 `LookUp-Popup` 文件夹放到一个固定的位置，例如「文档」或 `D:\`。不要放进层级很深的文件夹，路径太长会导致无法启动。
3. 双击 `LookUp.exe`，右下角托盘出现绿色 H 图标即已运行。右键这个图标，可以设置**开机自动启动**和**更改查词按键**。

首次运行如果出现「Windows 已保护你的电脑」，点「更多信息」→「仍要运行」。`LookUp.exe` 就是开源的 [AutoHotkey](https://www.autohotkey.com/)，只是改了名字。

系统要求：Windows 11（Windows 10 应该也能用，但没有测试过；需要 [WebView2 运行时](https://go.microsoft.com/fwlink/p/?LinkId=2124703)，Windows 11 已自带）。

## 从源码安装

适合想修改代码的用户。需要 [Python](https://www.python.org/) 3.10–3.13 和 [AutoHotkey](https://www.autohotkey.com/) v2。

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
| 鼠标停在单词上，按**查词键**（默认是鼠标前进侧键） | 弹出释义 |
| 点 **more** | 展开该分区的全部内容 |
| 点击面板外 / 按 `Esc` | 关闭 |
| **Shift + 侧键** | 侧键原来的前进/后退功能（查词键是侧键时） |

优先通过 UI Automation 直接读取光标下的文字，不会改动选区；不支持的程序会自动改用"双击选中 + 复制"，查完恢复剪贴板。

### 更改查词键

右键（或双击）托盘里的 AutoHotkey 图标 → **更改查词按键…**，在弹出的窗口里直接按下想用的键，立即生效：

- 鼠标侧键（前进 / 后退）或中键，也可以同时按住 Ctrl / Alt / Shift，如 Ctrl + 侧键
- 键盘组合键，如 Ctrl+Alt+D；功能键（F1–F24）可以单独使用
- 单独的字母、数字等会影响打字，需要配合 Ctrl / Alt / Win；鼠标左右键不能用

设置保存在本文件夹的 `config.ini` 里（不会被 `git pull` 覆盖），也可以手动编辑，写法见 [AutoHotkey 按键名](https://www.autohotkey.com/docs/v2/KeyList.htm)：

```ini
[LookUp]
Key=^!d
```

## 字体

界面会优先使用 SF Pro，没有安装时用 Segoe UI；词条正文用 Windows 自带的 Palatino Linotype 和思源宋体（Noto Serif SC）。本仓库不附带任何苹果字体。

## 工作原理

- `lookup.ahk`：监听查词键（可在托盘菜单里更改），调用本地查词服务；必要时自动启动它
- `lookup.pyw`：查词服务（只监听 `127.0.0.1`）。用 UI Automation 取词，查本地 SQLite 词典，用 pywebview（WebView2）显示弹窗
- `ui/popup.html`：弹窗的排版和动画。Windows 不能让 WebView 窗口真正透明，所以弹出前先截下窗口将要覆盖的屏幕区域作为背景，在此基础上做模糊和展开动画
- `build_db.py`：下载词典原始数据并生成 `data/dict.db`
- `tools/build-portable.ps1`：打包免安装版（自带嵌入式 Python、依赖库、AutoHotkey 和词典数据）

已知限制：玻璃背景是弹出那一刻的截图，背后如果在播放视频，弹窗打开期间玻璃里的画面不会跟着动。

## 卸载

如果开了开机自启，先在托盘菜单里取消勾选「开机自动启动」；然后右键托盘图标 → 退出，删除整个文件夹即可。

## 许可

代码以 [MIT](LICENSE) 许可发布。从源码安装时，词典数据在安装过程中从原始来源下载，不包含在本仓库中；免安装版附带的词典数据库包含 Wiktionary 内容，以 CC BY-SA 4.0 许可分发（详见压缩包内的 `THIRD-PARTY-NOTICES.txt`）。各数据来源的许可：

- [ECDICT](https://github.com/skywind3000/ECDICT)：MIT
- [Wiktionary](https://en.wiktionary.org/)（经 [kaikki.org](https://kaikki.org/) 提取）：CC BY-SA 4.0 / GFDL
- [Open English WordNet](https://en-word.net/)：CC BY 4.0
