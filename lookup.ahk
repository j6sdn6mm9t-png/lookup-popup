; 侧键查词：按一下鼠标侧键查光标下的单词，弹出仿 macOS 词典的释义窗口
; 释义窗口由 lookup.pyw 提供（本脚本会在需要时自动启动它）
#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent

; ---------------- 设置 ----------------
; 查词键。XButton2 = 鼠标前进侧键，XButton1 = 鼠标后退侧键；
; 也可以用键盘组合，如 "^!d"（Ctrl+Alt+D），写法见 https://www.autohotkey.com/docs/v2/Hotkeys.htm
LOOKUP_KEY := "XButton2"
; --------------------------------------

SERVER := "http://127.0.0.1:47213"
POPUP := "LookUp Popup"
PYTHONW := A_ScriptDir "\.venv\Scripts\pythonw.exe"

if !FileExist(PYTHONW) {
    MsgBox "没有找到 Python 运行环境，请先在本文件夹运行 setup.ps1。", "查词", "Icon!"
    ExitApp
}

A_IconTip := "侧键查词（" LOOKUP_KEY "）"
Hotkey LOOKUP_KEY, LookUp
; 侧键原来的前进/后退功能改用 Shift + 侧键
if (LOOKUP_KEY = "XButton1")
    Hotkey "+XButton1", (*) => Send("{Browser_Back}")
else if (LOOKUP_KEY = "XButton2")
    Hotkey "+XButton2", (*) => Send("{Browser_Forward}")

EnsureServer()

LookUp(*) {
    if !EnsureServer()
        return
    if (Http("/lookup") != "miss")
        return
    ; 这个程序不支持直接读取文字时：双击选中单词并复制，查完恢复剪贴板
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
