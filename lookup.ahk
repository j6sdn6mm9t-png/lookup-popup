; 侧键查词：按一下设定的按键（默认鼠标前进侧键）查光标下的单词，弹出仿 macOS 词典的释义窗口
; 释义窗口由 lookup.pyw 提供（本脚本会在需要时自动启动它）
;
; 更改按键：右键托盘图标 → "更改查词按键…"（或用 /setkey 参数启动本脚本），
; 设置保存在同目录的 config.ini 里。
#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent

CONFIG := A_ScriptDir "\config.ini"
DEFAULT_KEY := "XButton2"
SERVER := "http://127.0.0.1:47213"
POPUP := "LookUp Popup"
STARTUP_LINK := A_Startup "\LookUp Popup.lnk"
; 免安装版自带 python\ 文件夹；从源码安装的用 setup.ps1 建的 .venv
PYTHONW := FileExist(A_ScriptDir "\python\pythonw.exe")
    ? A_ScriptDir "\python\pythonw.exe"
    : A_ScriptDir "\.venv\Scripts\pythonw.exe"

if !FileExist(PYTHONW) {
    MsgBox "没有找到 Python 运行环境，请先在本文件夹运行 setup.ps1。", "查词", "Icon!"
    ExitApp
}

lookupKey := ""
SetLookupKey(IniRead(CONFIG, "LookUp", "Key", DEFAULT_KEY))

A_TrayMenu.Delete()
A_TrayMenu.Add("更改查词按键…", (*) => ChangeKey())
A_TrayMenu.Add("开机自动启动", (*) => ToggleStartup())
A_TrayMenu.Add("打开所在文件夹", (*) => Run(A_ScriptDir))
A_TrayMenu.Add()
A_TrayMenu.Add("退出", (*) => ExitApp())
A_TrayMenu.Default := "更改查词按键…"   ; 双击托盘图标也能打开
if FileExist(STARTUP_LINK)
    A_TrayMenu.Check("开机自动启动")

if (A_Args.Length && A_Args[1] = "/setkey")
    ChangeKey()

EnsureServer()

LookUp(*) {
    if !EnsureServer()
        return
    if (Http("/lookup") != "miss")
        return
    ; 这个程序不支持直接读取文字时：双击选中单词并复制，查完恢复剪贴板。
    ; 先等组合键里的 Ctrl/Alt/Shift/Win 松开，否则双击和 Ctrl+C 会带上这些修饰键
    for mod in ["Ctrl", "Alt", "Shift", "LWin", "RWin"]
        KeyWait mod, "T1"
    saved := ClipboardAll()
    A_Clipboard := ""
    Click 2
    Send "^c"
    if ClipWait(0.5)
        Http("/lookup?word=" UriEncode(SubStr(Trim(A_Clipboard), 1, 60)))
    A_Clipboard := saved
}

; 弹窗显示时：点击别处或按 Esc 关闭
#HotIf WinExist(POPUP)
~LButton::
~RButton::
~MButton:: {
    MouseGetPos , , &win
    if (WinGetTitle("ahk_id " win) != POPUP)
        Http("/hide")
}
~Esc::Http("/hide")
#HotIf

; ---------------------------------------------------------------- 查词按键

SetLookupKey(key) {
    global lookupKey
    HotIf()
    try {
        Hotkey key, LookUp, "On"
    } catch as err {
        MsgBox "无法使用按键 " key "：" err.Message "`n已改回默认的鼠标前进侧键。", "查词", "Icon!"
        key := DEFAULT_KEY
        Hotkey key, LookUp, "On"
    }
    if (lookupKey != "" && lookupKey != key) {
        Hotkey lookupKey, "Off"
        SideButtonPassthrough(lookupKey, "Off")
    }
    lookupKey := key
    SideButtonPassthrough(key, "On")
    A_IconTip := "侧键查词（" KeyName(key) "）"
}

; 侧键用来查词后，原来的前进/后退功能改用 Shift + 侧键
SideButtonPassthrough(key, state) {
    HotIf()
    if (key = "XButton1")
        Hotkey "+XButton1", (*) => Send("{Browser_Back}"), state
    else if (key = "XButton2")
        Hotkey "+XButton2", (*) => Send("{Browser_Forward}"), state
}

; AutoHotkey 的按键写法（如 "^!d"、"XButton2"）转成给人看的名字
KeyName(key) {
    static mice := Map("XButton1", "鼠标后退侧键", "XButton2", "鼠标前进侧键", "MButton", "鼠标中键")
    static mods := Map("^", "Ctrl+", "!", "Alt+", "+", "Shift+", "#", "Win+")
    name := ""
    while (key != "" && mods.Has(SubStr(key, 1, 1)) && StrLen(key) > 1) {
        name .= mods[SubStr(key, 1, 1)]
        key := SubStr(key, 2)
    }
    return name (mice.Has(key) ? mice[key] : StrUpper(SubStr(key, 1, 1)) SubStr(key, 2))
}

; 弹出窗口，等用户按下新的按键（鼠标侧键/中键，或键盘组合键）
ChangeKey() {
    static busy := false
    if busy
        return
    busy := true

    ; 采集期间先停用当前的查词按键，免得按下去就触发查词
    HotIf()
    Hotkey lookupKey, "Off"
    SideButtonPassthrough(lookupKey, "Off")

    g := Gui("+AlwaysOnTop -MinimizeBox", "更改查词按键")
    g.SetFont("s10", "Microsoft YaHei UI")
    g.Add("Text", "w340", "当前按键：" KeyName(lookupKey))
    g.Add("Text", "w340 y+10",
        "请按下新的按键：`n"
        "  · 鼠标侧键或中键（可以同时按住 Ctrl / Alt / Shift）`n"
        "  · 或键盘组合键，例如 Ctrl+Alt+D、F8")
    hint := g.Add("Text", "w340 y+12 cGray", "按 Esc 取消")
    ih := InputHook("L0")
    g.OnEvent("Close", (*) => ih.Stop())
    g.Show()

    mouseKey := ""
    OnMouse(button, *) {
        mods := (GetKeyState("Ctrl") ? "^" : "") (GetKeyState("Alt") ? "!" : "")
              . (GetKeyState("Shift") ? "+" : "") (GetKeyState("LWin") || GetKeyState("RWin") ? "#" : "")
        mouseKey := mods button
        ih.Stop()
    }
    buttons := ["XButton1", "XButton2", "MButton"]
    HotIf()
    for b in buttons
        Hotkey "*" b, OnMouse.Bind(b), "On"

    newKey := ""
    loop {
        mouseKey := ""
        ih := InputHook("L0")
        ih.KeyOpt("{All}", "ES")                 ; 任意键结束采集，并且不传给其他程序
        modKeys := "{LCtrl}{RCtrl}{LAlt}{RAlt}{LShift}{RShift}{LWin}{RWin}"
        ih.KeyOpt(modKeys, "-E")                 ; 单按修饰键不算，等组合键
        ih.KeyOpt(modKeys, "-S")
        ih.Start()
        ih.Wait()

        if (mouseKey != "") {
            newKey := mouseKey
            break
        }
        if (ih.EndReason != "EndKey" || (ih.EndKey = "Escape" && ih.EndMods = ""))
            break                                ; 关闭窗口或按了 Esc：取消
        mods := (InStr(ih.EndMods, "^") ? "^" : "") (InStr(ih.EndMods, "!") ? "!" : "")
              . (InStr(ih.EndMods, "+") ? "+" : "") (InStr(ih.EndMods, "#") ? "#" : "")
        key := ih.EndKey
        ; 单独的字母、数字、空格等会影响正常打字，必须配 Ctrl / Alt / Win
        standalone := "i)^(F\d{1,2}|Pause|ScrollLock|AppsKey|Browser_\w+|Media_\w+|Launch_\w+)$"
        if (!RegExMatch(mods, "[\^!#]") && !RegExMatch(key, standalone)) {
            hint.Opt("cRed")
            hint.Value := KeyName(mods key) " 会影响正常打字，请配合 Ctrl / Alt / Win 使用，或换一个键"
            continue
        }
        newKey := mods key
        break
    }

    for b in buttons
        Hotkey "*" b, "Off"
    g.Destroy()

    if (newKey != "") {
        IniWrite newKey, CONFIG, "LookUp", "Key"
        SetLookupKey(newKey)
        TrayTip "查词按键已改为 " KeyName(lookupKey), "侧键查词"
    } else {
        SetLookupKey(lookupKey)                  ; 取消：恢复原来的按键
    }
    busy := false
}

; 在"启动"文件夹里放（或删掉）一个指向本脚本的快捷方式。
; A_AhkPath 是正在运行的 AutoHotkey：免安装版里就是 LookUp.exe
ToggleStartup() {
    if FileExist(STARTUP_LINK) {
        FileDelete STARTUP_LINK
        A_TrayMenu.Uncheck("开机自动启动")
    } else {
        FileCreateShortcut A_AhkPath, STARTUP_LINK, A_ScriptDir, '"' A_ScriptFullPath '"', "LookUp Popup 查词"
        A_TrayMenu.Check("开机自动启动")
    }
}

; ---------------------------------------------------------------- 查词服务

EnsureServer() {
    if (Http("/ping", 300) = "pong")
        return true
    Run '"' PYTHONW '" "' A_ScriptDir '\lookup.pyw"', A_ScriptDir
    loop 40 {
        Sleep 250
        if (Http("/ping", 300) = "pong")
            return true
    }
    TrayTip "查词服务没有启动，详情见 lookup.log", "侧键查词"
    return false
}

Http(path, timeoutMs := 3000) {
    try {
        req := ComObject("WinHttp.WinHttpRequest.5.1")
        req.SetTimeouts(timeoutMs, timeoutMs, timeoutMs, timeoutMs)
        req.Open("GET", SERVER path, false)
        req.Send()
        return req.ResponseText
    } catch
        return ""
}

UriEncode(str) {
    buf := Buffer(StrPut(str, "UTF-8"))
    StrPut(str, buf, "UTF-8")
    out := ""
    loop buf.Size - 1 {
        b := NumGet(buf, A_Index - 1, "UChar")
        out .= (b >= 0x30 && b <= 0x39) || (b >= 0x41 && b <= 0x5A) || (b >= 0x61 && b <= 0x7A)
            ? Chr(b) : Format("%{:02X}", b)
    }
    return out
}
