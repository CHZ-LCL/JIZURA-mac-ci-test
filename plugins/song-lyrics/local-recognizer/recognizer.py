#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JIZURA 本地识曲工具（本地识曲小工具）
====================================
给 JIZURA 的「AI 识曲 → 歌词」插件用的本地小服务，只用本机资源：

  GET  /ping                → {"ok": true, ...}      用于插件自动检测
  POST /recognize           → 用 Shazam（免费、免注册）识别请求体里的音频
  POST /audd?token=XXX      → 用你自己的 AudD token 识别（代理，绕过浏览器 CORS 限制）

用法：
    python recognizer.py [端口]        默认端口 8787
或者双击同目录下的「启动识曲工具.bat」（会自建虚拟环境并安装依赖）。

要求：Python 3.10+（ShazamIO 的要求）；首次运行需要联网安装依赖。
音频建议 12 秒以内（Shazam 客户端本身就只发送约 12 秒；更长反而识别不到）。
"""
import asyncio
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
MAX_BODY = 30 * 1024 * 1024  # 30 MB

CORS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '86400',
}


# ---------------------------------------------------------------- engines
def shazam_recognize(audio: bytes):
    """用 ShazamIO 识别一段音频（bytes）。返回原始 dict。"""
    from shazamio import Shazam  # 延迟导入：第一次调用才加载（比较慢）

    async def run():
        shazam = Shazam()   # shazamio ≥ 2.3x: no async-context-manager anymore
        return await shazam.recognize(audio)

    return asyncio.run(run())


def parse_track(out):
    """从 ShazamIO 原始结果里取出插件需要的字段。"""
    track = (out or {}).get('track') or {}
    if not track:
        return None
    res = {
        'title': track.get('title') or '',
        'artist': track.get('subtitle') or '',
        'album': '',
        'genre': '',
        'year': '',
        'cover': ((track.get('images') or {}).get('coverart') or ''),
    }
    for section in track.get('sections') or []:
        for item in section.get('metadata') or []:
            key = (item.get('title') or '').lower()
            val = item.get('text') or ''
            if key == 'album':
                res['album'] = val
            elif key == 'genre':
                res['genre'] = val
            elif key in ('released', 'release date', 'releasedate', 'year'):
                res['year'] = val
    return res


def audd_proxy(token: str, audio: bytes):
    """把你的音频转发给 AudD 并原样返回它们的 JSON（解决浏览器直连的 CORS 问题）。"""
    import requests

    r = requests.post(
        'https://api.audd.io/',
        data={'api_token': token, 'return': 'apple_music'},
        files={'file': ('clip.wav', audio, 'audio/wav')},
        timeout=45,
    )
    return r.json()


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = 'JizuraRecognizer/1.0'

    def _send(self, code, obj):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        for k, v in CORS.items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):
        sys.stderr.write('[%s] %s\n' % (self.log_date_time_string(), fmt % args))

    # -- routes ----------------------------------------------------
    def do_OPTIONS(self):
        self._send(204, b'')

    def do_GET(self):
        path = self.path.split('?', 1)[0].rstrip('/') or '/'
        if path == '/ping':
            self._send(200, {
                'ok': True,
                'service': 'jizura-recognizer',
                'version': '1.0',
                'engine': 'shazamio',
                'python': sys.version.split()[0],
            })
        else:
            self._send(200, {
                'ok': True,
                'hint': 'JIZURA 本地识曲工具运行中。POST /recognize 发送音频字节；POST /audd?token=… 走 AudD。',
            })

    def do_POST(self):
        from urllib.parse import parse_qs, urlparse

        url = urlparse(self.path)
        route = url.path.rstrip('/')
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._send(400, {'ok': False, 'error': 'empty body'})
            return
        if length > MAX_BODY:
            self._send(413, {'ok': False, 'error': 'body too large'})
            return
        body = self.rfile.read(length)

        if route == '/recognize':
            try:
                out = shazam_recognize(body)
                track = parse_track(out)
                if not track:
                    self._send(200, {'ok': False, 'error': 'not_found'})
                    return
                track['ok'] = True
                track['engine'] = 'shazamio'
                self._send(200, track)
            except Exception as e:  # 引擎或网络错误都原样告诉插件
                self._send(200, {'ok': False, 'error': '%s: %s' % (type(e).__name__, e)})
            return

        if route == '/audd':
            token = (parse_qs(url.query).get('token') or [''])[0].strip()
            if not token:
                self._send(400, {'ok': False, 'error': 'missing token'})
                return
            try:
                self._send(200, audd_proxy(token, body))
            except Exception as e:
                self._send(200, {
                    'status': 'error',
                    'error': {'error_code': -1, 'error_message': '%s: %s' % (type(e).__name__, e)},
                })
            return

        self._send(404, {'ok': False, 'error': 'not found'})


def main():
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print('=' * 56)
    print('JIZURA 本地识曲工具已启动： http://127.0.0.1:%d' % PORT)
    print('保持这个窗口开着；JIZURA 插件会自动连接它。Ctrl+C 退出。')
    print('=' * 56)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已退出。')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
