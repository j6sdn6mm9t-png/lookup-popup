"""查词服务：常驻后台，收到 AutoHotkey 的请求后取光标下的单词并弹出仿 macOS 的释义窗口。
由 lookup.ahk 用 .venv 里的 pythonw 自动启动，一般不需要手动运行。

接口（只监听 127.0.0.1）：
  /lookup            取光标下的单词并显示；取不到词返回 "miss"，由 AHK 改用双击复制的方式
  /lookup?word=xxx   直接查给定的词
  /hide              隐藏弹窗
  /ping              存活检查
"""
import ctypes
import json
import logging
import re
import sqlite3
import struct
import threading
import time
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent

user32 = ctypes.windll.user32
# 必须在创建任何窗口前设置：按每个显示器的真实 DPI 工作，坐标全部用物理像素
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

import uiautomation as auto  # noqa: E402
import webview  # noqa: E402

PORT = 47213
TITLE = "LookUp Popup"
WIDTH = 400          # 面板宽度（逻辑像素）
MAX_HEIGHT = 520     # 面板最大高度，超过就在面板内滚动
MARGIN = 24          # 面板四周留给阴影的边距，显示的是截图，看起来是透明的（与 popup.html 一致）

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[RotatingFileHandler(ROOT / "lookup.log", maxBytes=1_000_000,
                                                  backupCount=1, encoding="utf-8")])
log = logging.getLogger("lookup")

# ---------------------------------------------------------------- 词典

EXCHANGE_NAMES = {"p": "过去式", "d": "过去分词", "i": "现在分词", "3": "第三人称单数",
                  "r": "比较级", "t": "最高级", "s": "复数"}
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*[A-Za-z]|[A-Za-z]")


class Dictionary:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()

    def _ecdict(self, word):
        with self.lock:
            rows = self.db.execute(
                "SELECT word, phonetic, translation, collins, tag, exchange FROM dict WHERE sw = ?",
                (word.lower(),)).fetchall()
        # 同一个小写形式可能有多条（如 polish / Polish），优先大小写完全一致的
        rows.sort(key=lambda r: r[0] != word)
        return rows[0] if rows else None

    def _json(self, table, word):
        with self.lock:
            try:
                row = self.db.execute(f"SELECT data FROM {table} WHERE sw = ?", (word.lower(),)).fetchone()
            except sqlite3.OperationalError:     # 表不存在（例如还没构建 Wiktionary）
                return None
        return json.loads(row[0]) if row else None

    def _english(self, word):
        """英英释义：优先 Wiktionary（含音标、词源），没有再用 WordNet。"""
        wk = self._json("wiktionary", word)
        if wk:
            return wk
        wn = self._json("wordnet", word)
        return {"en": wn} if wn else None

    def _candidates(self, word):
        """原词查不到时，按常见词尾规则猜原形。"""
        w = word.lower()
        rules = [("ies", "y"), ("ied", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"),
                 ("ing", ""), ("ing", "e"), ("er", ""), ("est", ""), ("ly", "")]
        out = []
        for suf, rep in rules:
            if w.endswith(suf) and len(w) - len(suf) >= 2:
                out.append(w[: -len(suf)] + rep)
        # running -> run, stopped -> stop
        if re.search(r"(\w)\1(ing|ed)$", w):
            out.append(re.sub(r"(\w)\1(ing|ed)$", r"\1", w))
        return out

    def lookup(self, raw):
        m = WORD_RE.search(raw.replace("’", "'"))
        if not m:
            return None
        word = re.sub(r"'s$", "", m.group(0))

        ec, en = self._ecdict(word), self._english(word)
        if not ec and not en:
            for cand in self._candidates(word):
                ec, en = self._ecdict(cand), self._english(cand)
                if ec or en:
                    word = cand
                    break

        entry = {"word": ec[0] if ec else word, "query": raw.strip()}
        if not ec and not en:
            entry["notFound"] = True
            return entry

        if ec:
            _, phonetic, translation, _, _, exchange = ec
            entry["phonetic"] = phonetic or ""
            entry["zh"] = [line for line in (translation or "").split("\\n") if line.strip()]
            ex = dict(part.split(":", 1) for part in (exchange or "").split("/") if ":" in part)
            lemma = ex.get("0")
            if lemma and lemma.lower() != entry["word"].lower():
                kinds = "、".join(EXCHANGE_NAMES[k] for k in ex.get("1", "") if k in EXCHANGE_NAMES)
                entry["lemma"] = {"word": lemma, "kind": kinds or "变形"}
                # 变形词（ran、dogs）的 Wiktionary 条目多是冷僻义项，直接用原形的释义；
                # 但 WordNet 里也收录为独立词的（running、rose）保留它自己的释义
                if not en or not self._json("wordnet", word):
                    en = self._english(lemma) or en
                    entry["enFrom"] = lemma
                    # 中文区也换成原形的（ran 自己的中文只有"run的过去式"）
                    lec = self._ecdict(lemma)
                    if lec:
                        entry["zhWord"], entry["phonetic"] = lec[0], lec[1] or ""
                        entry["zh"] = [l for l in (lec[2] or "").split("\\n") if l.strip()]
        en = en or {}
        head = entry.get("enFrom") or entry["word"]   # 英英、同义词分区显示的词头（ran 显示 run）
        entry["en"] = en.get("en") or []
        entry["head"] = en.get("hyph") or head      # 带音节点，如 hap·py
        entry["ipa"] = en.get("ipa") or ("" if entry.get("enFrom") else entry.get("phonetic", ""))
        if en.get("origin"):
            entry["origin"] = en["origin"]
        entry["thesaurus"] = self._synonyms(head)
        return entry

    def _synonyms(self, word):
        """同义词去掉只是把原词重复一遍的（English → "English language"），去完为空的义项和词性也去掉。"""
        own = re.compile(rf"\b{re.escape(word)}\b", re.I)
        groups = []
        for g in self._json("thesaurus", word) or []:
            senses = []
            for s in g["senses"]:
                syns = [w for w in s["s"] if not own.search(w)]
                if syns:
                    senses.append({**s, "s": syns})
            if senses:
                groups.append({**g, "senses": senses})
        return groups


# ---------------------------------------------------------------- 取词

def _grow_to_word(rng):
    """补齐单词边界。网页把一个词拆进多个标签时（如 <span>P</span>rospectus），
    Chrome 给出的单词会少掉开头或结尾的字母（"rospectus"、"Summar"），
    所以逐个字符向两边看，相邻的还是字母就并进来，直到碰到空格或标点。"""
    for endpoint, step in ((auto.TextPatternRangeEndpoint.Start, -1),
                           (auto.TextPatternRangeEndpoint.End, 1)):
        for _ in range(30):
            text = rng.GetText(100)
            edge = text[:1] if step < 0 else text[-1:]
            if not edge.isalpha():              # 这一侧已经是空格或标点，说明边界本来就对
                break
            probe = rng.Clone()
            if probe.MoveEndpointByUnit(endpoint, auto.TextUnit.Character, step, waitTime=0) == 0:
                break                           # 到了文档开头或结尾
            grown = probe.GetText(100)
            added = grown[:1] if step < 0 else grown[-1:]
            if len(grown) != len(text) + 1 or not added.isalpha():
                break
            rng.MoveEndpointByUnit(endpoint, auto.TextUnit.Character, step, waitTime=0)


_S, _E, _CH = auto.TextPatternRangeEndpoint.Start, auto.TextPatternRangeEndpoint.End, auto.TextUnit.Character


def _char_rect(rng, i):
    """文字块里第 i 个字符的屏幕矩形。"""
    c = rng.Clone()
    c.MoveEndpointByRange(_E, c, _S, waitTime=0)        # 折叠到开头
    c.MoveEndpointByUnit(_E, _CH, i + 1, waitTime=0)
    c.MoveEndpointByUnit(_S, _CH, i, waitTime=0)
    rects = c.GetBoundingRectangles()
    return rects[0] if rects else None


def _char_index_at(rng, n, x, y):
    """二分查找光标下是第几个字符（文字按从上到下、从左到右排列）。"""
    lo, hi = 0, n - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        r = _char_rect(rng, mid)
        if r is None:
            return None
        if y < r.top or (r.top <= y <= r.bottom and x < r.left):
            hi = mid - 1
        elif y > r.bottom or x >= r.right:
            lo = mid + 1
        else:
            return mid
    return None


def _text_chunk_at(ctrl, x, y):
    """找到光标下的文字块（TextControl）。
    Chrome 对一个位置的第一次点击测试常常只返回整个文档，第二次才返回具体的文字块，
    所以第一次没找到就再测一次。"""
    return _find_chunk(ctrl, x, y) or _find_chunk(auto.ControlFromPoint(x, y), x, y)


def _find_chunk(ctrl, x, y):
    """点击测试命中的是文字块就直接用；命中整个文档时，先用文档的 RangeFromPoint
    落到光标处的字符（或代表嵌入块的 "￼"），再从它所在的元素往下找包含光标的文字块。"""
    if ctrl is None:
        return None
    if ctrl.ControlTypeName == "TextControl" and ctrl.Name:
        return ctrl
    tp = ctrl.GetPattern(auto.PatternId.TextPattern)
    if not tp:
        return None
    r = tp.RangeFromPoint(x, y)
    if not r:
        return None
    r.ExpandToEnclosingUnit(auto.TextUnit.Character, waitTime=0)
    roots = r.GetChildren() or []
    enclosing = r.textRange.GetEnclosingElement()
    if enclosing:
        roots.append(auto.Control.CreateControlFromElement(enclosing))
    for root in roots:
        if root is None or auto.ControlsAreSame(root, ctrl):
            continue                                    # 别把整个文档再遍历一遍
        for c, _ in auto.WalkControl(root, includeTop=True, maxDepth=6):
            b = c.BoundingRectangle
            if c.ControlTypeName == "TextControl" and c.Name and b.left <= x < b.right and b.top <= y < b.bottom:
                return c
    return None


def _chunk_range(tp, ctrl):
    # uiautomation 的 RangeFromChild 封装有 bug，直接调底层接口
    raw = tp.pattern.RangeFromChild(ctrl.Element)
    return auto.TextRange(raw) if raw else None


def _adjacent_letters(tp, ctrl, rng, text, side, depth=0):
    """单词碰到文字块边缘时，取紧挨着的相邻块（同一行、左右相接）里接上的字母。"""
    edge = _char_rect(rng, 0 if side < 0 else len(text) - 1)
    if edge is None or depth > 2:
        return ""
    ymid = (edge.top + edge.bottom) // 2
    px = edge.left - 3 if side < 0 else edge.right + 2
    other = _text_chunk_at(auto.ControlFromPoint(px, ymid), px, ymid)
    if other is None or auto.ControlsAreSame(other, ctrl):
        return ""
    orng = _chunk_range(tp, other)
    otext = orng.GetText(-1) if orng else ""
    if not otext:
        return ""
    oedge = _char_rect(orng, len(otext) - 1 if side < 0 else 0)
    if oedge is None or not (oedge.top < ymid < oedge.bottom):
        return ""
    gap = edge.left - oedge.right if side < 0 else oedge.left - edge.right
    if gap > 3:                                         # 中间有空隙，说明是两个词
        return ""
    if side < 0:
        m = re.search(r"[A-Za-z]*$", otext).group(0)
        more = _adjacent_letters(tp, other, orng, otext, side, depth + 1) if m == otext else ""
        return more + m
    m = re.match(r"[A-Za-z]*", otext).group(0)
    more = _adjacent_letters(tp, other, orng, otext, side, depth + 1) if m == otext else ""
    return m + more


def _word_from_text_chunk(ctrl, x, y):
    """直接读光标下文字块的内容和字符位置来定单词。
    有些网页（如 SEC 的申报文件）把每行切成很多绝对定位的小块，单词正好被切开
    （"P" | "rospectus Summar" | "y"）；Chrome 在这种页面上取词只给出 "￼"，
    双击选词也只选到半个词。所以自己算光标在哪个字符上，碰到块边缘就去拼相邻块。"""
    tp, anc = None, ctrl
    for _ in range(30):
        anc = anc.GetParentControl()
        if anc is None:
            return None
        tp = anc.GetPattern(auto.PatternId.TextPattern)
        if tp:
            break
    if not tp:
        return None
    rng = _chunk_range(tp, ctrl)
    text = rng.GetText(-1) if rng else ""
    if not text:
        return None
    i = _char_index_at(rng, len(text), x, y)
    if i is None or not text[i].isalpha():
        return None
    s, e = i, i + 1
    while s > 0 and text[s - 1].isalpha():
        s -= 1
    while e < len(text) and text[e].isalpha():
        e += 1
    word = text[s:e]
    if s == 0:
        word = _adjacent_letters(tp, ctrl, rng, text, -1) + word
    if e == len(text):
        word += _adjacent_letters(tp, ctrl, rng, text, 1)
    return word


def _word_at_uia(x, y, result):
    try:
        with auto.UIAutomationInitializerInThread():
            ctrl = auto.ControlFromPoint(x, y)
            # 网页里的文字（Chrome/Edge）：直接按文字块和字符位置来定单词
            chunk = _text_chunk_at(ctrl, x, y)
            if chunk is not None:
                word = _word_from_text_chunk(chunk, x, y)
                if word and WORD_RE.fullmatch(word):
                    result.append(word)
                    return
            for _ in range(8):
                if ctrl is None:
                    return
                tp = ctrl.GetPattern(auto.PatternId.TextPattern)
                if tp:
                    rng = tp.RangeFromPoint(x, y)
                    if not rng:
                        return
                    rng.ExpandToEnclosingUnit(auto.TextUnit.Word, waitTime=0)
                    _grow_to_word(rng)
                    text = rng.GetText(80)
                    rects = rng.GetBoundingRectangles()
                    # 光标没落在这个词上（比如指着空白处）就算取词失败
                    if rects and not any(r.left - 4 <= x <= r.right + 4 and r.top - 4 <= y <= r.bottom + 4
                                         for r in rects):
                        log.info("uia: cursor outside word %r", text)
                        return
                    m = WORD_RE.fullmatch(text.strip(" \t\r\n.,;:!?\"()[]{}<>“”‘’"))
                    if m:
                        result.append(m.group(0))
                    else:
                        log.info("uia: not a word %r", text[:40])
                    return
                ctrl = ctrl.GetParentControl()
    except Exception:
        log.exception("uia failed")


def word_at(x, y, timeout=1.5):
    """用 UI Automation 读光标下的单词；有的程序会卡住，所以放到线程里限时。
    Chrome 在页面第一次被读取时才建无障碍信息，可能要将近一秒，时限不能太短。"""
    result = []
    t = threading.Thread(target=_word_at_uia, args=(x, y, result), daemon=True)
    t.start()
    t.join(timeout)
    return result[0] if result else None


# ---------------------------------------------------------------- 弹窗

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x10
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4

# 不声明参数类型的话，64 位下 HWND_TOPMOST(-1) 会被截成 32 位，调用直接失败
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
ctypes.windll.dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                                       ctypes.c_void_p, wintypes.DWORD]


def cursor_pos():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def monitor_at(x, y):
    """返回光标所在显示器的工作区（物理像素）和缩放比例。"""
    hmon = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(hmon, ctypes.byref(info))
    dpi_x, dpi_y = ctypes.c_uint(), ctypes.c_uint()
    ctypes.windll.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
    return info.rcWork, dpi_x.value / 96


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


gdi32 = ctypes.windll.gdi32
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]


def capture_bmp(x, y, w, h):
    """截取屏幕上一块区域，作为玻璃效果的"背后画面"。
    用 BMP 而不是 PNG：不用压缩，大区域截图也只要几十毫秒，本机传输体积不是问题。"""
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, w, h, screen, x, y, 0x00CC0020 | 0x40000000)  # SRCCOPY | CAPTUREBLT
    info = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h,
                            biPlanes=1, biBitCount=32)
    px = bytearray(w * h * 4)
    buf = (ctypes.c_char * len(px)).from_buffer(px)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, screen)
    del buf

    px[3::4] = b"\xff" * (w * h)               # 屏幕截图的 alpha 是 0，改成不透明
    header = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(px), 0, 0, 14 + 40)
    info = struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, len(px), 0, 0, 0, 0)  # 负高度 = 自上而下
    return header + info + bytes(px)


DWMWA_NCRENDERING_POLICY, DWMWA_CLOAK, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWA_BORDER_COLOR = 2, 13, 33, 34
GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = -20, 0x80, 0x40000, 0x08000000
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]


def dwm_set(hwnd, attr, value):
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(ctypes.c_uint(value)), 4)


class Popup:
    """玻璃效果的做法：弹出前先截下窗口将要覆盖的屏幕区域，页面用这张图
    当底（第一帧和背后完全一致，看起来"透明"），再在上面做模糊、提亮和展开动画。
    窗口比面板四周各大 MARGIN，用来画阴影；圆角、阴影都由页面绘制，系统的全部关掉。"""

    def __init__(self, dictionary):
        self.dict = dictionary
        self.window = None
        self.hwnd = None
        self.lock = threading.Lock()
        self.backdrop = b""
        self.backdrop_id = 0

    # pywebview 把这些方法暴露给页面里的 JS：window.pywebview.api.xxx
    class Api:
        def __init__(self, popup):
            self._popup = popup

        def open(self, word):
            return self._popup.dict.lookup(word)

        def fit(self, height):
            self._popup.fit(height)

        def hide(self):
            self._popup.hide()

    def attach(self):
        self.window.events.loaded.wait()
        self.hwnd = self.window.native.Handle.ToInt64()
        dwm_set(self.hwnd, DWMWA_NCRENDERING_POLICY, 1)          # 关掉系统阴影
        dwm_set(self.hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, 1)    # 关掉系统圆角
        dwm_set(self.hwnd, DWMWA_BORDER_COLOR, 0xFFFFFFFE)       # 关掉系统描边
        # 标成工具窗口：不在任务栏和 Alt+Tab 里出现（否则每次弹出任务栏都会多一个 Python 图标）；
        # 同时设为点击不激活，点 more 时焦点留在原来的程序里
        ex = user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
        ex = (ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW
        user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, ex)
        log.info("popup ready hwnd=%s", self.hwnd)

    def js(self, code):
        return self.window.evaluate_js(code)

    def js_await(self, code, timeout=2.0):
        """执行返回 Promise 的 JS 并等待结果（pywebview 只在给了回调时才等 Promise）。"""
        done, box = threading.Event(), []

        def callback(result):
            box.append(result)
            done.set()
        self.window.evaluate_js(code, callback)
        done.wait(timeout)
        return box[0] if box else None

    def show(self, entry, anchor):
        if not self.hwnd:
            return
        with self.lock:
            t0 = time.perf_counter()
            if user32.IsWindowVisible(self.hwnd):   # 弹窗开着时又查了新词：先藏起来再截屏
                user32.ShowWindow(self.hwnd, SW_HIDE)
                time.sleep(0.04)

            work, scale = monitor_at(*anchor)
            m = int(MARGIN * scale)
            w = int(WIDTH * scale) + 2 * m
            max_h = int(MAX_HEIGHT * scale) + 2 * m
            # 面板左上角在光标左下方一点；窗口再往外扩 MARGIN
            x = max(work.left, min(anchor[0] - int(24 * scale) - m, work.right - w))
            # 截图区域要盖住窗口可能出现的所有位置（光标下方或上方，以及之后展开变高），
            # 左右再多截一些，免得模糊在边缘处变暗
            extra = int(40 * scale)
            cap_left, cap_right = max(work.left, x - extra), min(work.right, x + w + extra)
            cap_top = max(work.top, anchor[1] - max_h - int(8 * scale))
            cap_bottom = min(work.bottom, anchor[1] + int(16 * scale) + max_h)
            self.backdrop = capture_bmp(cap_left, cap_top, cap_right - cap_left, cap_bottom - cap_top)
            self.backdrop_id += 1
            self._capture = (cap_left, cap_top, scale)
            t1 = time.perf_counter()

            # 窗口隐藏时 WebView2 不重绘，直接显示会先闪一下上次的旧画面。
            # 所以先"隐身"显示（DWM cloak：窗口照常绘制但不上屏），画好新的第一帧再现身
            dwm_set(self.hwnd, DWMWA_CLOAK, 1)
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            # 先把宽度调对再排版：否则第一次查词时窗口还是初始宽度，文字换行不同，量出的高度不准
            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, cap_top, w, max_h, SWP_NOACTIVATE)

            url = f"http://127.0.0.1:{PORT}/backdrop.bmp?{self.backdrop_id}"
            height = self.js_await(f"prepare({json.dumps(entry, ensure_ascii=False)}, {json.dumps(url)}, "
                             f"{(cap_right - cap_left) / scale}, {(cap_bottom - cap_top) / scale})")
            try:
                height = float(height)
            except (TypeError, ValueError):
                log.warning("prepare returned %r", height)
                height = 200
            h = int(min(height, MAX_HEIGHT) * scale) + 2 * m
            y = anchor[1] + int(16 * scale)
            if y + h > work.bottom:            # 下方放不下就放到光标上方
                y = anchor[1] - h - int(8 * scale)
            y = max(work.top, y)
            self._place(x, y, w, h, anchor)

            self.js_await("painted()", timeout=0.3)
            dwm_set(self.hwnd, DWMWA_CLOAK, 0)
            self.js("reveal()")
            log.info("shown in %.0f ms (capture %.0f ms)", (time.perf_counter() - t0) * 1000, (t1 - t0) * 1000)

    def _place(self, x, y, w, h, anchor=None):
        cx, cy, scale = self._capture
        self._rect = (x, y, w, h)
        if anchor:
            self._anchor = ((anchor[0] - x) / scale, (anchor[1] - y) / scale)
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE)
        self.js(f"place({(x - cx) / scale}, {(y - cy) / scale}, {self._anchor[0]}, {self._anchor[1]})")

    def fit(self, height):
        """展开"更多释义"或跳转到原形后，按内容重新调整高度。"""
        if not self.hwnd or not user32.IsWindowVisible(self.hwnd):
            return
        x, y, w, _ = self._rect
        work, scale = monitor_at(x, y)
        h = int(min(height, MAX_HEIGHT) * scale) + 2 * int(MARGIN * scale)
        if y + h > work.bottom:
            y = max(work.top, work.bottom - h)
        self._place(x, y, w, h)

    def hide(self):
        if self.hwnd and user32.IsWindowVisible(self.hwnd):
            self.js("conceal()")
            time.sleep(0.14)                   # 等淡出动画结束
            user32.ShowWindow(self.hwnd, SW_HIDE)


# ---------------------------------------------------------------- HTTP 接口

def make_handler(popup):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, text):
            body = text.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            if url.path == "/ping":
                return self.reply("pong")
            if url.path == "/backdrop.bmp":
                body = popup.backdrop
                self.send_response(200)
                self.send_header("Content-Type", "image/bmp")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            if url.path == "/hide":
                popup.hide()
                return self.reply("ok")
            if url.path == "/lookup":
                pos = cursor_pos()
                word = qs.get("word", [None])[0] or word_at(*pos)
                log.info("lookup %r at %s", word, pos)
                if not word:
                    return self.reply("miss")
                entry = popup.dict.lookup(word)
                if entry is None:
                    return self.reply("miss")
                popup.show(entry, pos)
                return self.reply("ok")
            self.send_error(404)

    return Handler


def main():
    db_path = ROOT / "data" / "dict.db"
    if not db_path.exists():
        user32.MessageBoxW(None, "找不到词典数据 data\\dict.db，请先运行 setup.ps1。", "查词", 0x30)
        return
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), None)
    except OSError:
        log.info("already running")
        return

    popup = Popup(Dictionary(db_path))
    server.RequestHandlerClass = make_handler(popup)
    popup.window = webview.create_window(
        TITLE, url=str(ROOT / "ui" / "popup.html"), js_api=Popup.Api(popup),
        width=WIDTH, height=300, frameless=True, on_top=True, hidden=True,
        focus=False, shadow=False, resizable=False, background_color="#F7F7F8")

    def background():
        popup.attach()
        # 预热：comtypes 第一次使用时要生成 COM 接口代码，耗时较长，不能留到第一次查词
        word_at(*cursor_pos(), timeout=15)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log.info("listening on %s", PORT)

    webview.start(background, private_mode=True)


if __name__ == "__main__":
    main()
