#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aozora2epub - 青空文庫形式のテキストを Kindle で読める EPUB3 に一括変換する。

使い方:
    python aozora2epub.py 入力ディレクトリ [入力ファイル ...] [-o 出力ディレクトリ]

入力にディレクトリを指定すると、その配下 (サブディレクトリを含む) にある
すべての .txt / .zip (青空文庫の配布 zip) を変換する。
外部ライブラリは不要 (Python 3.8 以降の標準ライブラリのみで動作)。
"""

import argparse
import datetime
import html
import re
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

__version__ = '1.0.0'

# ---------------------------------------------------------------------------
# 文字・注記の基本処理
# ---------------------------------------------------------------------------

ZEN_DIGITS = str.maketrans('０１２３４５６７８９', '0123456789')
KANSUJI = {'〇': 0, '一': 1, '二': 2, '三': 3, '四': 4,
           '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
N = r'([0-9０-９〇一二三四五六七八九十]+)'

KANJI_RE = re.compile(
    '[々〆〇〻ヵヶ㐀-䶿一-鿿'
    '豈-﫿\U00020000-\U0003ffff]')

# 本文の装飾注記 (開始注記・前方参照注記で共通)
EMPHASIS = {
    '': 'em-sesame', '黒ゴマ': 'em-sesame', '白ゴマ': 'em-sesame-open',
    '丸': 'em-circle', '黒丸': 'em-circle', '白丸': 'em-circle-open',
    '黒三角': 'em-triangle', '白三角': 'em-triangle-open',
    '二重丸': 'em-double-circle-open', '蛇の目': 'em-double-circle',
    'ばつ': 'em-x',
}
SIDELINE = {'傍線': 'sl-solid', '二重傍線': 'sl-double', '鎖線': 'sl-dotted',
            '破線': 'sl-dashed', '波線': 'sl-wavy'}
SIMPLE_STYLES = {
    '太字': ('bold', 'b'), '斜体': ('italic', 'i'),
    '小書き': ('kogaki', 'kogaki'),
    '上付き小文字': ('sup', 'sup'), '行右小書き': ('sup', 'sup'),
    '下付き小文字': ('sub', 'sub'), '行左小書き': ('sub', 'sub'),
    '罫囲み': ('kei', 'kei'), 'キャプション': ('caption', 'caption'),
    '横組み': ('yoko', None),
}
HEADING_LEVEL = {'大': 1, '中': 2, '小': 3}

RE_EMPHASIS = re.compile(r'^(?:左に)?(%s)?傍点$' % '|'.join(k for k in EMPHASIS if k))
RE_SIDELINE = re.compile(r'^(?:左に)?(%s)$' % '|'.join(SIDELINE))
RE_SIZE = re.compile(r'^%s段階(大きな|小さな)文字$' % N)
RE_PAGEBREAK = re.compile(r'^(改ページ|改丁|改見開き|改段)$')
RE_JISAGE = re.compile(r'^(?:天から)?%s字下げ$' % N)
RE_JISAGE_ORI = re.compile(r'^(?:天から)?%s字下げ、折り返して%s字下げ$' % (N, N))
RE_BLK_JISAGE = re.compile(r'^ここから(?:天から)?%s字下げ$' % N)
RE_BLK_JISAGE_ORI = re.compile(r'^ここから(?:天から)?%s字下げ、折り返して%s字下げ$' % (N, N))
RE_BLK_TENTSUKI_ORI = re.compile(r'^ここから改行天付き、折り返して%s字下げ$' % N)
RE_BLK_JISAGE_END = re.compile(r'^ここで字下げ終わ?り$')
RE_JIAGE = re.compile(r'^地から%s字上げ$' % N)
RE_BLK_JIAGE = re.compile(r'^ここから地から%s字上げ$' % N)
RE_BLK_ALIGN_END = re.compile(r'^ここで(地付き|字上げ)終わ?り$')
RE_MIDASHI = re.compile(r'^(?:同行|窓)?(大|中|小)見出し$')
RE_MIDASHI_END = re.compile(r'^(?:同行|窓)?(大|中|小)見出し終わ?り$')
RE_BLK_MIDASHI = re.compile(r'^ここから(?:同行|窓)?(大|中|小)見出し$')
RE_BLK_MIDASHI_END = re.compile(r'^ここで(?:同行|窓)?(大|中|小)見出し終わ?り$')
RE_BLK_START = re.compile(r'^ここから(.+)$')
RE_BLK_END = re.compile(r'^ここで(.+?)終わ?り$')
RE_INLINE_END = re.compile(r'^(.+?)終わ?り$')
RE_FORWARD_SPLIT = re.compile(r'」(は|に|の)')
RE_RUBY_NOTE = re.compile(r'^「(.+)」の(?:注記|ルビ)$')
RE_IMAGE = re.compile(r'（(?P<file>[^（）、]+?\.(?:png|jpe?g|gif|svg))(?:、[^）]*)?）入る$', re.I)
RE_CAPTION = re.compile(r'^「(.*?)」のキャプション付きの')
RE_GAIJI = re.compile(r'^(?:「(?P<d1>.*?)」|(?P<d2>[^「」、]+))、')
RE_ANNOTATION = re.compile(r'［＃[^［］]*(?:［[^［］]*］[^［］]*)*］')
RE_GAIJI_ANNOTATION = re.compile(r'※［＃([^［］]*(?:［[^［］]*］[^［］]*)*)］')

MEDIA_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
               '.gif': 'image/gif', '.svg': 'image/svg+xml'}


def esc(s):
    return html.escape(s, quote=True)


def to_int(s):
    s = s.translate(ZEN_DIGITS)
    if s.isdigit():
        return int(s)
    if '十' in s:
        a, _, b = s.partition('十')
        return (KANSUJI.get(a, 1) if a else 1) * 10 + (KANSUJI.get(b, 0) if b else 0)
    n = 0
    for ch in s:
        n = n * 10 + KANSUJI.get(ch, 0)
    return n


def char_class(ch):
    if KANJI_RE.match(ch):
        return 'kanji'
    o = ord(ch)
    if 0x3041 <= o <= 0x309f:
        return 'hira'
    if 0x30a1 <= o <= 0x30ff or 0x31f0 <= o <= 0x31ff or 0xff66 <= o <= 0xff9f:
        return 'kata'
    if ch.isalnum() and (o < 0x250 or 0xff10 <= o <= 0xff5a):
        return 'latin'
    return 'other'


def decode_gaiji(content):
    """外字注記の中身から文字を得る。JIS X 0213 の面区点番号か U+XXXX を解釈する。"""
    content = content.translate(ZEN_DIGITS)
    m = re.search(r'U\+([0-9A-Fa-f]{4,6})', content)
    if m:
        try:
            return chr(int(m.group(1), 16))
        except ValueError:
            pass
    m = re.search(r'(?<![0-9])([12])-(\d{1,2})-(\d{1,2})(?![0-9])', content)
    if m:
        men, ku, ten = (int(x) for x in m.groups())
        if 1 <= ku <= 94 and 1 <= ten <= 94:
            raw = bytes([0xa0 + ku, 0xa0 + ten])
            if men == 2:
                raw = b'\x8f' + raw
            try:
                return raw.decode('euc_jis_2004')
            except UnicodeDecodeError:
                pass
    return None


def strip_markup(s):
    """ルビ・注記を取り除いた素のテキストを返す (メタデータや前方参照の照合用)。"""
    s = RE_GAIJI_ANNOTATION.sub(lambda m: decode_gaiji(m.group(1)) or '※', s)
    s = RE_ANNOTATION.sub('', s)
    s = re.sub(r'《[^《》]*》', '', s)
    return s.replace('｜', '')


def replace_kunoji(s):
    """青空文庫の「くの字点」表記 ／＼ ／″＼ を Unicode の文字に置き換える。"""
    return s.replace('／″＼', '〴〵').replace('／＼', '〳〵')


def find_bracket_end(text, start):
    depth = 0
    for j in range(start, len(text)):
        c = text[j]
        if c == '［':
            depth += 1
        elif c == '］':
            depth -= 1
            if depth == 0:
                return j
    return -1


def style_for(spec, vertical):
    """装飾注記名 (例: 「傍点」「太字」「２段階大きな文字」) → (グループ名, CSS クラス, 前置, 後置)"""
    m = RE_EMPHASIS.match(spec)
    if m:
        return 'em', EMPHASIS[m.group(1) or ''], '', ''
    m = RE_SIDELINE.match(spec)
    if m:
        return 'sl', SIDELINE[m.group(1)], '', ''
    if spec in SIMPLE_STYLES:
        name, cls = SIMPLE_STYLES[spec]
        return name, cls, '', ''
    if spec == '縦中横':
        return 'tcy', 'tcy' if vertical else None, '', ''
    m = RE_SIZE.match(spec)
    if m:
        n = min(max(to_int(m.group(1)), 1), 3)
        return 'size', ('font-larger%d' if m.group(2) == '大きな' else 'font-smaller%d') % n, '', ''
    if spec == '割り注':
        return 'warichu', 'warichu', '（', '）'
    return None


def style_end_name(spec):
    if spec in ('大きな文字', '小さな文字'):
        return 'size'
    st = style_for(spec, True)
    return st[0] if st else None


# ---------------------------------------------------------------------------
# 行の解析
# ---------------------------------------------------------------------------

class Unit:
    """行を構成する要素。plain は前方参照の照合用テキスト、html は出力。"""
    __slots__ = ('plain', 'html', 'cls', 'kind', 'name', 'close', 'heading')

    def __init__(self, plain, html_, cls='other', kind='text', name='', close='', heading=0):
        self.plain = plain
        self.html = html_
        self.cls = cls
        self.kind = kind      # text / gaiji / ruby / group / image / open / close
        self.name = name
        self.close = close
        self.heading = heading


class BlockState:
    """「ここから〜」「ここで〜終わり」で指定される複数行にわたる書式。"""

    def __init__(self):
        self.indent = None    # (1行目の字下げ, 2行目以降の字下げ)
        self.align = None     # 地付き/字上げ: 字上げ量
        self.heading = 0
        self.classes = {}

    def copy(self):
        b = BlockState()
        b.indent, b.align, b.heading = self.indent, self.align, self.heading
        b.classes = dict(self.classes)
        return b


class LineContext:
    def __init__(self):
        self.indent = None
        self.align = None
        self.heading = 0
        self.heading_label = None
        self.break_before = False
        self.break_after = False
        self.snapshot = None
        self.open_stack = []


class Page:
    def __init__(self):
        self.lines = []
        self.size = 0
        self.has_content = False
        self.headings = []
        self.filename = ''


class Converter:
    SOFT_PAGE_LIMIT = 200 * 1024
    HARD_PAGE_LIMIT = 400 * 1024

    def __init__(self, base_dir, vertical=True, auto_tcy=True):
        self.base_dir = Path(base_dir)
        self.vertical = vertical
        self.auto_tcy = auto_tcy
        self.images = {}          # 元画像の絶対パス -> EPUB 内のファイル名
        self.warnings = []
        self.heading_count = 0
        self.reset()

    def reset(self):
        self.block = BlockState()
        self.carry = []

    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    # --- 行 → Unit 列 --------------------------------------------------------

    def parse_line(self, text, ctx):
        units = []
        for u in self.carry:
            self._open(units, ctx, u.name, u.html, u.close, u.heading)
        self.carry = []
        bar = None
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch == '｜':
                bar = len(units)
                i += 1
                continue
            if ch == '《':
                j = text.find('》', i + 1)
                if j > 0 and self._ruby(units, bar, text[i + 1:j]):
                    bar = None
                    i = j + 1
                    continue
            if ch == '［' and text.startswith('［＃', i):
                j = find_bracket_end(text, i)
                if j > 0:
                    self._annotation(text[i + 2:j], units, ctx)
                    i = j + 1
                    continue
            if self.vertical and self.auto_tcy:
                consumed = self._auto_tcy(text, i, units, ctx)
                if consumed:
                    i += consumed
                    continue
            self._add(units, ctx, Unit(ch, esc(ch), char_class(ch)))
            i += 1
        # 行末で閉じていない装飾は閉じ、次の行に引き継ぐ
        for u in reversed(ctx.open_stack):
            units.append(Unit('', u.close, kind='close', name=u.name))
        self.carry = list(ctx.open_stack)
        return units

    def _add(self, units, ctx, unit):
        units.append(unit)
        if ctx.snapshot is None and (unit.plain.strip() or unit.kind == 'image'):
            ctx.snapshot = self.block.copy()

    def _open(self, units, ctx, name, open_html, close_html, heading=0):
        u = Unit('', open_html, kind='open', name=name, close=close_html, heading=heading)
        units.append(u)
        ctx.open_stack.append(u)

    def _close(self, units, ctx, name):
        for k in range(len(ctx.open_stack) - 1, -1, -1):
            if ctx.open_stack[k].name == name:
                inner = ctx.open_stack[k + 1:]
                # 入れ子を崩さないよう、内側の装飾を一旦閉じて開き直す
                for u in reversed(inner):
                    units.append(Unit('', u.close, kind='close', name=u.name))
                target = ctx.open_stack[k]
                units.append(Unit('', target.close, kind='close', name=name))
                del ctx.open_stack[k:]
                for u in inner:
                    self._open(units, ctx, u.name, u.html, u.close, u.heading)
                return

    def _auto_tcy(self, text, i, units, ctx):
        ch = text[i]
        if '0' <= ch <= '9':
            m = re.compile(r'[0-9]+').match(text, i)
            run = m.group()
            prev = text[i - 1] if i > 0 else ''
            nxt = text[m.end()] if m.end() < len(text) else ''
            if len(run) <= 2 and not re.match(r'[0-9A-Za-z.,:/%_-]', prev or ' ') \
                    and not re.match(r'[0-9A-Za-z.,:/%_-]', nxt or ' '):
                self._add(units, ctx, Unit(run, '<span class="tcy">%s</span>' % run, 'latin'))
                return len(run)
            for c in run:
                self._add(units, ctx, Unit(c, c, 'latin'))
            return len(run)
        marks = set('!?！？')
        pair = text[i:i + 2]
        if len(pair) == 2 and pair[0] in marks and pair[1] in marks:
            prev = text[i - 1] if i > 0 else ''
            nxt = text[i + 2] if i + 2 < len(text) else ''
            if prev not in marks and nxt not in marks:
                half = pair.replace('！', '!').replace('？', '?')
                self._add(units, ctx, Unit(pair, '<span class="tcy">%s</span>' % half))
                return 2
        return 0

    def _ruby(self, units, bar, reading):
        if bar is not None:
            start = bar
        else:
            if not units:
                return False
            cls = units[-1].cls
            if cls not in ('kanji', 'kata', 'latin') or units[-1].kind not in ('text', 'gaiji'):
                return False
            start = len(units)
            while start > 0 and units[start - 1].cls == cls and units[start - 1].kind in ('text', 'gaiji'):
                start -= 1
        base = units[start:]
        if not base or any(u.kind == 'ruby' for u in base) or not ''.join(u.plain for u in base):
            return False
        rt = esc(strip_markup(reading))
        units[start:] = [Unit(''.join(u.plain for u in base),
                              '<ruby>%s<rp>（</rp><rt>%s</rt><rp>）</rp></ruby>'
                              % (''.join(u.html for u in base), rt), 'ruby', kind='ruby')]
        return True

    def _find_target(self, units, target):
        """前方参照の対象文字列に対応する Unit の範囲 [s, e) を返す。"""
        target = strip_markup(target)
        if not target:
            return None
        joined = ''.join(u.plain for u in units)
        pos = joined.rfind(target)
        if pos < 0:
            return None
        end = pos + len(target)
        s = e = None
        off = 0
        for k, u in enumerate(units):
            ln = len(u.plain)
            if s is None and ln and off + ln > pos:
                s = k
            if s is not None and ln and off + ln >= end:
                e = k + 1
                break
            off += ln
        if s is None or e is None:
            return None
        return s, e

    def _wrap(self, units, target, open_html, close_html):
        r = self._find_target(units, target)
        if r is None:
            self.warn('前方参照の対象が見つかりません: 「%s」' % target)
            return False
        s, e = r
        part = units[s:e]
        units[s:e] = [Unit(''.join(u.plain for u in part),
                           open_html + ''.join(u.html for u in part) + close_html,
                           kind='group')]
        return True

    # --- 注記 --------------------------------------------------------------

    def _annotation(self, content, units, ctx):
        # 外字: ※［＃「…」、第3水準1-85-54］
        if units and units[-1].kind == 'text' and units[-1].plain == '※':
            m = RE_GAIJI.match(content)
            if m:
                ch = decode_gaiji(content)
                if ch:
                    units[-1] = Unit(ch, esc(ch), char_class(ch[0]), kind='gaiji')
                else:
                    desc = m.group('d1') or m.group('d2')
                    units[-1] = Unit('※', '<span class="gaiji-note">※（%s）</span>' % esc(desc),
                                     'kanji', kind='gaiji')
                    self.warn('外字を変換できませんでした: %s' % content)
                return

        has_content = ctx.snapshot is not None
        c = content.strip()

        if RE_PAGEBREAK.match(c):
            if has_content:
                ctx.break_after = True
            else:
                ctx.break_before = True
            return

        m = RE_IMAGE.search(c)
        if m:
            self._image(units, ctx, m.group('file'), RE_CAPTION.match(c))
            return

        if c.startswith('「'):
            self._forward_ref(c, units, ctx)
            return

        # 1行だけの字下げ・地付き
        m = RE_JISAGE.match(c)
        if m:
            n = to_int(m.group(1))
            ctx.indent = (n, n)
            return
        m = RE_JISAGE_ORI.match(c)
        if m:
            ctx.indent = (to_int(m.group(1)), to_int(m.group(2)))
            return
        if c == '地付き':
            ctx.align = 0
            return
        m = RE_JIAGE.match(c)
        if m:
            ctx.align = to_int(m.group(1))
            return

        # ブロック (ここから〜 / ここで〜終わり)
        m = RE_BLK_JISAGE.match(c)
        if m:
            n = to_int(m.group(1))
            self.block.indent = (n, n)
            return
        m = RE_BLK_JISAGE_ORI.match(c)
        if m:
            self.block.indent = (to_int(m.group(1)), to_int(m.group(2)))
            return
        m = RE_BLK_TENTSUKI_ORI.match(c)
        if m:
            self.block.indent = (0, to_int(m.group(1)))
            return
        if RE_BLK_JISAGE_END.match(c):
            self.block.indent = None
            return
        if c == 'ここから地付き':
            self.block.align = 0
            return
        m = RE_BLK_JIAGE.match(c)
        if m:
            self.block.align = to_int(m.group(1))
            return
        if RE_BLK_ALIGN_END.match(c):
            self.block.align = None
            return
        m = RE_BLK_MIDASHI.match(c)
        if m:
            self.block.heading = HEADING_LEVEL[m.group(1)]
            return
        if RE_BLK_MIDASHI_END.match(c):
            self.block.heading = 0
            return
        m = RE_BLK_END.match(c)
        if m:
            name = style_end_name(m.group(1))
            if name:
                self.block.classes.pop(name, None)
                return
        m = RE_BLK_START.match(c)
        if m:
            st = style_for(m.group(1), self.vertical)
            if st:
                if st[1]:
                    self.block.classes[st[0]] = st[1]
                return

        # 範囲指定の見出し・装飾 (［＃太字］…［＃太字終わり］)
        m = RE_MIDASHI.match(c)
        if m:
            self._open(units, ctx, 'midashi', '', '', HEADING_LEVEL[m.group(1)])
            return
        if RE_MIDASHI_END.match(c):
            self._close(units, ctx, 'midashi')
            return
        st = style_for(c, self.vertical)
        if st:
            name, cls, pre, post = st
            open_html = ('<span class="%s">' % cls if cls else '') + pre
            close_html = post + ('</span>' if cls else '')
            self._open(units, ctx, name, open_html, close_html)
            return
        m = RE_INLINE_END.match(c)
        if m:
            name = style_end_name(m.group(1))
            if name:
                self._close(units, ctx, name)
                return

        # その他 (ページの左右中央、本文終わり、未対応の注記など) は出力しない

    def _forward_ref(self, c, units, ctx):
        for m in RE_FORWARD_SPLIT.finditer(c):
            target, rel, spec = c[1:m.start()], m.group(1), c[m.end():]
            if rel == 'の' and not spec.startswith('左に'):
                continue
            mh = RE_MIDASHI.match(spec)
            if mh:
                ctx.heading = HEADING_LEVEL[mh.group(1)]
                ctx.heading_label = strip_markup(target).strip()
                return
            mr = RE_RUBY_NOTE.match(spec)
            if mr:
                if rel == 'に':
                    r = self._find_target(units, target)
                    if r and not any(u.kind == 'ruby' for u in units[r[0]:r[1]]):
                        part = units[r[0]:r[1]]
                        units[r[0]:r[1]] = [Unit(
                            ''.join(u.plain for u in part),
                            '<ruby>%s<rp>（</rp><rt>%s</rt><rp>）</rp></ruby>'
                            % (''.join(u.html for u in part), esc(strip_markup(mr.group(1)))),
                            'ruby', kind='ruby')]
                return
            st = style_for(spec, self.vertical)
            if st:
                _, cls, pre, post = st
                self._wrap(units, target,
                           ('<span class="%s">' % cls if cls else '') + pre,
                           post + ('</span>' if cls else ''))
                return
        # 「ママ」「底本では〜」などの校訂注記は出力しない

    def _image(self, units, ctx, filename, caption_match):
        src = (self.base_dir / filename)
        caption = strip_markup(caption_match.group(1)) if caption_match else ''
        if not src.is_file():
            self.warn('画像が見つかりません: %s' % filename)
            if caption:
                self._add(units, ctx, Unit(caption, '<span class="caption">%s</span>' % esc(caption)))
            return
        key = str(src.resolve())
        if key not in self.images:
            ext = src.suffix.lower()
            self.images[key] = 'img%04d%s' % (len(self.images) + 1, '.jpg' if ext == '.jpeg' else ext)
        self._add(units, ctx, Unit('', '<img class="illust" src="../images/%s" alt="%s"/>'
                                   % (self.images[key], esc(caption)), kind='image'))

    # --- 行の出力 ------------------------------------------------------------

    def render_line(self, units, ctx):
        """Unit 列を 1 行分の XHTML にする。(html, 見出しレベル, 見出しラベル)"""
        blk = ctx.snapshot or self.block
        indent = ctx.indent or blk.indent
        align = ctx.align if ctx.align is not None else blk.align
        heading = ctx.heading or blk.heading or max((u.heading for u in units), default=0)
        classes = list(blk.classes.values())
        styles = []
        if indent:
            first, rest = indent
            if rest:
                styles.append('%s: %dem' % ('margin-top' if self.vertical else 'margin-left', rest))
            if first != rest:
                styles.append('text-indent: %dem' % (first - rest))
        if align is not None:
            classes.append('chitsuki')
            if align:
                styles.append('%s: %dem' % ('margin-bottom' if self.vertical else 'margin-right', align))
        if units and all(u.kind in ('image', 'open', 'close') or not u.plain.strip() for u in units) \
                and any(u.kind == 'image' for u in units):
            classes.append('illust')
        inner = ''.join(u.html for u in units)
        attrs = ''
        label = None
        if heading:
            tag = 'h%d' % (heading + 1)
            self.heading_count += 1
            anchor = 'h%d' % self.heading_count
            attrs += ' id="%s"' % anchor
            label = ctx.heading_label or strip_markup(''.join(u.plain for u in units)).strip(' 　\t')
            label = (anchor, label or '（見出し）')
        else:
            tag = 'p'
        if classes:
            attrs += ' class="%s"' % ' '.join(classes)
        if styles:
            attrs += ' style="%s"' % '; '.join(styles)
        return '<%s%s>%s</%s>' % (tag, attrs, inner, tag), heading, label

    def convert_lines(self, lines):
        """本文の行リストを Page のリストに変換する。"""
        pages = [Page()]

        def new_page():
            cur = pages[-1]
            while cur.lines and cur.lines[-1] == BLANK_LINE:
                cur.lines.pop()
            if cur.has_content:
                pages.append(Page())
            else:
                cur.lines.clear()
                cur.size = 0

        for raw in lines:
            text = replace_kunoji(raw.rstrip('\r\n'))
            ctx = LineContext()
            units = self.parse_line(text, ctx)
            if ctx.break_before:
                new_page()
            page = pages[-1]
            if not text.strip():
                if page.has_content:
                    if page.size > self.SOFT_PAGE_LIMIT:
                        new_page()
                    else:
                        page.lines.append(BLANK_LINE)
            elif any(u.kind not in ('open', 'close') for u in units):
                line, level, label = self.render_line(units, ctx)
                if page.size > self.HARD_PAGE_LIMIT or (level and page.size > self.SOFT_PAGE_LIMIT):
                    new_page()
                    page = pages[-1]
                if level:
                    page.headings.append((level, label[0], label[1]))
                page.lines.append(line)
                page.size += len(line.encode('utf-8'))
                page.has_content = True
            if ctx.break_after:
                new_page()
        new_page()
        return [p for p in pages if p.has_content]


BLANK_LINE = '<p><br/></p>'


# ---------------------------------------------------------------------------
# 文書全体の解析
# ---------------------------------------------------------------------------

CONTRIB_RE = re.compile(r'(訳|翻訳|共訳|編|編纂|編集|校訂|校注|注釈|訳注|編訳|監修|選|補|口述|画|絵|解説)$')


class Book:
    def __init__(self):
        self.title = ''
        self.subtitle = ''
        self.author = ''
        self.contributors = []
        self.header_lines = []
        self.pages = []
        self.colophon_lines = []
        self.images = {}
        self.cover = None
        self.vertical = True
        self.source = None
        self.warnings = []


def read_text(path, encoding=None):
    data = Path(path).read_bytes()
    if encoding:
        return data.decode(encoding, errors='replace')
    if data.startswith(b'\xef\xbb\xbf'):
        return data[3:].decode('utf-8', errors='replace')
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16', errors='replace')
    for enc in ('utf-8', 'cp932', 'shift_jis_2004', 'euc_jis_2004'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode('cp932', errors='replace')


RE_DASHES = re.compile(r'^-{10,}\s*$')
RE_SOKOHON = re.compile(r'^底本[：:]')


def looks_like_body(line, first=False):
    """表題・著者名の行ではなく本文らしい行か。"""
    s = strip_markup(line).rstrip()
    if s[:1] in ('　', ' ', '\t') or s.endswith('。') or len(s) > 40:
        return True
    # 1 行目 (作品名) は「いき」の構造 のように括弧で始まることがある
    return not first and s[:1] in ('「', '『')


def has_text(lines):
    return any(strip_markup(l).strip(' 　\t') for l in lines)


def split_document(lines):
    """(表題行, 本文行, 底本情報行) に分ける。"""
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    start = i
    # 表題は冒頭の空行までの数行。空行を挟まずに本文が始まる場合は本文らしい行の手前まで
    while i < len(lines) and lines[i].strip() and not looks_like_body(lines[i], i == start):
        i += 1
    header = lines[start:i]
    if not header or len(header) > 6 or any(RE_DASHES.match(h) for h in header) \
            or any(len(strip_markup(h)) > 80 for h in header):
        header, i = [], start

    # 【テキスト中に現れる記号について】のブロックを飛ばす (本文中の区切り線は残す)
    j = i
    while j < len(lines) and not lines[j].strip():
        j += 1
    if j < len(lines) and RE_DASHES.match(lines[j]):
        k = j + 1
        while k < len(lines) and k - j < 80 and not RE_DASHES.match(lines[k]):
            k += 1
        if k < len(lines) and RE_DASHES.match(lines[k]) and \
                any('記号について' in l or '：ルビ' in l for l in lines[j:k]):
            i = k + 1

    # 底本情報は通常は末尾。本文より前にある場合は、その段落だけを底本情報に回す
    candidates = [k for k in range(i, len(lines)) if RE_SOKOHON.match(lines[k])]
    front = []
    if candidates and not has_text(lines[i:candidates[0]]):
        e = candidates[0]
        while e < len(lines) and lines[e].strip():
            e += 1
        front, i = lines[candidates[0]:e], e
    footer = len(lines)
    for k in candidates:
        if k >= i and has_text(lines[i:k]):
            footer = k
            break
    back = lines[footer:]
    return header, lines[i:footer], front + ([''] if front and back else []) + back


def parse_header(header, fallback_title):
    plain = [strip_markup(h).strip(' 　') for h in header]
    if not plain:
        return fallback_title, '', '', []
    contributors = [p for p in plain[1:] if CONTRIB_RE.search(p)]
    rest = [p for idx, p in enumerate(plain) if idx == 0 or p not in contributors]
    title = rest[0]
    author = rest[-1] if len(rest) >= 2 else ''
    subtitle = ' '.join(rest[1:-1])
    if not author and contributors:
        author = contributors[0]
        contributors = contributors[1:]
    return title, subtitle, author, contributors


def convert_text(text, base_dir, name, vertical=True, auto_tcy=True):
    """青空文庫形式のテキストを解析して Book を返す。"""
    lines = text.splitlines()
    header, body, footer = split_document(lines)
    book = Book()
    book.vertical = vertical
    book.title, book.subtitle, book.author, book.contributors = parse_header(header, name)
    conv = Converter(base_dir, vertical=vertical, auto_tcy=auto_tcy)

    # 扉
    title_html = []
    for idx, h in enumerate(header):
        units = conv.parse_line(replace_kunoji(h.strip()), LineContext())
        conv.reset()
        plain = strip_markup(h).strip(' 　')
        if idx == 0:
            role, tag = 'title', 'h1'
        elif plain in book.contributors:
            role, tag = 'contrib', 'p'
        elif plain == book.author:
            role, tag = 'author', 'p'
        else:
            role, tag = 'subtitle', 'p'
        title_html.append('<%s class="%s">%s</%s>' % (tag, role, ''.join(u.html for u in units), tag))
    if not title_html:
        title_html.append('<h1 class="title">%s</h1>' % esc(book.title))
    book.header_lines = title_html

    book.pages = conv.convert_lines(body)
    conv.reset()
    colophon = Converter(base_dir, vertical=vertical, auto_tcy=auto_tcy)
    colophon.images = conv.images
    colophon.heading_count = conv.heading_count
    book.colophon_lines = []
    for p in colophon.convert_lines(footer):
        book.colophon_lines.extend(p.lines)
    book.images = conv.images
    book.warnings = conv.warnings + colophon.warnings
    if not book.pages:
        book.warnings.insert(0, '本文が見つかりませんでした。テキストの形式を確認してください')
    return book


# ---------------------------------------------------------------------------
# EPUB 出力
# ---------------------------------------------------------------------------

def build_css(vertical):
    common = '''@charset "UTF-8";
body { margin: 0; padding: 0; font-family: serif; line-height: 1.75; text-align: justify; }
p { margin: 0; padding: 0; text-indent: 0; }
h1, h2, h3, h4 { font-weight: bold; text-indent: 0; padding: 0; line-height: 1.5; }
h2 { font-size: 1.4em; }
h3 { font-size: 1.2em; }
h4 { font-size: 1.05em; }
rt { font-size: 0.5em; }
.b { font-weight: bold; }
.i { font-style: italic; }
.kogaki { font-size: 0.75em; }
.sup { font-size: 0.7em; vertical-align: super; }
.sub { font-size: 0.7em; vertical-align: sub; }
.kei { border: 1px solid; padding: 0 0.1em; }
.warichu { font-size: 0.7em; }
.caption { font-size: 0.85em; }
.gaiji-note { font-size: 0.75em; }
.font-larger1 { font-size: 1.2em; }
.font-larger2 { font-size: 1.44em; }
.font-larger3 { font-size: 1.73em; }
.font-smaller1 { font-size: 0.83em; }
.font-smaller2 { font-size: 0.69em; }
.font-smaller3 { font-size: 0.58em; }
.chitsuki { text-align: right; text-align: end; }
.illust { text-align: center; text-indent: 0; }
img.illust { max-width: 100%; max-height: 100%; }
.colophon p { font-size: 0.85em; }
.cover-page { margin: 0; padding: 0; }
.cover { margin: 0; padding: 0; text-align: center; }
.cover img { max-width: 100%; max-height: 100%; }
'''
    emph = {
        'em-sesame': 'filled sesame', 'em-sesame-open': 'open sesame',
        'em-circle': 'filled circle', 'em-circle-open': 'open circle',
        'em-triangle': 'filled triangle', 'em-triangle-open': 'open triangle',
        'em-double-circle': 'filled double-circle', 'em-double-circle-open': 'open double-circle',
        'em-x': '"×"',
    }
    for cls, val in emph.items():
        common += ('.%s { -webkit-text-emphasis-style: %s; -epub-text-emphasis-style: %s; '
                   'text-emphasis-style: %s; }\n' % (cls, val, val, val))
    line = 'overline' if vertical else 'underline'
    for cls, style in (('sl-solid', 'solid'), ('sl-double', 'double'), ('sl-dotted', 'dotted'),
                       ('sl-dashed', 'dashed'), ('sl-wavy', 'wavy')):
        common += ('.%s { text-decoration: %s; -webkit-text-decoration-style: %s; '
                   'text-decoration-style: %s; }\n' % (cls, line, style, style))
    if vertical:
        return common + '''html { -epub-writing-mode: vertical-rl; -webkit-writing-mode: vertical-rl; writing-mode: vertical-rl; }
h1, h2, h3, h4 { margin: 0 0.5em; }
.tcy { -webkit-text-combine: horizontal; -epub-text-combine: horizontal; text-combine-upright: all; }
.titlepage h1.title { font-size: 1.6em; margin: 0 2em 0 1em; padding-top: 2em; }
.titlepage p.subtitle { font-size: 1.2em; padding-top: 3em; margin: 0 0 0 1em; }
.titlepage p.author, .titlepage p.contrib { font-size: 1.1em; padding-top: 6em; margin: 0 1em 0 0; }
'''
    return common + '''html { writing-mode: horizontal-tb; }
h1, h2, h3, h4 { margin: 0.5em 0; }
.titlepage h1.title { font-size: 1.6em; margin: 2em 0 1em 0; text-align: center; }
.titlepage p.subtitle { font-size: 1.2em; text-align: center; }
.titlepage p.author, .titlepage p.contrib { font-size: 1.1em; text-align: center; margin-top: 1em; }
'''


def xhtml(title, body, vertical, body_class=None):
    cls = ' class="%s"' % body_class if body_class else ''
    return '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="ja" lang="ja" class="%s">
<head>
<meta charset="UTF-8"/>
<title>%s</title>
<link rel="stylesheet" type="text/css" href="../style/book.css"/>
</head>
<body%s>
%s
</body>
</html>
''' % ('vrtl' if vertical else 'hltr', esc(title), cls, body)


def toc_tree(entries):
    root = {'level': 0, 'children': []}
    stack = [root]
    for level, label, href in entries:
        node = {'level': level, 'label': label, 'href': href, 'children': []}
        while stack[-1]['level'] >= level:
            stack.pop()
        stack[-1]['children'].append(node)
        stack.append(node)
    return root['children']


def nav_ol(nodes, indent=''):
    out = [indent + '<ol>']
    for n in nodes:
        li = '%s  <li><a href="%s">%s</a>' % (indent, esc(n['href']), esc(n['label']))
        if n['children']:
            out.append(li)
            out.append(nav_ol(n['children'], indent + '    '))
            out.append(indent + '  </li>')
        else:
            out.append(li + '</li>')
    out.append(indent + '</ol>')
    return '\n'.join(out)


def ncx_points(nodes, counter, indent='    '):
    out = []
    for n in nodes:
        counter[0] += 1
        out.append('%s<navPoint id="np%d" playOrder="%d"><navLabel><text>%s</text></navLabel>'
                   '<content src="%s"/>' % (indent, counter[0], counter[0], esc(n['label']),
                                            esc(n['href'])))
        if n['children']:
            out.append(ncx_points(n['children'], counter, indent + '  '))
        out.append(indent + '</navPoint>')
    return '\n'.join(out)


def tree_depth(nodes):
    return max((1 + tree_depth(n['children']) for n in nodes), default=0)


def write_epub(book, out_path):
    vertical = book.vertical
    full_title = book.title + (' ' + book.subtitle if book.subtitle else '')
    files = []   # (EPUB 内パス, bytes)
    manifest = []
    spine = []

    def add_xhtml(item_id, name, title, body, body_class=None, in_spine=True, props=None):
        files.append(('OEBPS/text/' + name, xhtml(title, body, vertical, body_class).encode('utf-8')))
        manifest.append('<item id="%s" href="text/%s" media-type="application/xhtml+xml"%s/>'
                        % (item_id, name, ' properties="%s"' % props if props else ''))
        if in_spine:
            spine.append('<itemref idref="%s"/>' % item_id)

    cover_meta = ''
    if book.cover:
        ext = Path(book.cover).suffix.lower()
        cover_name = 'cover' + ('.jpg' if ext == '.jpeg' else ext)
        files.append(('OEBPS/images/' + cover_name, Path(book.cover).read_bytes()))
        manifest.append('<item id="cover" href="images/%s" media-type="%s" properties="cover-image"/>'
                        % (cover_name, MEDIA_TYPES[ext]))
        cover_meta = '\n    <meta name="cover" content="cover"/>'
        add_xhtml('coverpage', 'cover.xhtml', full_title,
                  '<div class="cover"><img src="../images/%s" alt="表紙"/></div>' % cover_name,
                  'cover-page')

    add_xhtml('titlepage', 'title.xhtml', full_title,
              '<div class="titlepage">\n%s\n</div>' % '\n'.join(book.header_lines), 'titlepage')

    entries = []
    for idx, page in enumerate(book.pages, 1):
        page.filename = 'p%04d.xhtml' % idx
        add_xhtml('p%04d' % idx, page.filename, full_title, '\n'.join(page.lines), 'main')
        for level, anchor, label in page.headings:
            entries.append((level, label, 'text/%s#%s' % (page.filename, anchor)))
    if book.colophon_lines:
        add_xhtml('colophon', 'colophon.xhtml', full_title,
                  '<div class="colophon">\n%s\n</div>' % '\n'.join(book.colophon_lines), 'colophon')

    if entries:
        top = min(e[0] for e in entries)
        entries = [(lvl - top + 1, label, href) for lvl, label, href in entries]
    nodes = [{'level': 1, 'label': '扉', 'href': 'text/title.xhtml', 'children': []}]
    if not entries and book.pages:
        nodes.append({'level': 1, 'label': '本文', 'href': 'text/p0001.xhtml', 'children': []})
    nodes += toc_tree(entries)
    if book.colophon_lines:
        nodes.append({'level': 1, 'label': '底本情報', 'href': 'text/colophon.xhtml', 'children': []})

    # nav.xhtml は text/ 内にあるのでリンクの text/ を外す
    def local(ns):
        return [dict(n, href=n['href'][5:], children=local(n['children'])) for n in ns]

    body_start = 'p0001.xhtml' if book.pages else 'title.xhtml'
    nav_body = ('<nav epub:type="toc" id="toc">\n<h1>目次</h1>\n%s\n</nav>\n'
                '<nav epub:type="landmarks" id="landmarks" hidden="">\n<ol>\n'
                '  <li><a epub:type="toc" href="nav.xhtml">目次</a></li>\n'
                '%s  <li><a epub:type="bodymatter" href="%s">本文</a></li>\n</ol>\n</nav>'
                % (nav_ol(local(nodes)),
                   '  <li><a epub:type="cover" href="cover.xhtml">表紙</a></li>\n' if book.cover else '',
                   body_start))
    files.append(('OEBPS/text/nav.xhtml', xhtml('目次', nav_body, False).encode('utf-8')))
    manifest.append('<item id="nav" href="text/nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
    spine.append('<itemref idref="nav" linear="no"/>')

    book_id = 'urn:uuid:%s' % uuid.uuid5(uuid.NAMESPACE_URL, 'aozora2epub:%s:%s:%s' % (
        full_title, book.author, book.source or ''))
    ncx = '''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="ja">
  <head>
    <meta name="dtb:uid" content="%s"/>
    <meta name="dtb:depth" content="%d"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>%s</text></docTitle>
  <docAuthor><text>%s</text></docAuthor>
  <navMap>
%s
  </navMap>
</ncx>
''' % (book_id, tree_depth(nodes), esc(full_title), esc(book.author), ncx_points(nodes, [0]))
    files.append(('OEBPS/toc.ncx', ncx.encode('utf-8')))
    manifest.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')

    files.append(('OEBPS/style/book.css', build_css(vertical).encode('utf-8')))
    manifest.append('<item id="css" href="style/book.css" media-type="text/css"/>')

    for src, name in book.images.items():
        files.append(('OEBPS/images/' + name, Path(src).read_bytes()))
        manifest.append('<item id="%s" href="images/%s" media-type="%s"/>'
                        % (name.replace('.', '_'), name, MEDIA_TYPES[Path(name).suffix]))
    meta = ['<dc:identifier id="BookId">%s</dc:identifier>' % book_id,
            '<dc:title id="title">%s</dc:title>' % esc(full_title),
            '<dc:language>ja</dc:language>']
    if book.author:
        meta += ['<dc:creator id="creator">%s</dc:creator>' % esc(book.author),
                 '<meta refines="#creator" property="role" scheme="marc:relators">aut</meta>']
    for k, cname in enumerate(book.contributors, 1):
        meta.append('<dc:contributor id="contrib%d">%s</dc:contributor>' % (k, esc(cname)))
    meta.append('<meta property="dcterms:modified">%s</meta>'
                % datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    meta.append('<meta name="primary-writing-mode" content="%s"/>'
                % ('vertical-rl' if vertical else 'horizontal-lr'))

    opf = '''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="BookId" xml:lang="ja">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    %s%s
  </metadata>
  <manifest>
    %s
  </manifest>
  <spine toc="ncx"%s>
    %s
  </spine>
</package>
''' % ('\n    '.join(meta), cover_meta, '\n    '.join(manifest),
       ' page-progression-direction="rtl"' if vertical else '', '\n    '.join(spine))
    files.append(('OEBPS/content.opf', opf.encode('utf-8')))

    container = '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
'''
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + '.tmp')
    with zipfile.ZipFile(tmp, 'w') as z:
        z.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', container, compress_type=zipfile.ZIP_DEFLATED)
        for name, data in files:
            z.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    tmp.replace(out_path)


# ---------------------------------------------------------------------------
# ファイル単位・一括変換
# ---------------------------------------------------------------------------

IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.gif')


def parse_filename(stem):
    """「著者名-タイトル(補足)」形式のファイル名から (著者名, タイトル) を取り出す。

    区切りは半角の「-」と「(」「[」(全角は区切りとみなさない)。
    最初の「-」より前が著者名、その後ろから「(」「[」の手前までがタイトル。
    「-」が無いファイル名はこの形式ではないとみなし (None, None) を返す。
    """
    if '-' not in stem:
        return None, None
    author, rest = stem.split('-', 1)
    author = re.split(r'[(\[]', author)[0].strip(' _　')
    title = re.split(r'[(\[]', rest)[0].strip(' _　')
    return author or None, title or None


class CoverFinder:
    """作品ファイルと同じ名前 (拡張子違い) の画像を探す。大文字小文字は区別しない。"""

    def __init__(self):
        self._cache = {}

    def _index(self, directory, recursive):
        key = (str(Path(directory).resolve()), recursive)
        if key not in self._cache:
            idx = {}
            it = Path(directory).rglob('*') if recursive else Path(directory).glob('*')
            # 浅い階層・名前順で先に見つかったものを優先
            for f in sorted(it, key=lambda x: (len(x.parts), str(x))):
                if f.suffix.lower() in IMAGE_EXTS and f.is_file():
                    idx.setdefault(f.stem.lower(), f)
            self._cache[key] = idx
        return self._cache[key]

    def find(self, stems, near, root=None, recursive=True):
        """near (作品と同じフォルダ) を先に探し、無ければ root 以下を探す。"""
        for directory, rec in ((near, False), (root, recursive)):
            if directory is None:
                continue
            idx = self._index(directory, rec)
            for stem in stems:
                if stem.lower() in idx:
                    return idx[stem.lower()]
        return None


def safe_filename(s):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', s).strip(' .')
    return s[:120] or 'book'


def title_page_lines(book):
    lines = ['<h1 class="title">%s</h1>' % esc(book.title)]
    if book.subtitle:
        lines.append('<p class="subtitle">%s</p>' % esc(book.subtitle))
    if book.author:
        lines.append('<p class="author">%s</p>' % esc(book.author))
    for c in book.contributors:
        lines.append('<p class="contrib">%s</p>' % esc(c))
    return lines


def apply_filename_metadata(book, stem):
    """ファイル名から取得した著者名・タイトルで上書きする。"""
    author, title = parse_filename(stem)
    if not (author or title):
        return False
    if title:
        if title != book.title:
            book.subtitle = ''
        book.title = title
    if author:
        if author in book.contributors:
            book.contributors.remove(author)
        book.author = author
    book.header_lines = title_page_lines(book)
    return True


def convert_file(txt_path, out_dir, opts, out_stem=None, name_stem=None, cover=None):
    """1 つのテキストファイルを変換し、(出力した EPUB のパス, Book) を返す。"""
    txt_path = Path(txt_path)
    text = read_text(txt_path, opts.encoding)
    book = convert_text(text, txt_path.parent, txt_path.stem,
                        vertical=opts.writing != 'yoko', auto_tcy=not opts.no_auto_tcy)
    book.source = txt_path.name
    book.cover = cover
    if opts.name_from_filename:
        apply_filename_metadata(book, name_stem or txt_path.stem)
    if opts.title_filename:
        name = ('[%s] ' % book.author if book.author else '') + book.title + \
               (' ' + book.subtitle if book.subtitle else '')
        stem = safe_filename(name)
        # 同じ名前になる別の作品 (上巻・下巻など) を上書きしないよう、元のファイル名に戻す
        written = getattr(opts, 'written', None)
        if written is not None and str(Path(out_dir) / (stem + '.epub')) in written:
            book.warnings.append('出力名「%s.epub」が他の作品と重なるため、元のファイル名で出力しました' % stem)
            stem = out_stem or txt_path.stem
    else:
        stem = out_stem or txt_path.stem
    out_path = Path(out_dir) / (stem + '.epub')
    if getattr(opts, 'written', None) is not None:
        opts.written.add(str(out_path))
    write_epub(book, out_path)
    return out_path, book


def zip_member_name(info):
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode('cp437').decode('cp932')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def convert_zip(zip_path, out_dir, opts, covers, root=None):
    zip_path = Path(zip_path)
    results = []
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(zip_path) as z:
        tmp = Path(tmp)
        for info in z.infolist():
            if info.is_dir():
                continue
            name = zip_member_name(info)
            dest = (tmp / name).resolve()
            if tmp.resolve() not in dest.parents:
                continue   # 不正なパスは展開しない
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(z.read(info))
        txts = sorted(p for p in tmp.rglob('*') if p.is_file() and p.suffix.lower() == '.txt')
        for t in txts:
            single = len(txts) == 1
            stems = [zip_path.stem, t.stem] if single else [t.stem]
            cover = (covers.find(stems, zip_path.parent, root, not opts.no_recursive)
                     or covers.find(stems, t.parent))
            name_stem = zip_path.stem if single and parse_filename(zip_path.stem)[1] else t.stem
            results.append(convert_file(t, out_dir, opts, out_stem=zip_path.stem if single else t.stem,
                                        name_stem=name_stem, cover=cover))
    return results


def iter_inputs(paths, recursive):
    """(探索の起点ディレクトリ or None, ファイル) を返す。"""
    for p in paths:
        p = Path(p)
        if p.is_dir():
            it = p.rglob('*') if recursive else p.glob('*')
            for f in sorted(it):
                if f.is_file() and f.suffix.lower() in ('.txt', '.zip'):
                    yield p, f
        elif p.is_file():
            yield None, p
        else:
            print('見つかりません: %s' % p, file=sys.stderr)


def ask(prompt, default=''):
    try:
        ans = input(prompt).strip()
    except EOFError:
        ans = ''
    # ドラッグ＆ドロップで入力されたパスの引用符を外す
    return ans.strip('"\'') or default


def ask_yes_no(prompt, default=False):
    ans = ask('%s [%s]: ' % (prompt, 'Y/n' if default else 'y/N')).lower()
    if not ans:
        return default
    return ans in ('y', 'yes', 'はい', 'ｙ')


def interactive_setup(opts):
    """足りない設定を対話形式で尋ねる。"""
    here = Path(__file__).resolve().parent
    print('=== 青空文庫テキスト → EPUB 変換 ===')
    default_out = None
    if not opts.inputs:
        default_in = here / 'input'
        path = ask('変換するフォルダ (またはファイル) を入力してください。ドラッグ＆ドロップもできます。\n'
                   '  [Enter で %s]: ' % default_in, str(default_in))
        opts.inputs = [path]
        if Path(path) == default_in:
            default_in.mkdir(exist_ok=True)
            default_out = here / 'output'
    if opts.output is None:
        if default_out:
            ans = ask('出力先フォルダ ("-" で元ファイルと同じ場所)\n  [Enter で %s]: ' % default_out,
                      str(default_out))
        else:
            ans = ask('出力先フォルダ\n  [Enter で元ファイルと同じ場所]: ', '-')
        opts.output = None if ans == '-' else ans
    if opts.writing is None:
        ans = ask('書字方向を選んでください  1) 縦書き  2) 横書き  [Enter で 1]: ', '1')
        opts.writing = 'yoko' if ans in ('2', '２', 'yoko', '横', '横書き') else 'tate'
    if opts.name_from_filename is None:
        opts.name_from_filename = ask_yes_no(
            'ファイル名 (著者名-タイトル(…).txt) から著者名とタイトルを取得しますか？')
    if opts.force is None:
        opts.force = ask_yes_no('変換済みのファイルも変換し直しますか？')
    print()


def build_parser():
    ap = argparse.ArgumentParser(
        description='青空文庫形式のテキスト (.txt / .zip) を Kindle 向け EPUB3 に一括変換します。'
                    '引数なしで実行すると対話形式で設定を尋ねます。')
    ap.add_argument('inputs', nargs='*', metavar='入力',
                    help='変換するファイル、またはディレクトリ (配下の .txt/.zip をすべて変換)')
    ap.add_argument('-o', '--output', metavar='DIR',
                    help='出力先ディレクトリ (省略時は元ファイルと同じ場所。ディレクトリ構成は保持)')
    wm = ap.add_mutually_exclusive_group()
    wm.add_argument('-w', '--writing-mode', dest='writing', choices=['tate', 'yoko'],
                    help='書字方向: tate=縦書き (既定) / yoko=横書き')
    wm.add_argument('--tate', '--vertical', dest='writing', action='store_const', const='tate',
                    help='縦書きで出力する (-w tate と同じ)')
    wm.add_argument('--yoko', '--horizontal', dest='writing', action='store_const', const='yoko',
                    help='横書きで出力する (-w yoko と同じ)')
    ap.add_argument('-n', '--name-from-filename', action='store_true', default=None,
                    help='ファイル名「著者名-タイトル(…).txt」から著者名とタイトルを取得する '
                         '(半角の - と ( が区切り)')
    ap.add_argument('--title-filename', action='store_true',
                    help='出力ファイル名を「[著者] 作品名.epub」にする (既定は元ファイル名)')
    ap.add_argument('--no-auto-tcy', action='store_true',
                    help='縦書き時に 2 桁までの半角数字や「!?」を自動で縦中横にしない')
    ap.add_argument('--encoding', help='入力の文字コードを指定 (既定は自動判定: UTF-8 / Shift_JIS)')
    ap.add_argument('--no-recursive', action='store_true', help='サブディレクトリを検索しない')
    ap.add_argument('-f', '--force', action='store_true', default=None,
                    help='EPUB が既にあって元ファイルより新しい場合も変換し直す')
    ap.add_argument('-i', '--interactive', action='store_true',
                    help='指定しなかった設定を対話形式で尋ねる')
    ap.add_argument('-q', '--quiet', action='store_true', help='警告を表示しない')
    ap.add_argument('--version', action='version', version='%(prog)s ' + __version__)
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')
        except AttributeError:
            pass
    opts = build_parser().parse_args(argv)
    interactive = opts.interactive or not opts.inputs
    if interactive:
        interactive_setup(opts)
    opts.writing = opts.writing or 'tate'
    opts.force = bool(opts.force)
    opts.name_from_filename = bool(opts.name_from_filename)
    try:
        return run(opts)
    finally:
        if interactive:
            ask('\nEnter キーを押すと終了します。')


def run(opts):
    targets = list(iter_inputs(opts.inputs, not opts.no_recursive))
    if not targets:
        print('変換対象のファイル (.txt / .zip) が見つかりませんでした: %s' % ', '.join(opts.inputs))
        return 1
    print('書字方向: %s / ファイル名から著者名・タイトルを取得: %s'
          % ('縦書き' if opts.writing == 'tate' else '横書き',
             'する' if opts.name_from_filename else 'しない'))
    covers = CoverFinder()
    opts.written = set()
    recursive = not opts.no_recursive
    ok = skipped = failed = 0
    total = len(targets)
    for k, (root, f) in enumerate(targets, 1):
        base = f.parent if root is None else root
        out_dir = Path(opts.output) / f.parent.relative_to(base) if opts.output else f.parent
        prefix = '[%d/%d] %s' % (k, total, f)
        is_zip = f.suffix.lower() == '.zip'
        cover = covers.find([f.stem], f.parent, root, recursive)
        if not opts.force and not opts.title_filename:
            existing = out_dir / (f.stem + '.epub')
            newest = max(f.stat().st_mtime, cover.stat().st_mtime if cover else 0)
            if existing.is_file() and existing.stat().st_mtime >= newest:
                print('%s -> スキップ (変換済み。変換し直すには -f)' % prefix)
                skipped += 1
                continue
        try:
            if is_zip:
                results = convert_zip(f, out_dir, opts, covers, root)
                if not results:
                    print('%s -> zip 内にテキストファイルがありません' % prefix)
                    skipped += 1
                    continue
            else:
                results = [convert_file(f, out_dir, opts, cover=cover)]
        except Exception as e:   # 1 ファイルの失敗で全体を止めない
            print('%s -> 失敗: %s: %s' % (prefix, type(e).__name__, e), file=sys.stderr)
            failed += 1
            continue
        for out_path, book in results:
            print('%s -> %s  (%s / %s%s)' % (prefix, out_path, book.title, book.author or '著者不明',
                                            ' / 表紙: %s' % book.cover.name if book.cover else ''))
            if not opts.quiet:
                for w in book.warnings[:10]:
                    print('    警告: %s' % w)
                if len(book.warnings) > 10:
                    print('    警告: ほか %d 件' % (len(book.warnings) - 10))
        ok += 1
    print('完了: 変換 %d 件 / スキップ %d 件 / 失敗 %d 件' % (ok, skipped, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
