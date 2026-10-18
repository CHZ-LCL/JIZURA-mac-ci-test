#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JIZURA 桌面启动器（exe 版）
===========================
双击运行：在本机起一个网页服务，并在**独立应用窗口**（Windows 用 WebView2；macOS 用
WKWebView，由 pywebview 自动选择）里打开 JIZURA。如果窗口不可用，会自动退回默认浏览器
打开（功能完全一致）。

- 绿色版（PyInstaller onefile）：页面资源内嵌在可执行文件里；若程序旁边有 models/ 文件夹，
  会自动把 /models/ 的请求映射过去，从而启用本地 Whisper 模型（完全离线）。
- 安装版：安装目录里若有 web/ 文件夹（含页面 + libs + models），直接以它为网站根目录。
- macOS：JIZURA.app 与 models/、ffmpeg/ 并排放置即可（即放在 .app 的上一层目录）。
- 若内嵌/同目录存在 JIZURA-Recognizer(.exe) 且 8787 端口空闲，会自动带起识曲服务。
- 若内嵌/同目录存在 JIZURA-FFmpeg(.exe) 且 8788 端口空闲，会自动带起 FFmpeg 转换服务；
  ffmpeg 会到程序旁边或工具目录的 ffmpeg/ 子目录里找。

用法：
  JIZURA [--port 8080] [--page zh-hans] [--browser] [--no-ui] [--no-recognizer] [--no-converter]
"""
import argparse
import functools
import os
import posixpath
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FROZEN = getattr(sys, 'frozen', False)
IS_WIN = os.name == 'nt'
IS_MAC = sys.platform == 'darwin'
EXE_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
MEI = Path(getattr(sys, '_MEIPASS', EXE_DIR))
REPO = Path(__file__).resolve().parents[1] if not FROZEN else None
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
SUF = '.exe' if IS_WIN else ''


def _bundle_parent():
    """macOS：JIZURA.app/Contents/MacOS/JIZURA → 返回 .app 所在目录（models/、ffmpeg/ 放这里）。"""
    if not (IS_MAC and FROZEN):
        return None
    try:
        for up in Path(sys.executable).resolve().parents:
            if up.suffix.lower() == '.app':
                return up.parent
    except Exception:
        pass
    return None


BUNDLE_PARENT = _bundle_parent()

# 网站根目录：优先程序旁边的 web/（安装版），否则用内嵌的（绿色版）；开发模式回落到仓库根
_CANDS = [EXE_DIR / 'web', MEI / 'web']
if REPO is not None:
    _CANDS += [REPO / 'web', REPO]
WEBROOT = next((p for p in _CANDS if p.is_dir()), None)
if WEBROOT is None:
    print('[错误] 找不到页面资源（web/），文件可能不完整。')
    try:
        input('按回车退出…')
    except EOFError:
        pass
    sys.exit(1)

# 绿色版：程序旁边的 models/ 作为外部模型目录（映射 /models/ 请求）
if (WEBROOT / 'models').is_dir():
    EXT_MODELS = None
else:
    _EXT_CANDS = [EXE_DIR / 'models']
    if BUNDLE_PARENT is not None:
        _EXT_CANDS.insert(0, BUNDLE_PARENT / 'models')
    EXT_MODELS = next((p for p in _EXT_CANDS if p.is_dir()), None)

# 识曲工具（内嵌或同目录）
RECOG = next((p for p in (MEI / ('JIZURA-Recognizer' + SUF),
                          EXE_DIR / ('JIZURA-Recognizer' + SUF),
                          EXE_DIR / 'recognizer' / ('JIZURA-Recognizer' + SUF)) if p.is_file()), None)

# FFmpeg 转换工具（内嵌或同目录；开发模式用仓库里的脚本）
CONV = next((p for p in (MEI / ('JIZURA-FFmpeg' + SUF),
                         EXE_DIR / ('JIZURA-FFmpeg' + SUF),
                         EXE_DIR / 'tools' / ('JIZURA-FFmpeg' + SUF)) if p.is_file()), None)
CONV_DEV = (REPO / 'plugins' / 'song-lyrics' / 'ffmpeg-tools' / 'converter.py') if REPO else None

# ffmpeg 所在目录：程序旁边 / tools 下 / 内嵌包内（macOS 再查 .app 的上一层）
_FF_CANDS = [EXE_DIR / 'ffmpeg', EXE_DIR / 'tools' / 'ffmpeg', MEI / 'ffmpeg']
if BUNDLE_PARENT is not None:
    _FF_CANDS.insert(0, BUNDLE_PARENT / 'ffmpeg')
FFDIR = next((p for p in _FF_CANDS if p.is_dir()), None)


def close_splash():
    try:
        import pyi_splash  # PyInstaller 启动画面模块（未启用时不存在）
        pyi_splash.close()
    except Exception:
        pass


def hide_console():
    """把本进程的控制台窗口隐藏（窗口模式）。仅当控制台确实属于本程序时才隐藏，
    避免用户从已有的 cmd/PowerShell 里启动时把对方的终端一并隐藏。macOS 无控制台窗口，直接跳过。"""
    if not IS_WIN:
        return
    if not console_owned_by_us():
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)   # SW_HIDE
    except Exception:
        pass


def show_console():
    if not IS_WIN:
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 5)   # SW_SHOW
    except Exception:
        pass


def console_owned_by_us():
    """控制台里的进程是否只有本程序（PyInstaller 单文件的父/子进程算同一个程序）。"""
    if not IS_WIN:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        buf = (wintypes.DWORD * 32)()
        n = k32.GetConsoleProcessList(buf, 32)
        if n <= 0:
            return False
        me = os.getpid()
        myname = Path(sys.executable).name.lower()
        for i in range(n):
            pid = int(buf[i])
            if pid == me:
                continue
            h = k32.OpenProcess(0x1000, False, pid)    # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            try:
                sbuf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if not k32.QueryFullProcessImageNameW(h, 0, sbuf, ctypes.byref(size)):
                    return False
                if Path(sbuf.value).name.lower() != myname:
                    return False
            finally:
                k32.CloseHandle(h)
        return True
    except Exception:
        return False


def kill_tree(p):
    """结束子进程（PyInstaller 单文件是父+子结构，必须杀整棵树）。"""
    if p is None:
        return
    if IS_WIN:
        try:
            subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'], creationflags=NO_WINDOW,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        return
    # POSIX：子进程用独立进程组启动，整组一起结束（onefile 的父/子进程都会退出）
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGTERM)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def translate_path(self, path):
        if EXT_MODELS is not None:
            q = urllib.parse.urlsplit(path).path
            if q.startswith('/models/'):
                rel = posixpath.normpath(urllib.parse.unquote(q))[len('/models/'):]
                parts = [p for p in rel.split('/') if p and p not in ('.', '..')]
                if parts:
                    return str(EXT_MODELS.joinpath(*parts))
        return super().translate_path(path)

    def log_message(self, *args):
        pass  # 安静模式；排错时把这行删掉


def free_port(start):
    for port in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    return 0


def port_busy(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(('127.0.0.1', port)) == 0


def storage_dir():
    if IS_WIN:
        base = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
        d = Path(base) / 'JIZURA' / 'webview'
    elif IS_MAC:
        d = Path.home() / 'Library' / 'Application Support' / 'JIZURA' / 'webview'
    else:
        d = Path.home() / '.jizura' / 'webview'
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def spawn_recognizer(args):
    if not RECOG or args.no_recognizer:
        return None
    if port_busy(8787):
        print('识曲服务已在运行（127.0.0.1:8787），直接使用。')
        return None
    try:
        p = subprocess.Popen([str(RECOG), '8787'], creationflags=NO_WINDOW,
                             start_new_session=not IS_WIN)
        print('识曲服务已随主程序启动（127.0.0.1:8787）。')
        return p
    except Exception as e:
        print('识曲服务启动失败（不影响其他功能）：%s' % e)
        return None


def spawn_converter(args):
    if args.no_converter:
        return None
    if port_busy(8788):
        print('FFmpeg 转换服务已在运行（127.0.0.1:8788），直接使用。')
        return None
    cmd = None
    if CONV:
        cmd = [str(CONV), '--port', '8788']
    elif CONV_DEV and CONV_DEV.is_file() and not FROZEN:
        cmd = [sys.executable, str(CONV_DEV), '--port', '8788']
    if not cmd:
        return None
    if FFDIR is not None:
        cmd += ['--ffmpeg', str(FFDIR)]
    try:
        p = subprocess.Popen(cmd, creationflags=NO_WINDOW, start_new_session=not IS_WIN)
        print('FFmpeg 转换服务已随主程序启动（127.0.0.1:8788）。')
        return p
    except Exception as e:
        print('FFmpeg 转换服务启动失败（不影响其他功能）：%s' % e)
        return None


def start_window(url):
    """在独立应用窗口里打开（Windows: WebView2 / macOS: WKWebView）；
    失败返回 False（调用方退回浏览器）。"""
    try:
        import webview
    except Exception as e:
        print('未启用应用窗口（pywebview 不可用：%s），改用浏览器。' % e)
        return False
    try:
        win = webview.create_window('JIZURA 字面', url, width=1560, height=940,
                                    min_size=(1024, 640))

        def _settle():
            close_splash()
            hide_console()

        # 双重保险：loaded 事件（不同 WebView2 版本触发时机不稳）+ 2 秒定时器
        try:
            win.events.loaded += _settle
        except Exception:
            pass
        threading.Timer(2.0, _settle).start()

        try:
            webview.start(private_mode=False, storage_path=str(storage_dir()), debug=False)
        except Exception:
            show_console()      # 退回浏览器模式时把控制台放回来，方便看日志
            raise
        return True
    except Exception as e:
        print('应用窗口启动失败（%s），改用浏览器。' % e)
        return False


def main():
    ap = argparse.ArgumentParser(description='JIZURA 桌面启动器')
    ap.add_argument('--port', type=int, default=8080, help='起始端口（默认 8080，被占用则自动顺延）')
    ap.add_argument('--page', default='zh-hans', help='默认语言页（zh-hans/en/zh-hant/ko/id/vi）')
    ap.add_argument('--browser', action='store_true', help='用默认浏览器打开（不用应用窗口）')
    ap.add_argument('--no-ui', action='store_true', help='不打开任何界面（只跑服务）')
    ap.add_argument('--no-browser', action='store_true', help=argparse.SUPPRESS)   # 兼容旧参数
    ap.add_argument('--no-recognizer', action='store_true', help='不自动带起识曲服务')
    ap.add_argument('--no-converter', action='store_true', help='不自动带起 FFmpeg 转换服务')
    args = ap.parse_args()
    no_ui = args.no_ui or args.no_browser

    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW('JIZURA 离线版')
        except Exception:
            pass

    port = free_port(args.port)
    if not port:
        print('[错误] 找不到可用端口（%d 起 30 个都被占用）。' % args.port)
        try:
            input('按回车退出…')
        except EOFError:
            pass
        sys.exit(1)

    handler = functools.partial(Handler, directory=str(WEBROOT))
    server = ThreadingHTTPServer(('127.0.0.1', port), handler)
    page = (args.page.strip('/') + '/index.html') if args.page else 'index.html'
    url = 'http://127.0.0.1:%d/%s' % (port, page)

    rec = spawn_recognizer(args)
    conv = spawn_converter(args)

    mode = '安装版' if WEBROOT == EXE_DIR / 'web' else ('绿色版（页面内嵌）' if FROZEN else '开发模式（仓库）')
    print('=' * 58)
    print('JIZURA 已启动：%s' % url)
    print('运行模式：%s' % mode)
    if EXT_MODELS is not None:
        print('已挂载外置模型目录：%s' % EXT_MODELS)
    if FFDIR is not None:
        print('FFmpeg 目录：%s' % FFDIR)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        if no_ui:
            print('未打开界面（--no-ui）；按 Ctrl+C 停止。')
            print('=' * 58)
            close_splash()
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                pass
        else:
            opened = False
            if not args.browser:
                print('正在打开应用窗口（无浏览器界面）…')
                print('=' * 58)
                opened = start_window(url)
            if not opened:
                print('已在默认浏览器打开；保持本窗口开着，按 Ctrl+C 停止。')
                print('=' * 58)
                close_splash()
                webbrowser.open(url)
                try:
                    while True:
                        time.sleep(3600)
                except KeyboardInterrupt:
                    pass
    finally:
        print('\n已停止。')
        server.shutdown()
        server.server_close()
        kill_tree(rec)
        kill_tree(conv)


if __name__ == '__main__':
    main()
