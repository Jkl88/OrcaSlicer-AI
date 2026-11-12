import io
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
    def test_plugin_page_has_colour_picker_and_decoder_warning(self):
        self.assertIn('id="color" type="color"', fq.PLUGIN_HTML)
        self.assertIn("color:$('color').value", fq.PLUGIN_HTML)
        self.assertIn('s.decoder_error', fq.PLUGIN_HTML)
        self.assertIn('swatch(', fq.PLUGIN_HTML)

    def test_phone_page_shows_spool_colour(self):
        self.assertIn('swatch(', fq.PHONE_HTML)
