import io
import re
import sys
import unittest
from unittest import mock

from tests.helpers import fq

try:
    import qrcode
    from PIL import Image
    import zxingcpp  # noqa: F401
    HAVE_QR = True
except ImportError:
    HAVE_QR = False


class NormalizeTest(unittest.TestCase):
    def test_accepted_forms(self):
        for text in ('FQ-1A2B3C', 'fq-1a2b3c', 'FQ:FQ-1A2B3C', ' 1a2b3c '):
            self.assertEqual(fq.normalize_code(text), 'FQ-1A2B3C')

    def test_rejected_forms(self):
        for text in ('', 'FQ-12345', 'FQ-1234567', 'FQ-GGGGGG', '../etc', 'FQ:XX-123456'):
            with self.assertRaises(ValueError):
                fq.normalize_code(text)


class DecoderAvailabilityTest(unittest.TestCase):
    def test_import_failure_reason_is_reported(self):
        with mock.patch.dict(sys.modules, {'zxingcpp': None}):
            reason = fq.decoder_error()
            self.assertIn('zxingcpp', reason)
            self.assertTrue(reason.startswith('ModuleNotFoundError: '), reason)
            with self.assertRaises(fq.DecodeError) as ctx:
                fq.decode_qr_image(b'not an image')
        self.assertIn('zxingcpp', str(ctx.exception))

    @unittest.skipUnless(HAVE_QR, 'Pillow and zxing-cpp are required')
    def test_available_decoder_has_no_error(self):
        self.assertEqual(fq.decoder_error(), '')


@unittest.skipUnless(HAVE_QR, 'qrcode, Pillow and zxing-cpp are required')
class LabelAndDecodeTest(unittest.TestCase):
    spool = {'code': 'FQ-1A2B3C', 'material': 'PETG', 'brand': '<b>eSUN</b> & co', 'profile': 'Generic "PETG"'}

    def png(self, text, scale=8):
        img = qrcode.make(text, box_size=scale, border=4)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()

    def test_svg_has_label_size_and_escapes_text(self):
        svg = fq.qr_svg(self.spool)
        self.assertIn('width="56mm" height="72mm"', svg)
        self.assertIn('&lt;b&gt;eSUN&lt;/b&gt; &amp; co', svg)
        self.assertNotIn('<b>', svg)
        self.assertNotIn('<script', svg)
        self.assertNotIn('href', svg)

    def test_centre_shows_only_the_code_and_note_is_at_the_bottom(self):
        svg = fq.qr_svg(dict(self.spool, notes='Сушить 4 ч <60°C>'))
        centre = svg.split('<g class="centre">', 1)[1].split('</g>', 1)[0]
        self.assertIn('1A2B3C', centre)
        self.assertNotIn('PETG', centre)
        bottom = svg.split('<g class="bottom">', 1)[1].split('</g>', 1)[0]
        self.assertIn('PETG', bottom)
        self.assertIn('Сушить 4 ч &lt;60°C&gt;', bottom)
        self.assertNotIn('Generic', bottom)
        no_note = fq.qr_svg(self.spool).split('<g class="bottom">', 1)[1].split('</g>', 1)[0]
        self.assertIn('Generic &quot;PETG&quot;', no_note)

    def test_long_note_wraps_between_words(self):
        note = 'Сушить 4 часа при 65°C перед печатью, хранить в боксе с силикагелем и не трогать до печати'
        bottom = fq.qr_svg(dict(self.spool, notes=note)).split('<g class="bottom">', 1)[1]
        lines = re.findall(r'font-size="2.15" fill="#333">([^<]*)</text>', bottom)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith('печатью,'), lines)
        self.assertTrue(lines[1].startswith('хранить'), lines)
        self.assertTrue(lines[1].endswith('…'), lines)

    def test_label_shows_colour_swatch(self):
        svg = fq.qr_svg(dict(self.spool, color='#FF0000'))
        self.assertIn('fill="#FF0000"', svg)
        self.assertIn('#FF0000', svg)
        self.assertNotIn('class="swatch"', fq.qr_svg(self.spool))

    def test_generated_code_round_trips(self):
        self.assertEqual(fq.decode_qr_image(self.png('FQ:FQ-1A2B3C')), ['FQ-1A2B3C'])

    def test_foreign_qr_is_rejected(self):
        with self.assertRaises(fq.DecodeError):
            fq.decode_qr_image(self.png('https://example.com'))

    def test_garbage_and_truncated_images_are_rejected(self):
        with self.assertRaises(fq.DecodeError):
            fq.decode_qr_image(b'not an image')
        with self.assertRaises(fq.DecodeError):
            fq.decode_qr_image(self.png('FQ:FQ-1A2B3C')[:200])

    def test_oversized_payload_is_rejected_before_decoding(self):
        with self.assertRaises(fq.DecodeError):
            fq.decode_qr_image(b'\0' * (fq.MAX_IMAGE_BYTES + 1))

    def test_huge_dimensions_are_rejected_before_decoding(self):
        img = Image.new('1', (fq.MAX_IMAGE_SIDE * 3, fq.MAX_IMAGE_SIDE * 3))
        buf = io.BytesIO()
        img.save(buf, format='PNG', optimize=True)
        self.assertLess(len(buf.getvalue()), fq.MAX_IMAGE_BYTES)
        with self.assertRaises(fq.DecodeError):
            fq.decode_qr_image(buf.getvalue())

    def test_large_jpeg_photo_is_downscaled_and_decoded(self):
        img = qrcode.make('FQ:FQ-ABCDEF', box_size=60, border=4).convert('RGB')
        canvas = Image.new('RGB', (4000, 3000), 'white')
        canvas.paste(img, (200, 200))
        buf = io.BytesIO()
        canvas.save(buf, format='JPEG', quality=90)
        self.assertEqual(fq.decode_qr_image(buf.getvalue()), ['FQ-ABCDEF'])


class PageTest(unittest.TestCase):
    def test_plugin_page_fills_fields_from_the_profile(self):
        self.assertIn("post('profile_info'", fq.PLUGIN_HTML)
        self.assertIn('id="price"', fq.PLUGIN_HTML)
        self.assertIn("price:$('price').value", fq.PLUGIN_HTML)

    def test_plugin_page_has_colour_picker_and_decoder_warning(self):
        self.assertIn('id="color" type="color"', fq.PLUGIN_HTML)
        self.assertIn("color:$('color').value", fq.PLUGIN_HTML)
        self.assertIn('s.decoder_error', fq.PLUGIN_HTML)
        self.assertIn('swatch(', fq.PLUGIN_HTML)

    def test_plugin_page_shows_address_qr_codes(self):
        self.assertIn('s.url_qrs', fq.PLUGIN_HTML)

    def test_phone_page_shows_spool_colour(self):
        self.assertIn('swatch(', fq.PHONE_HTML)


class DependencyPinTest(unittest.TestCase):
    """OrcaSlicer 2.5.0 for Windows ships no python3.dll, so stable-ABI (abi3) wheels cannot load there."""

    def requirements(self, environment):
        from pip._vendor.packaging.requirements import Requirement
        header = fq.__file__ and open(fq.__file__, encoding='utf-8').read().split('# ///', 2)[1]
        specs = re.findall(r'"([^"]+)"', header.split('dependencies', 1)[1].split(']', 1)[0])
        env = {'sys_platform': 'linux', 'platform_machine': 'x86_64', 'python_version': '3.12',
               'python_full_version': '3.12.13', 'os_name': 'posix', 'platform_system': 'Linux',
               'implementation_name': 'cpython', 'platform_python_implementation': 'CPython'}
        env.update(environment)
        reqs = [Requirement(spec) for spec in specs]
        return {r.name: r.specifier for r in reqs if r.marker is None or r.marker.evaluate(env)}

    def test_windows_x64_gets_a_version_specific_zxing_wheel(self):
        reqs = self.requirements({'sys_platform': 'win32', 'platform_machine': 'AMD64', 'os_name': 'nt',
                                  'platform_system': 'Windows'})
        self.assertIn('2.3.0', reqs['zxing-cpp'])
        self.assertNotIn('3.1.1', reqs['zxing-cpp'])
        self.assertEqual(set(reqs), {'qrcode', 'Pillow', 'zxing-cpp'})

    def test_other_platforms_keep_current_zxing(self):
        for env in ({'sys_platform': 'linux', 'platform_machine': 'aarch64'},
                    {'sys_platform': 'darwin', 'platform_machine': 'arm64'},
                    {'sys_platform': 'win32', 'platform_machine': 'ARM64'}):
            with self.subTest(env=env):
                self.assertIn('3.1.1', self.requirements(env)['zxing-cpp'])
