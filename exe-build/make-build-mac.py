#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JIZURA macOS 构建脚本（JIZURA.app，Apple Silicon）
====================================================
必须在 macOS 上运行 —— PyInstaller 不能跨平台编译，Windows 上请用 make-build.py。

用法（任意目录都可执行）：
    python3 exe-build/make-build-mac.py                 # 构建 app + 组装发行目录 + 打包 zip
    python3 exe-build/make-build-mac.py --only app      # 只重建 app（recognizer/converter 已构建时）
    python3 exe-build/make-build-mac.py --no-zip        # 不打包 zip
    python3 exe-build/make-build-mac.py --dmg           # 另生成 dmg

产物：
    exe-build/dist-mac/JIZURA.app                  原生应用（窗口模式）
    exe-build/dist-mac/JIZURA-mac/                 JIZURA.app + models/ + ffmpeg/ + 使用说明.txt
    <仓库上一级>/JIZURA-mac-<日期>.zip              完整发行包（ditto 打包，保留 .app 权限）

依赖：Python 3.12 / 3.13（推荐 3.12）。
构建工具（PyInstaller / Pillow / pywebview / pyobjc）优先从
exe-build/build-tools/wheels-mac-arm64 离线安装；识曲依赖优先从
plugins/song-lyrics/local-recognizer/wheels-mac-arm64 离线安装；缺什么自动联网装。
"""
import argparse
import datetime
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BUILD = HERE / 'build-mac'
OUT = BUILD / 'out'
DIST = HERE / 'dist-mac'
VENV = HERE / '.build-venv-mac'
TOOLS_WHEELS = HERE / 'build-tools' / 'wheels-mac-arm64'

RECOG_DIR = ROOT / 'plugins' / 'song-lyrics' / 'local-recognizer'
RECOG_WHEELS = RECOG_DIR / 'wheels-mac-arm64'
RECOG_REQ = RECOG_DIR / 'requirements.txt'
RECOG_SRC = RECOG_DIR / 'recognizer.py'

FFTOOLS = ROOT / 'plugins' / 'song-lyrics' / 'ffmpeg-tools'
CONV_SRC = FFTOOLS / 'converter.py'
FFMPEG_BIN = FFTOOLS / 'ffmpeg'

PAGES = ['index.html', 'en', 'zh-hans', 'zh-hant', 'ko', 'id', 'vi']
FOLDER_NAME = 'JIZURA-mac'

USAGE_TXT = """JIZURA 离线版 · macOS 使用说明
================================

【一、直接用（推荐）】
1. 把整个「JIZURA-mac」文件夹解压到任意位置（比如「应用程序」或桌面）。
   注意：请保持 JIZURA.app、models、ffmpeg 三者在同一层，不要只单独拖走 app。
2. 双击 JIZURA.app：在独立应用窗口里打开 JIZURA（无浏览器界面）。
   - 首次打开若提示「无法验证开发者」：右键 JIZURA.app → 打开 → 再点「打开」；
     或在终端执行：xattr -dr com.apple.quarantine JIZURA.app
3. Whisper 转写：models 已内置，完全离线可用。
4. FFmpeg 转换：ffmpeg 已内置（Apple Silicon 版），全程离线。
5. 识曲（Shazam）与在线歌词（LRCLIB）需要联网，联网时自动可用。

【二、跑源码版（开发/调试，可选）】
  终端执行：python3 plugins/song-lyrics/serve.py
  或双击仓库根目录的「启动JIZURA-离线版.command」。

【三、自己重新构建 JIZURA.app】
  需要 macOS + Python 3.12/3.13（建议 3.12）：
      python3 exe-build/make-build-mac.py
  详见 exe-build/README-BUILD.md。

说明：本应用未做苹果签名/公证，首次打开需要上面第 2 步的放行操作，属正常现象。
"""


def log(msg):
    print('[build-mac] %s' % msg, flush=True)


def run(cmd, check=True):
    log('> ' + ' '.join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd])
    if check and r.returncode != 0:
        raise SystemExit('命令失败（退出码 %d）' % r.returncode)
    return r.returncode


def has_module(py, mod):
    return subprocess.run([str(py), '-c', 'import ' + mod],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def pip_install(py, pkgs, wheels_dir=None, extra=None):
    args = [py, '-m', 'pip', 'install', '-q', '--disable-pip-version-check']
    if wheels_dir and Path(wheels_dir).is_dir():
        args += ['--no-index', '--find-links', str(wheels_dir)]
    args += list(extra or []) + list(pkgs)
    return run(args, check=False)


def ensure_venv():
    py = VENV / 'bin' / 'python'
    if not py.exists():
        log('创建构建用虚拟环境：%s' % VENV)
        run([sys.executable, '-m', 'venv', str(VENV)])

    need_tools = (not has_module(py, 'PyInstaller')) or (not has_module(py, 'PIL')) \
        or (not has_module(py, 'webview'))
    if need_tools:
        log('安装构建工具（优先离线 wheels-mac-arm64）…')
        pip_install(py, ['setuptools'], TOOLS_WHEELS)
        rc = pip_install(py, ['pyinstaller', 'pillow', 'pywebview'], TOOLS_WHEELS,
                         extra=['--no-build-isolation'])
        if rc != 0:
            log('离线安装构建工具失败，尝试联网安装…')
            run([py, '-m', 'pip', 'install', '-q', '--disable-pip-version-check',
                 'pyinstaller', 'pillow', 'pywebview'])

    if not has_module(py, 'shazamio'):
        log('安装识曲依赖（优先离线 wheels-mac-arm64）…')
        rc = pip_install(py, ['-r', str(RECOG_REQ)], RECOG_WHEELS)
        if rc != 0:
            log('离线安装识曲依赖失败，尝试联网安装…')
            run([py, '-m', 'pip', 'install', '-q', '--disable-pip-version-check', '-r', str(RECOG_REQ)])
    return py


def gen_icns(py):
    """用 make-icon.py 画 1024 PNG，再经 sips + iconutil 生成 .icns（macOS 自带工具）。"""
    icns = BUILD / 'icon.icns'
    script = HERE / 'make-icon.py'
    if not script.is_file() or not has_module(py, 'PIL'):
        return None
    png = BUILD / 'icon-1024.png'
    log('生成图标与启动画面…')
    run([py, str(script), str(BUILD / 'icon.ico'), str(BUILD / 'splash.png'), str(png)],
        check=False)
    if not png.exists():
        return None
    if not shutil.which('iconutil') or not shutil.which('sips'):
        log('未找到 iconutil / sips，app 将使用默认图标。')
        return None
    iconset = BUILD / 'JIZURA.iconset'
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            name = 'icon_%dx%d%s.png' % (size, size, '@2x' if scale == 2 else '')
            if run(['sips', '-z', str(px), str(px), str(png), '--out', str(iconset / name)],
                   check=False) != 0:
                return None
    if run(['iconutil', '-c', 'icns', str(iconset), '-o', str(icns)], check=False) != 0:
        return None
    log('图标：%s' % icns)
    return icns


def stage_web():
    web = BUILD / 'web-lite'
    if web.exists():
        shutil.rmtree(web)
    web.mkdir(parents=True)
    for rel in PAGES:
        src = ROOT / rel
        if src.is_dir():
            shutil.copytree(src, web / rel)
        elif src.is_file():
            shutil.copy2(src, web / rel)
    libs = ROOT / 'libs'
    if libs.is_dir():
        shutil.copytree(libs, web / 'libs')
    log('暂存网页：%s' % web)
    return web


def pyi(py, name, script, extra=None, windowed=False, icon=None, bundle_id=None):
    work = BUILD / 'tmp' / name
    work.mkdir(parents=True, exist_ok=True)
    (BUILD / 'spec').mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    cmd = [py, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
           '--log-level', 'WARN', '--name', name, '--distpath', OUT,
           '--workpath', work, '--specpath', BUILD / 'spec']
    if windowed:
        cmd.append('--windowed')
    if icon:
        cmd += ['--icon', str(icon)]
    if bundle_id:
        cmd += ['--osx-bundle-identifier', bundle_id]
    cmd += [str(x) for x in (extra or [])]
    cmd.append(str(script))
    log('PyInstaller 构建 %s …' % name)
    run(cmd)
    out = OUT / (name + '.app') if windowed else OUT / name
    if not out.exists():
        raise SystemExit('未找到产物：%s' % out)
    log('完成：%s' % out)
    return out


def codesign(path):
    """ad-hoc 签名（Apple Silicon 上未签名的二进制无法运行）。"""
    if not shutil.which('codesign'):
        log('未找到 codesign，跳过签名（可能无法在 Apple Silicon 上启动）。')
        return
    log('ad-hoc 签名：%s' % Path(path).name)
    run(['codesign', '--force', '--deep', '--sign', '-', str(path)], check=False)


def assemble(app):
    folder = DIST / FOLDER_NAME
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    log('组装发行目录：%s' % folder)
    shutil.copytree(app, folder / 'JIZURA.app', symlinks=True)

    models = ROOT / 'models'
    if models.is_dir():
        shutil.copytree(models, folder / 'models')
    else:
        log('警告：models/ 不存在，发行包将不含本地 Whisper 模型（可先跑 get-models.py）。')

    ff = folder / 'ffmpeg'
    ff.mkdir()
    for nm in ('ffmpeg', 'VERSION-mac.txt', '许可说明-mac.txt',
               'darwin-arm64.LICENSE', 'darwin-arm64.README'):
        p = FFMPEG_BIN / nm
        if p.is_file():
            shutil.copy2(p, ff / nm)
    if (ff / 'ffmpeg').exists():
        try:
            os.chmod(ff / 'ffmpeg', 0o755)
        except OSError:
            pass
    else:
        log('警告：未找到 mac 版 ffmpeg（%s/ffmpeg）。'
            '可先运行 get-ffmpeg.py --os mac --arch arm64 下载。' % FFMPEG_BIN)

    (folder / '使用说明.txt').write_text(USAGE_TXT, encoding='utf-8')
    codesign(folder / 'JIZURA.app')
    return folder


def zip_folder(folder):
    out = ROOT.parent / ('JIZURA-mac-%s.zip' % datetime.date.today().isoformat())
    if out.exists():
        out.unlink()
    log('打包 zip（ditto，保留 .app 权限与符号链接）…')
    run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(folder), str(out)])
    log('完成：%s（%.1f MB）' % (out, out.stat().st_size / 1048576.0))
    return out


def main():
    ap = argparse.ArgumentParser(description='构建 macOS 版 JIZURA.app')
    ap.add_argument('--only', default='',
                    help='只跑指定步骤（逗号分隔：recognizer,converter,app,assemble,zip）')
    ap.add_argument('--no-zip', action='store_true', help='不打包 zip')
    ap.add_argument('--dmg', action='store_true', help='另生成 dmg（需要 hdiutil）')
    a = ap.parse_args()

    if sys.platform != 'darwin':
        raise SystemExit(
            '本脚本必须在 macOS 上运行（PyInstaller 不能跨平台编译）。\n'
            'Windows 上请用 exe-build/make-build.py 构建 exe；\n'
            'macOS 上如果没有 Python，可先用源码离线版：双击「启动JIZURA-离线版.command」。')
    if sys.version_info < (3, 10):
        raise SystemExit('需要 Python 3.10+（推荐 3.12）。')
    if sys.version_info[:2] not in ((3, 12), (3, 13)):
        log('提示：内置离线 wheel 只覆盖 Python 3.12 / 3.13；当前 %d.%d 将尝试联网安装依赖。'
            % sys.version_info[:2])

    for need in (ROOT / 'zh-hans' / 'index.html', ROOT / 'libs', RECOG_SRC, CONV_SRC):
        if not need.exists():
            raise SystemExit('缺少构建素材：%s' % need)

    steps = [s.strip() for s in a.only.split(',') if s.strip()] or \
        ['recognizer', 'converter', 'app', 'assemble', 'zip']
    BUILD.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    py = ensure_venv()
    icon = gen_icns(py)

    rec = OUT / 'JIZURA-Recognizer'
    conv = OUT / 'JIZURA-FFmpeg'
    app = OUT / 'JIZURA.app'

    if 'recognizer' in steps:
        rec = pyi(py, 'JIZURA-Recognizer', RECOG_SRC,
                  extra=['--collect-all', 'shazamio', '--collect-all', 'aiohttp_retry',
                         '--hidden-import', 'shazamio_core'])
    if 'converter' in steps:
        conv = pyi(py, 'JIZURA-FFmpeg', CONV_SRC)
    if 'app' in steps:
        for need, nm in ((rec, 'recognizer'), (conv, 'converter')):
            if not need.exists():
                raise SystemExit('请先构建 %s 步骤（缺少 %s）' % (nm, need))
        web_lite = stage_web()
        app = pyi(py, 'JIZURA', HERE / 'launcher.py',
                  extra=['--add-data', '%s:web' % web_lite,
                         '--add-binary', '%s:.' % rec,
                         '--add-binary', '%s:.' % conv,
                         '--collect-all', 'webview',
                         '--hidden-import', 'webview.platforms.cocoa'],
                  windowed=True, icon=icon, bundle_id='com.jizura.offline')

    folder = DIST / FOLDER_NAME
    if 'assemble' in steps:
        if not app.exists():
            raise SystemExit('请先构建 app 步骤（缺少 %s）' % app)
        folder = assemble(app)

    if 'zip' in steps and not a.no_zip:
        if not folder.exists():
            raise SystemExit('请先 assemble（缺少 %s）' % folder)
        out = zip_folder(folder)
        if a.dmg and shutil.which('hdiutil'):
            dmg = out.with_suffix('.dmg')
            log('生成 dmg：%s' % dmg)
            run(['hdiutil', 'create', '-volname', 'JIZURA', '-srcfolder', str(folder),
                 '-ov', '-format', 'UDZO', str(dmg)], check=False)

    log('全部完成，用时 %.0f 秒。产物在：%s' % (time.time() - t0, DIST))


if __name__ == '__main__':
    main()
