import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fonts.portable.development import prepare_corpora
from fonts.portable.development.project import VALIDATION_DIR
from fonts.portable.development.validation.portable_checks import corpus_run


class LocalCorpusTest(unittest.TestCase):
    def test_failed_refresh_preserves_the_complete_original_text(self):
        profiles = json.loads((VALIDATION_DIR / 'config/profiles.json').read_text())
        version = profiles['directWriteVersion']
        text = 'ب\u034fِ'

        def native_export(renderer, request, run, workspace, binary):
            settings = json.loads(request.read_text())
            self.assertEqual((settings['layout'], settings['justifier'], settings['lastPage']),
                             ('qpc_v2_layout', 'none', 0))
            binary.with_suffix('.bin.json').write_text(json.dumps({'pages': [[text], [text]]}))

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(prepare_corpora.live, 'export_run', side_effect=native_export):
                prepare_corpora.export_family('madina', root, profiles, Path('renderer'))
            run = corpus_run('madina', version, root)
            self.assertEqual((run / 'corpus.txt').read_text(), (text + '\n') * 2)
            self.assertEqual(json.loads((run / 'locations.json').read_text()),
                             [{'page': page, 'line': 1, 'text': text} for page in (1, 2)])

            with patch.object(prepare_corpora.live, 'export_run', side_effect=RuntimeError('interrupted')):
                with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                    prepare_corpora.export_family('madina', root, profiles, Path('renderer'))
            self.assertEqual(corpus_run('madina', version, root), run)

            (run / 'locations.json').unlink()
            with self.assertRaisesRegex(FileNotFoundError, 'No complete madina corpus'):
                corpus_run('madina', version, root)
