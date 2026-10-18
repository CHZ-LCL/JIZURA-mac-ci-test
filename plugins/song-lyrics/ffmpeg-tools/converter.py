#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JIZURA FFmpeg 转换工具（本地服务 + 命令行）
============================================
把 JIZURA 导出的视频 / 透明 PNG 序列 / 音频，转换成更多格式：
HEVC、VP9、AV1、ProRes（含 4444 透明）、MOV/MKV/WebM、高质量 GIF、MP3/WAV/FLAC/M4A 等。

用法：
    python converter.py                     # 作为本地服务运行（默认 127.0.0.1:8788）
    python converter.py --port 8788 --ffmpeg 目录   # 指定端口与 ffmpeg 所在目录
    python converter.py convert -i 输入 -o 输出 -p webm-vp9 [-a 音频] [-q 中] [-f 30]

HTTP API（供插件面板调用）：
    GET  /ping                 → 状态、ffmpeg 版本、可用格式列表
    POST /upload?name=xx       → 上传原始字节，返回 {id}
    POST /convert              → JSON {input, audio?, preset, quality, fps} → 转换结果文件
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

VERSION = '1.1.0'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
IS_WIN = os.name == 'nt'
FFMPEG_NAME = 'ffmpeg.exe' if IS_WIN else 'ffmpeg'

# ---------------------------------------------------------------- ffmpeg 定位

FFMPEG = None  # 全局：ffmpeg 可执行文件完整路径


def _ffmpeg_candidate(path):
    """把用户给的路径（文件或目录）变成候选可执行文件路径。"""
    p = Path(path)
    if p.name.lower() in ('ffmpeg', 'ffmpeg.exe'):
        return p
    return p / FFMPEG_NAME


def _usable(path):
    """文件存在即可；POSIX 下顺手补可执行位（从 zip 解压有时会丢）。"""
    p = Path(path)
    try:
        if not p.is_file():
            return False
    except OSError:
        return False
    if not IS_WIN and not os.access(str(p), os.X_OK):
        try:
            os.chmod(str(p), 0o755)
        except OSError:
            pass
    return True


def find_ffmpeg(hint=''):
    cands = []
    if hint:
        cands.append(_ffmpeg_candidate(hint))
    env = os.environ.get('JIZURA_FFMPEG', '')
    if env:
        cands.append(_ffmpeg_candidate(env))
    here = Path(__file__).resolve().parent
    for base in (here, Path(getattr(sys, '_MEIPASS', here)),
                 Path(sys.executable).resolve().parent):
        cands.append(base / 'ffmpeg' / FFMPEG_NAME)
        cands.append(base / FFMPEG_NAME)
    which = shutil.which('ffmpeg')
    if which:
        cands.append(Path(which))
    if not IS_WIN:   # .app / .command 继承的 PATH 很短，常见安装位置兜底
        for extra in ('/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg',
                      '/opt/local/bin/ffmpeg'):
            cands.append(Path(extra))
    for c in cands:
        if _usable(c):
            return str(c)
    return ''


def ffmpeg_version(path):
    try:
        r = subprocess.run([path, '-version'], capture_output=True, text=True, timeout=30,
                           creationflags=NO_WINDOW)
        return (r.stdout or '').splitlines()[0] if r.returncode == 0 else ''
    except Exception:
        return ''


_ENCODERS = None


def encoders():
    global _ENCODERS
    if _ENCODERS is None:
        _ENCODERS = set()
        if FFMPEG:
            try:
                r = subprocess.run([FFMPEG, '-hide_banner', '-encoders'], capture_output=True,
                                   text=True, timeout=60, creationflags=NO_WINDOW)
                for line in (r.stdout or '').splitlines():
                    m = re.match(r'^\s*[A-Z.]{6}\s+(\S+)', line)
                    if m:
                        _ENCODERS.add(m.group(1))
            except Exception:
                pass
    return _ENCODERS

# ---------------------------------------------------------------- 预设

QUALITY = {'高': 0, '中': 1, '低': 2}
Q_VIDEO = {'libx264': (18, 23, 28), 'libx265': (20, 24, 28),
           'libvpx-vp9': (24, 32, 40), 'libsvtav1': (24, 32, 40), 'libaom-av1': (24, 32, 40)}


def qv(codec, quality):
    return Q_VIDEO[codec][QUALITY.get(quality, 1)]


# 每个预设：kind(video/alpha/audio)、容器扩展名、需要哪个编码器、参数生成器
def build_cmd(preset, inputs, out, quality='中', fps=30):
    """根据预设和输入类型拼 ffmpeg 参数。inputs 由 prepare_input() 生成。"""
    even = 'scale=trunc(iw/2)*2:trunc(ih/2)*2'   # yuv420 系编码要求偶数尺寸
    is_seq = 'seq' in inputs
    att_audio = inputs.get('audio')              # 附加/替换的音频文件
    has_a = bool(att_audio) or (not is_seq and inputs.get('has_audio'))

    # —— 音频输入：只支持音频预设 ——
    if not is_seq and 'video' not in inputs:
        if preset not in AUDIO_PRESETS:
            raise ValueError('音频文件只能输出音频格式：%s' % preset)
        return [FFMPEG, '-hide_banner', '-nostdin', '-y', '-i', inputs['audio'],
                '-vn'] + AUDIO_PRESETS[preset] + [out]
    # —— 序列输入想提取音频：直接用配音频文件 ——
    if is_seq and preset in AUDIO_PRESETS:
        if not att_audio:
            raise ValueError('从序列里提取音频需要先选择「配音频」文件')
        return [FFMPEG, '-hide_banner', '-nostdin', '-y', '-i', att_audio,
                '-vn'] + AUDIO_PRESETS[preset] + [out]

    cmd = [FFMPEG, '-hide_banner', '-nostdin', '-y']
    if is_seq:
        cmd += ['-framerate', str(max(1, min(120, int(fps or 30)))), '-start_number', '1', '-i', inputs['seq']]
    else:
        cmd += ['-i', inputs['video']]
    if att_audio:
        cmd += ['-i', att_audio]

    # —— 从视频/序列里提取音频 ——
    if preset in AUDIO_PRESETS:
        if not has_a:
            raise ValueError('这个文件没有音频轨道，无法提取音频')
        return cmd + (['-map', '1:a:0'] if att_audio else ['-map', '0:a:0']) + ['-vn'] \
            + AUDIO_PRESETS[preset] + [out]

    amap = ['-map', '0:v:0'] + (['-map', '1:a:0?'] if att_audio else (['-map', '0:a:0?'] if has_a else []))
    gif_fps = max(1, min(30, int(fps or 15)))
    vf = []
    vfstr = ''
    if preset == 'mp4-h264':
        args = ['-c:v', 'libx264', '-preset', 'medium', '-crf', str(qv('libx264', quality)),
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart']
        vf.append(even)
    elif preset == 'webm-vp9':
        args = ['-c:v', 'libvpx-vp9', '-crf', str(qv('libvpx-vp9', quality)), '-b:v', '0',
                '-row-mt', '1', '-c:a', 'libopus', '-b:a', '160k']
        vf.append(even)
    elif preset == 'mkv-hevc':
        args = ['-c:v', 'libx265', '-preset', 'medium', '-crf', str(qv('libx265', quality)),
                '-c:a', 'aac', '-b:a', '192k']
        vf.append(even)
    elif preset == 'webm-av1':
        codec = 'libsvtav1' if 'libsvtav1' in encoders() else 'libaom-av1'
        if codec == 'libsvtav1':
            args = ['-c:v', 'libsvtav1', '-preset', '6', '-crf', str(qv(codec, quality))]
        else:
            args = ['-c:v', 'libaom-av1', '-cpu-used', '6', '-crf', str(qv(codec, quality)), '-b:v', '0']
        args += ['-c:a', 'libopus', '-b:a', '160k']
        vf.append(even)
    elif preset == 'mov-prores':
        args = ['-c:v', 'prores_ks', '-profile:v', '3', '-pix_fmt', 'yuv422p10le', '-c:a', 'pcm_s16le']
        vf.append(even)
    elif preset == 'mov-prores4444':
        args = ['-c:v', 'prores_ks', '-profile:v', '4', '-pix_fmt', 'yuva444p10le',
                '-vendor', 'apl0', '-c:a', 'pcm_s16le']
    elif preset == 'webm-vp9-alpha':
        args = ['-c:v', 'libvpx-vp9', '-crf', str(qv('libvpx-vp9', quality)), '-b:v', '0',
                '-pix_fmt', 'yuva420p', '-auto-alt-ref', '0', '-row-mt', '1',
                '-c:a', 'libopus', '-b:a', '160k']
        vf.append(even)
    elif preset == 'gif':
        args = ['-an']
        vfstr = ('fps=%d,split[s0][s1];[s0]palettegen=stats_mode=diff[p];'
                 '[s1][p]paletteuse=dither=bayer:bayer_scale=3') % gif_fps
    elif preset == 'gif-alpha':
        args = ['-an']
        vfstr = ('fps=%d,split[s0][s1];[s0]palettegen=stats_mode=single:reserve_transparent=1[p];'
                 '[s1][p]paletteuse=alpha_threshold=128') % gif_fps
    else:
        raise ValueError('该格式不支持%s输入：%s' % ('序列' if is_seq else '视频', preset))

    if not vfstr and vf:
        vfstr = ','.join(vf)
    final = amap + (['-vf', vfstr] if vfstr else []) + args
    if has_a and preset not in ('gif', 'gif-alpha'):
        final += ['-shortest']
    return cmd + final + [out]


PRESETS = [
    # (id, 组, 显示名, 扩展名, 需要编码器：None=总是可用；str=必须存在；tuple=任一存在即可)
    ('mp4-h264', '视频', 'MP4（H.264，兼容最好）', '.mp4', 'libx264'),
    ('webm-vp9', '视频', 'WebM（VP9）', '.webm', 'libvpx-vp9'),
    ('webm-av1', '视频', 'WebM（AV1，体积小）', '.webm', ('libaom-av1', 'libsvtav1')),
    ('mkv-hevc', '视频', 'MKV（HEVC / H.265）', '.mkv', 'libx265'),
    ('mov-prores', '视频', 'MOV（ProRes 422 HQ，剪辑用）', '.mov', 'prores_ks'),
    ('gif', '视频', 'GIF（高质量调色板）', '.gif', 'gif'),
    ('mov-prores4444', '透明序列', 'MOV（ProRes 4444，带透明）', '.mov', 'prores_ks'),
    ('webm-vp9-alpha', '透明序列', 'WebM（VP9 + 透明）', '.webm', 'libvpx-vp9'),
    ('gif-alpha', '透明序列', 'GIF（带透明，单色透明）', '.gif', 'gif'),
    ('mp3', '音频', 'MP3', '.mp3', 'libmp3lame'),
    ('wav', '音频', 'WAV（无损 PCM）', '.wav', None),
    ('flac', '音频', 'FLAC（无损压缩）', '.flac', 'flac'),
    ('m4a', '音频', 'M4A（AAC）', '.m4a', 'aac'),
]

AUDIO_PRESETS = {
    'mp3': ['-c:a', 'libmp3lame', '-q:a', '2'],
    'wav': ['-c:a', 'pcm_s16le'],
    'flac': ['-c:a', 'flac', '-compression_level', '5'],
    'm4a': ['-c:a', 'aac', '-b:a', '256k'],
}

VIDEO_EXT = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v', '.wmv', '.flv', '.ts', '.mpg', '.mpeg', '.ogv'}
AUDIO_EXT = {'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.wma'}

UPLOADS = Path(tempfile.gettempdir()) / 'jizura-ffmpeg'
BUSY = threading.Lock()


def available_presets():
    enc = encoders()
    out = []
    for pid, group, label, ext, need in PRESETS:
        if need is not None:
            needs = (need,) if isinstance(need, str) else tuple(need)
            if not any(n in enc for n in needs):
                continue
        out.append({'id': pid, 'group': group, 'label': label, 'ext': ext})
    return out


# ---------------------------------------------------------------- 转换核心

def probe_has_audio(path):
    try:
        r = subprocess.run([FFMPEG, '-hide_banner', '-i', path], capture_output=True, text=True,
                           timeout=60, creationflags=NO_WINDOW)
        return 'Audio:' in (r.stderr or '')
    except Exception:
        return False


def natkey(p):
    m = re.findall(r'(\d+)', p.stem)
    return (int(m[-1]) if m else 0, p.name)


def prepare_input(src, fps=30):
    """把输入整理为 build_cmd 的 inputs 字典。返回 (inputs, cleanup_paths)"""
    src = Path(src)
    clean = []
    ext = src.suffix.lower()
    if ext == '.zip':
        import zipfile
        outdir = Path(tempfile.mkdtemp(prefix='jizura-seq-'))
        clean.append(outdir)
        with zipfile.ZipFile(src) as z:
            z.extractall(outdir)
        pngs = [p for p in outdir.rglob('*') if p.suffix.lower() == '.png']
        if not pngs:
            raise ValueError('这个 zip 里没有 PNG 图片（导出时选「PNG 序列」）')
        parents = {p.parent for p in pngs}
        if len(parents) > 1:
            raise ValueError('这是「分层 PNG 序列」（每层一个文件夹），无法直接合成视频；'
                             '请用不分层的方式重新导出 PNG 序列')
        pngs.sort(key=natkey)
        seqdir = outdir / 'seq'
        seqdir.mkdir(exist_ok=True)
        for i, p in enumerate(pngs, 1):
            os.replace(p, seqdir / ('seq_%06d.png' % i))
        return {'seq': str(seqdir / 'seq_%06d.png'), 'nframes': len(pngs)}, clean
    if ext in AUDIO_EXT:
        return {'audio': str(src)}, clean
    if ext in VIDEO_EXT:
        return {'video': str(src), 'has_audio': probe_has_audio(str(src))}, clean
    raise ValueError('不支持的文件类型：%s' % (ext or '(无扩展名)'))


def run_convert(input_path, preset, quality='中', fps=30, audio_path=None, out_path=None):
    inputs, clean = prepare_input(input_path, fps)
    if audio_path:
        inputs['audio'] = str(audio_path)
    pid = next((p for p in PRESETS if p[0] == preset), None)
    if not pid:
        raise ValueError('未知预设：%s' % preset)
    if out_path is None:
        outdir = Path(tempfile.mkdtemp(prefix='jizura-out-'))
        clean.append(outdir)
        out_path = outdir / ('out' + pid[3])
    cmd = build_cmd(preset, inputs, str(out_path), quality=quality, fps=fps)
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW)
    if r.returncode != 0 or not Path(out_path).exists():
        tail = '\n'.join((r.stderr or '').splitlines()[-12:])
        raise RuntimeError('ffmpeg 转换失败：\n%s' % tail)
    return Path(out_path), round(time.time() - t0, 1), clean


def cleanup(paths):
    for p in paths:
        try:
            if Path(p).is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif Path(p).exists():
                Path(p).unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- HTTP 服务

class Handler(BaseHTTPRequestHandler):
    server_version = 'JIZURA-FFmpeg/' + VERSION

    def _send(self, code, obj, ctype='application/json'):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8') if ctype == 'application/json' else obj
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)

    def _cors(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write('[%s] %s\n' % (self.log_date_time_string(), fmt % args))

    def do_OPTIONS(self):
        self._cors()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/ping':
            ver = ffmpeg_version(FFMPEG) if FFMPEG else ''
            self._send(200, {
                'ok': True, 'service': 'jizura-ffmpeg', 'version': VERSION,
                'ffmpeg': {'found': bool(FFMPEG), 'path': FFMPEG, 'version': ver},
                'presets': available_presets() if FFMPEG else [],
                'busy': BUSY.locked(),
                'hint': 'JIZURA 转换工具运行中。POST /upload 上传文件 → POST /convert 转换。',
            })
        else:
            self._send(200, {'ok': True, 'service': 'jizura-ffmpeg', 'version': VERSION})

    def do_POST(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        try:
            if u.path == '/upload':
                n = int(self.headers.get('Content-Length') or 0)
                if n <= 0:
                    return self._send(400, {'ok': False, 'error': '空文件'})
                name = (qs.get('name', ['input'])[0] or 'input').strip()
                safe = re.sub(r'[\\/:*?"<>|]', '_', name)[:120] or 'input'
                UPLOADS.mkdir(parents=True, exist_ok=True)
                up = UPLOADS / (uuid.uuid4().hex + '_' + safe)
                with open(up, 'wb') as f:
                    left = n
                    while left > 0:
                        chunk = self.rfile.read(min(1024 * 1024, left))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
                return self._send(200, {'ok': True, 'id': up.name})
            if u.path == '/convert':
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
                inp = UPLOADS / (body.get('input') or '')
                if not inp.is_file():
                    return self._send(404, {'ok': False, 'error': '找不到上传的文件，请重新上传'})
                preset = body.get('preset') or 'mp4-h264'
                quality = body.get('quality') or '中'
                fps = int(body.get('fps') or 30)
                audio = None
                if body.get('audio'):
                    a = UPLOADS / body['audio']
                    if a.is_file():
                        audio = a
                got = BUSY.acquire(blocking=False)
                if not got:
                    return self._send(409, {'ok': False, 'error': '已有转换任务在进行，请稍候'})
                out = None
                clean = []
                try:
                    out, secs, clean = run_convert(inp, preset, quality, fps, audio)
                    pid = next(p for p in PRESETS if p[0] == preset)
                    orig = body.get('origName') or inp.name
                    stem = Path(re.sub(r'[\\/:*?"<>|]', '_', orig)).stem[:80] or 'output'
                    filename = '%s_%s%s' % (stem, preset, pid[3])
                    ascii_name = re.sub(r'[^A-Za-z0-9._-]', '_', filename) or ('output' + pid[3])
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Length', str(out.stat().st_size))
                    self.send_header('Content-Disposition',
                                     'attachment; filename="%s"; filename*=UTF-8\'\'%s' % (ascii_name, quote(filename)))
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.send_header('Access-Control-Expose-Headers', 'Content-Disposition, X-Jizura-Secs')
                    self.send_header('X-Jizura-Secs', str(secs))
                    self.end_headers()
                    try:
                        with open(out, 'rb') as f:
                            shutil.copyfileobj(f, self.wfile, 1024 * 1024)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        pass                                  # 客户端断开（下载取消等）
                finally:
                    cleanup(([out] if out else []) + clean)
                    for p in (inp, audio):
                        try:
                            if p:
                                p.unlink()
                        except OSError:
                            pass
                    BUSY.release()
                return
            self._send(404, {'ok': False, 'error': '未知接口'})
        except ValueError as e:
            self._send(400, {'ok': False, 'error': str(e)})
        except RuntimeError as e:
            self._send(500, {'ok': False, 'error': str(e)})
        except Exception as e:
            self._send(500, {'ok': False, 'error': '内部错误：%s' % e})


def serve(port, ffmpeg_hint=''):
    global FFMPEG
    FFMPEG = find_ffmpeg(ffmpeg_hint)
    if FFMPEG:
        print('FFmpeg：%s' % ffmpeg_version(FFMPEG))
    else:
        print('提示：未找到 %s —— 把 %s 放到本目录的 ffmpeg/ 子目录，' % (FFMPEG_NAME, FFMPEG_NAME))
        print('      或用 --ffmpeg 目录 指定；也可运行 get-ffmpeg.py 自动下载。')
        print('      （未找到时服务仍会启动，面板会显示「未检测到 FFmpeg」）')
    # 清理上次残留
    try:
        if UPLOADS.is_dir():
            for f in UPLOADS.iterdir():
                try:
                    f.unlink() if f.is_file() else shutil.rmtree(f, ignore_errors=True)
                except OSError:
                    pass
    except OSError:
        pass
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print('JIZURA FFmpeg 转换工具已启动： http://127.0.0.1:%d' % port)
    print('保持本窗口开着；插件面板会自动连接。Ctrl+C 停止。')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止。')


def cli_convert(a):
    global FFMPEG
    FFMPEG = find_ffmpeg(a.ffmpeg)
    if not FFMPEG:
        print('未找到 %s：用 --ffmpeg 目录 指定，或放到本目录 ffmpeg/ 下。' % FFMPEG_NAME)
        return 2
    out = a.output
    if not out:
        pid = next(p for p in PRESETS if p[0] == a.preset)
        out = str(Path(a.input).with_suffix('')) + '_' + a.preset + pid[3]
    print('转换：%s → %s（%s / %s）' % (a.input, out, a.preset, a.quality))
    try:
        got, secs, _clean = run_convert(a.input, a.preset, a.quality, a.fps,
                                        getattr(a, 'audio', None), out)
    except (ValueError, RuntimeError) as e:
        print('失败：%s' % e)
        return 1
    print('完成：%s（%.1f MB，用时 %.1f 秒）' % (got, got.stat().st_size / 1048576.0, secs))
    return 0


def main():
    ap = argparse.ArgumentParser(description='JIZURA FFmpeg 转换工具')
    ap.add_argument('--port', type=int, default=8788, help='服务端口（默认 8788）')
    ap.add_argument('--ffmpeg', default='', help='ffmpeg 可执行文件或其所在目录（默认自动查找）')
    sub = ap.add_subparsers(dest='cmd')
    c = sub.add_parser('convert', help='命令行转换')
    c.add_argument('-i', '--input', required=True)
    c.add_argument('-o', '--output', default='')
    c.add_argument('-p', '--preset', default='mp4-h264',
                   help='预设：' + ', '.join(p[0] for p in PRESETS))
    c.add_argument('-a', '--audio', default='', help='配音频（序列/无声视频用）')
    c.add_argument('-q', '--quality', default='中', choices=['高', '中', '低'])
    c.add_argument('-f', '--fps', type=int, default=30, help='序列帧率（默认 30）')
    a = ap.parse_args()
    if a.cmd == 'convert':
        return cli_convert(a)
    serve(a.port, a.ffmpeg)
    return 0


if __name__ == '__main__':
    sys.exit(main())
