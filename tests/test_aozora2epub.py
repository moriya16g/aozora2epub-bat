import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aozora2epub as a2e  # noqa: E402


def render(text, vertical=True):
    conv = a2e.Converter('.', vertical=vertical)
    pages = conv.convert_lines(text.split('\n'))
    return '\n'.join(line for p in pages for line in p.lines), pages


class InlineTest(unittest.TestCase):
    def test_ruby_implicit_and_bar(self):
        out, _ = render('吾輩《わがはい》は｜見当《けんとう》')
        self.assertIn('<ruby>吾輩<rp>（</rp><rt>わがはい</rt><rp>）</rp></ruby>は', out)
        self.assertIn('<ruby>見当<rp>（</rp><rt>けんとう</rt>', out)

    def test_ruby_base_stops_at_kana(self):
        out, _ = render('ある日本人《にほんじん》')
        self.assertIn('ある<ruby>日本人<rp>', out)

    def test_gaiji(self):
        out, _ = render('※［＃「木＋吶のつくり」、第3水準1-85-54］と※［＃「口＋世」、U+546D、123-4］')
        self.assertIn('枘と呭', out)

    def test_unknown_gaiji_keeps_description(self):
        out, _ = render('※［＃「土へん＋竒」、94-13］')
        self.assertIn('※（土へん＋竒）', out)

    def test_forward_reference_emphasis_with_ruby(self):
        out, _ = render('吾輩《わがはい》は猫である［＃「吾輩は猫」に傍点］')
        self.assertIn('<span class="em-sesame"><ruby>吾輩', out)
        self.assertIn('</ruby>は猫</span>である', out)

    def test_ranges_carry_over_lines_and_nest(self):
        out, _ = render('［＃太字］一行目［＃傍点］まだ\n続き［＃太字終わり］です［＃傍点終わり］')
        self.assertIn('<p><span class="b">一行目<span class="em-sesame">まだ</span></span></p>', out)
        self.assertIn('<p><span class="b"><span class="em-sesame">続き</span></span>'
                      '<span class="em-sesame">です</span></p>', out)

    def test_auto_tcy(self):
        out, _ = render('12月2017年!?と！！')
        self.assertIn('<span class="tcy">12</span>月2017年', out)
        self.assertIn('<span class="tcy">!?</span>と<span class="tcy">!!</span>', out)
        out, _ = render('12月', vertical=False)
        self.assertNotIn('tcy', out)

    def test_escape(self):
        out, _ = render('<a>&')
        self.assertIn('&lt;a&gt;&amp;', out)

    def test_kunoji(self):
        out, _ = render('わいわい／＼がや／″＼')
        self.assertIn('わいわい〳〵がや〴〵', out)

    def test_editorial_notes_are_removed(self):
        out, _ = render('山田［＃「山田」はママ］と［＃未知の注記］')
        self.assertIn('<p>山田と</p>', out)


class BlockTest(unittest.TestCase):
    def test_indent_and_align(self):
        out, _ = render('［＃３字下げ］見出し\n［＃ここから２字下げ、折り返して４字下げ］\n本文\n'
                        '［＃ここで字下げ終わり］\n［＃地から２字上げ］署名')
        self.assertIn('<p style="margin-top: 3em">見出し</p>', out)
        self.assertIn('<p style="margin-top: 4em; text-indent: -2em">本文</p>', out)
        self.assertIn('<p class="chitsuki" style="margin-bottom: 2em">署名</p>', out)

    def test_horizontal_indent(self):
        out, _ = render('［＃３字下げ］見出し', vertical=False)
        self.assertIn('margin-left: 3em', out)

    def test_headings(self):
        out, pages = render('［＃５字下げ］第一章［＃「第一章」は中見出し］\n'
                            '［＃大見出し］序［＃大見出し終わり］')
        self.assertIn('<h3 id="h1" style="margin-top: 5em">第一章</h3>', out)
        self.assertIn('<h2 id="h2">序</h2>', out)
        self.assertEqual(pages[0].headings, [(2, 'h1', '第一章'), (1, 'h2', '序')])

    def test_page_break(self):
        _, pages = render('一ページ目\n\n［＃改ページ］\n\n二ページ目\n［＃改ページ］\n')
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0].lines, ['<p>一ページ目</p>'])
        self.assertEqual(pages[1].lines, ['<p>二ページ目</p>'])


SAMPLE = '''作品名
副題
著者名
訳者名訳

-------------------------------------------------------
【テキスト中に現れる記号について】

《》：ルビ
-------------------------------------------------------

［＃中見出し］一［＃中見出し終わり］
　本文《ほんぶん》です。
［＃挿絵（fig.png）入る］

底本：「作品名」テスト文庫
入力：テスト
'''

PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00'
       b'\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92'
       b'\xef\x00\x00\x00\x00IEND\xaeB`\x82')


class DocumentTest(unittest.TestCase):
    def test_header_split(self):
        header, body, footer = a2e.split_document(SAMPLE.split('\n'))
        self.assertEqual(header, ['作品名', '副題', '著者名', '訳者名訳'])
        self.assertEqual(body[1], '［＃中見出し］一［＃中見出し終わり］')
        self.assertTrue(footer[0].startswith('底本：'))
        self.assertEqual(a2e.parse_header(header, 'x'), ('作品名', '副題', '著者名', ['訳者名訳']))

    def test_no_header(self):
        text = '　いきなり本文が始まる長い文章です。' * 10
        book = a2e.convert_text(text, '.', 'fallback')
        self.assertEqual(book.title, 'fallback')

    def test_batch_conversion(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / 'in' / 'sub'
            src.mkdir(parents=True)
            (src / 'book.txt').write_bytes(SAMPLE.replace('\n', '\r\n').encode('cp932'))
            (src / 'fig.png').write_bytes(PNG)
            with zipfile.ZipFile(tmp / 'in' / 'z.zip', 'w') as z:
                z.writestr('inner.txt', 'ゾーン\n著者\n\n本文\n'.encode('cp932'))
            out = tmp / 'out'
            self.assertEqual(a2e.main([str(tmp / 'in'), '-o', str(out), '-q']), 0)
            epub = out / 'sub' / 'book.epub'
            self.assertTrue(epub.is_file())
            self.assertTrue((out / 'z.epub').is_file())
            with zipfile.ZipFile(epub) as z:
                names = z.namelist()
                self.assertEqual(names[0], 'mimetype')
                self.assertEqual(z.getinfo('mimetype').compress_type, zipfile.ZIP_STORED)
                self.assertIn('OEBPS/images/img0001.png', names)
                opf = z.read('OEBPS/content.opf').decode('utf-8')
                self.assertIn('<dc:title id="title">作品名 副題</dc:title>', opf)
                self.assertIn('<dc:creator id="creator">著者名</dc:creator>', opf)
                self.assertIn('page-progression-direction="rtl"', opf)
                page = z.read('OEBPS/text/p0001.xhtml').decode('utf-8')
                self.assertIn('<ruby>本文<rp>', page)
                self.assertNotIn('テキスト中に現れる記号', page)
                self.assertIn('底本：', z.read('OEBPS/text/colophon.xhtml').decode('utf-8'))
            # 2 回目は変換済みとしてスキップされる
            mtime = epub.stat().st_mtime_ns
            a2e.main([str(tmp / 'in'), '-o', str(out), '-q'])
            self.assertEqual(epub.stat().st_mtime_ns, mtime)


if __name__ == '__main__':
    unittest.main()
