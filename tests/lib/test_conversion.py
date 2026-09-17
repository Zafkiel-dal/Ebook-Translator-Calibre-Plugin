import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from unittest.mock import patch, Mock

from ...lib.cache import Paragraph
from ...lib.conversion import ConversionWorker, convert_book_novel
from ...lib.ebook import Ebook
from ...lib.exception import ConversionAbort


module_name = 'calibre_plugins.ebook_translator.lib.conversion'


class TestConversionWorker(unittest.TestCase):
    def setUp(self):
        self.gui = Mock()
        self.icon = Mock()
        self.worker = ConversionWorker(self.gui, self.icon)
        self.worker.db = Mock()
        self.worker.api = Mock()

        self.ebook = Mock(Ebook)
        self.job = Mock()
        self.worker.working_jobs = {
            self.job: (self.ebook, str(Path('/path/to/test.epub')))}

    def test_create_worker(self):
        self.assertIsInstance(self.worker, ConversionWorker)

    def test_translate_done_job_failed_debug(self):
        self.job.failed = True
        with patch(module_name + '.DEBUG', True):
            self.worker.translate_done(self.job)
            self.gui.job_exception.assert_not_called()

    def test_translate_done_job_failed_not_debug(self):
        with patch(module_name + '.DEBUG', False):
            self.worker.translate_done(self.job)
            self.gui.job_exception.assert_called_once_with(
                self.job, dialog_title='Translation job failed')

    @patch(module_name + '.os')
    @patch(module_name + '.open')
    @patch(module_name + '.get_metadata')
    @patch(module_name + '.set_metadata')
    def test_translate_done_ebook_to_library(
            self, mock_set_metadata, mock_get_metadata, mock_open, mock_os):
        self.job.failed = False
        self.job.description = 'test description'
        self.job.log_path = '/path/to/log'
        metadata_config = {
            'subjects': ['test subject 1', 'test subject 2'],
            'lang_code': True,
            'lang_mark': True,
        }
        self.worker.config = {
            'ebook_metadata': metadata_config,
            'to_library': True,
        }
        self.ebook.is_extra_format.return_value = False
        self.ebook.title = 'test title'
        self.ebook.input_format = 'epub'
        self.ebook.output_format = 'epub'
        self.ebook.custom_title = 'test custom title'
        self.ebook.target_lang = 'German'
        self.ebook.lang_code = 'de'
        file = Mock()
        mock_open.return_value.__enter__.return_value = file
        metadata = Mock()
        metadata.title = 'test title'
        metadata.tags = []
        metadata.language = 'en'
        mock_get_metadata.return_value = metadata

        self.worker.db.create_book_entry.return_value = 89
        self.worker.api.format_abspath.return_value = '/path/to/test[m].epub'

        self.worker.translate_done(self.job)

        mock_open.assert_called_once_with(
            str(Path('/path/to/test.epub')), 'r+b')
        mock_get_metadata.assert_called_once_with(file, 'epub')
        mock_set_metadata.assert_called_once_with(file, metadata, 'epub')
        self.assertEqual('test custom title [German]', metadata.title)
        self.assertEqual('de', metadata.language)
        self.assertEqual([
            'test subject 1', 'test subject 2', 'Translated by Ebook '
            'Translator: https://translator.bookfere.com'], metadata.tags)

        self.worker.db.create_book_entry.assert_called_once_with(metadata)
        self.worker.api.add_format.assert_called_once_with(
            89, 'epub', str(Path('/path/to/test.epub')), run_hooks=False)
        self.worker.gui.library_view.model.assert_called_once()
        self.worker.gui.library_view.model().books_added \
            .assert_called_once_with(1)
        self.worker.api.format_abspath.assert_called_once_with(89, 'epub')

        self.worker.gui.status_bar.show_message.assert_called_once_with(
            'test description completed', 5000)
        arguments = self.worker.gui.proceed_question.mock_calls[0].args
        self.assertIsInstance(arguments[0], Callable)
        self.assertIs(self.worker.gui.job_manager.launch_gui_app, arguments[1])
        self.assertEqual('/path/to/log', arguments[2])
        self.assertEqual('Ebook Translation Log', arguments[3])
        self.assertEqual('Translation Completed', arguments[4])
        self.assertEqual(
            'The translation of "test custom title [German]" was completed. '
            'Do you want to open the book?',
            arguments[5])

        mock_payload = Mock()
        arguments[0](mock_payload)
        mock_payload.assert_called_once_with(
            'ebook-viewer',
            kwargs={'args': ['ebook-viewer', '/path/to/test[m].epub']})

        arguments = self.worker.gui.proceed_question.mock_calls[0].kwargs
        self.assertEqual(True, arguments.get('log_is_file'))
        self.assertIs(self.icon, arguments.get('icon'))


    @patch(module_name + '.open')
    @patch(module_name + '.open_path')
    @patch(module_name + '.os.rename')
    @patch(module_name + '.get_metadata')
    @patch(module_name + '.set_metadata')
    def test_translate_done_ebook_to_path(
            self, mock_set_metadata, mock_get_metadata, mock_os_rename,
            mock_open_path, mock_open):
        self.job.failed = False
        self.job.description = 'test description'
        self.job.log_path = str(Path('/path/to/log'))
        metadata_config = {
            'subjects': ['test subject 1', 'test subject 2'],
            'lang_code': True,
            'lang_mark': True,
        }
        self.worker.config = {
            'ebook_metadata': metadata_config,
            'to_library': False,
        }
        self.ebook.is_extra_format.return_value = False
        self.ebook.title = 'test title'
        self.ebook.input_format = 'epub'
        self.ebook.output_format = 'epub'
        self.ebook.custom_title = 'test: custom title*'
        self.ebook.target_lang = 'German'
        self.ebook.lang_code = 'de'
        file = Mock()
        mock_open.return_value.__enter__.return_value = file
        metadata = Mock()
        metadata.title = 'test title'
        metadata.tags = []
        metadata.language = 'en'
        mock_get_metadata.return_value = metadata

        self.worker.translate_done(self.job)

        original_path = str(Path('/path/to/test.epub'))
        new_path = str(Path('/path/to/test_ custom title_ [German].epub'))

        mock_open.assert_called_once_with(original_path, 'r+b')
        mock_os_rename.assert_called_once_with(original_path, new_path)
        self.worker.gui.status_bar.show_message.assert_called_once_with(
            'test description ' + 'completed', 5000)
        arguments = self.worker.gui.proceed_question.mock_calls[0].args
        self.assertIsInstance(arguments[0], Callable)
        self.assertIs(self.worker.gui.job_manager.launch_gui_app, arguments[1])
        self.assertEqual(str(Path('/path/to/log')), arguments[2])
        self.assertEqual('Ebook Translation Log', arguments[3])
        self.assertEqual('Translation Completed', arguments[4])
        self.assertEqual(
            'The translation of "test: custom title* [German]" was completed. '
            'Do you want to open the book?',
            arguments[5])

        mock_payload = Mock()
        arguments[0](mock_payload)
        mock_payload.assert_called_once_with(
            'ebook-viewer', kwargs={'args': [
                'ebook-viewer',
                str(Path('/path/to/test_ custom title_ [German].epub'))]})

        arguments = self.worker.gui.proceed_question.mock_calls[0].kwargs
        self.assertEqual(True, arguments.get('log_is_file'))
        self.assertIs(self.icon, arguments.get('icon'))


    @patch(module_name + '.open_path')
    @patch(module_name + '.os.rename')
    @patch(module_name + '.open')
    def test_translate_done_other_to_library(
            self, mock_open, mock_os_rename, mock_open_path):
        self.job.failed = False
        self.job.description = 'test description'
        self.job.log_path = str(Path('/path/to/log'))
        metadata_config = {'lang_mark': True}
        self.worker.config = {
            'ebook_metadata': metadata_config,
            'to_library': True,
        }
        self.ebook.is_extra_format.return_value = True
        self.ebook.id = 89
        self.ebook.title = 'test title'
        self.ebook.custom_title = 'test custom title'
        self.ebook.input_format = 'srt'
        self.ebook.output_format = 'srt'
        self.ebook.custom_title = 'test custom title'
        self.ebook.target_lang = 'German'
        self.worker.working_jobs = {
            self.job: (self.ebook, str(Path('/path/to/test.srt')))}
        metadata = Mock()
        self.worker.api.get_metadata.return_value = metadata
        self.worker.api.format_abspath.return_value = \
            str(Path('/path/to/test[m].srt'))
        self.worker.db.create_book_entry.return_value = 90

        self.worker.translate_done(self.job)

        self.worker.api.get_metadata.assert_called_once_with(89)
        self.worker.db.create_book_entry.assert_called_once_with(metadata)
        self.worker.api.add_format.assert_called_once_with(
            90, 'srt', str(Path('/path/to/test.srt')), run_hooks=False)
        self.worker.gui.library_view.model.assert_called_once()
        self.worker.gui.library_view.model().books_added \
            .assert_called_once_with(1)
        self.worker.api.format_abspath.assert_called_once_with(90, 'srt')
        self.worker.gui.status_bar.show_message.assert_called_once_with(
            'test description ' + 'completed', 5000)
        self.assertEqual('test custom title [German]', metadata.title)

        arguments = self.worker.gui.proceed_question.mock_calls[0].args
        self.assertIsInstance(arguments[0], Callable)
        self.assertIs(self.worker.gui.job_manager.launch_gui_app, arguments[1])
        self.assertEqual(str(Path('/path/to/log')), arguments[2])
        self.assertEqual('Ebook Translation Log', arguments[3])
        self.assertEqual('Translation Completed', arguments[4])
        self.assertEqual(
            'The translation of "test custom title [German]" was completed. '
            'Do you want to open the book?',
            arguments[5])

        mock_payload = Mock()
        arguments[0](mock_payload)
        mock_open_path.assert_called_once_with(
            str(Path('/path/to/test[m].srt')))

        arguments = self.worker.gui.proceed_question.mock_calls[0].kwargs
        self.assertEqual(True, arguments.get('log_is_file'))
        self.assertIs(self.icon, arguments.get('icon'))

    @patch(module_name + '.open_path')
    @patch(module_name + '.os.rename')
    @patch(module_name + '.open')
    def test_translate_done_other_to_path(
            self, mock_open, mock_os_rename, mock_open_path):
        self.job.failed = False
        self.job.description = 'test description'
        self.job.log_path = str(Path('/path/to/log'))
        metadata_config = {'lang_mark': True}
        self.worker.config = {
            'ebook_metadata': metadata_config,
            'to_library': False,
        }
        self.ebook.is_extra_format.return_value = True
        self.ebook.id = 89
        self.ebook.title = 'test title'
        self.ebook.custom_title = 'test custom title'
        self.ebook.input_format = 'srt'
        self.ebook.output_format = 'srt'
        self.ebook.custom_title = 'test: custom title*'
        self.ebook.target_lang = 'German'
        self.worker.working_jobs = {
            self.job: (self.ebook, str(Path('/path/to/test.srt')))}
        metadata = Mock()
        self.worker.api.get_metadata.return_value = metadata

        self.worker.translate_done(self.job)

        self.worker.api.get_metadata.assert_called_once_with(89)
        mock_os_rename.assert_called_once_with(
            str(Path('/path/to/test.srt')),
            str(Path('/path/to/test_ custom title_ [German].srt')))
        self.worker.gui.status_bar.show_message.assert_called_once_with(
            'test description ' + 'completed', 5000)
        arguments = self.worker.gui.proceed_question.mock_calls[0].args
        self.assertIsInstance(arguments[0], Callable)
        self.assertIs(self.worker.gui.job_manager.launch_gui_app, arguments[1])
        self.assertEqual(str(Path('/path/to/log')), arguments[2])
        self.assertEqual('Ebook Translation Log', arguments[3])
        self.assertEqual('Translation Completed', arguments[4])
        self.assertEqual(
            'The translation of "test: custom title* [German]" was completed. '
            'Do you want to open the book?',
            arguments[5])

        mock_payload = Mock()
        arguments[0](mock_payload)
        mock_open_path.assert_called_once_with(
            str(Path('/path/to/test_ custom title_ [German].srt')))

        arguments = self.worker.gui.proceed_question.mock_calls[0].kwargs
        self.assertEqual(True, arguments.get('log_is_file'))
        self.assertIs(self.icon, arguments.get('icon'))


class _CacheOnlyNovelCache:
    def __init__(self, paragraphs):
        self.paragraphs = paragraphs
        self.cache_only = True

    def save(self, original_group):
        pass

    def set_cache_only(self, cache_only):
        self.cache_only = cache_only

    def all_paragraphs(self):
        if not self.cache_only:
            return list(self.paragraphs)
        return [p for p in self.paragraphs if p.translation]


class TestCacheOnlyNovelConversion(unittest.TestCase):
    @staticmethod
    def _paragraph(pid, page, translation=None, text='narrative text'):
        return Paragraph(
            pid, 'md5-%s' % pid, text, text, page=page,
            translation=translation)

    @staticmethod
    def _oeb(items):
        return SimpleNamespace(
            metadata=Mock(),
            toc=SimpleNamespace(nodes=[]),
            manifest=SimpleNamespace(items=items),
        )

    def _convert_cache_only(self, oeb, cache, novel_config):
        output_convert = Mock()
        output_plugin = Mock()
        output_plugin.convert = output_convert
        output_plugin.report_progress = Mock()
        plumber = Mock()
        plumber.output_plugin = output_plugin

        def run():
            output_plugin.convert(oeb, 'output.epub', None, None, Mock())

        plumber.run.side_effect = run
        element_handler = Mock()
        element_handler.prepare_original.return_value = []

        with patch(module_name + '.Plumber', return_value=plumber), \
                patch(module_name + '.CompositeProgressReporter'), \
                patch(module_name + '.get_metadata_elements', return_value=[]), \
                patch(module_name + '.get_toc_elements', return_value=[]), \
                patch(module_name + '.get_page_elements', return_value=[]):
            convert_book_novel(
                'input.epub', 'output.epub', Mock(), element_handler, cache,
                'debug', 'utf-8', Mock(), novel_config=novel_config,
                cache_only=True)
        return output_convert, element_handler

    def test_cache_only_aborts_for_missing_narrative_paragraphs(self):
        items = [
            SimpleNamespace(id='chapter-1', href='chapter-1.xhtml'),
            SimpleNamespace(id='chapter-2', href='chapter-2.xhtml'),
        ]
        oeb = self._oeb(items)
        cache = _CacheOnlyNovelCache([
            self._paragraph(10, 'chapter-1', translation='Translated one'),
            self._paragraph(20, 'chapter-2'),
            self._paragraph(99, 'content.opf'),
        ])

        with self.assertRaisesRegex(
                ConversionAbort,
                r'1 narrative paragraph\(s\) are missing translations '
                r'\(IDs: 20\)'):
            self._convert_cache_only(
                oeb, cache, {'novel_front_matter_min_chars': 0})

        self.assertTrue(cache.cache_only)

    def test_cache_only_allows_missing_auxiliary_and_skipped_rows(self):
        items = [
            SimpleNamespace(id='cover', href='cover.xhtml'),
            SimpleNamespace(id='story', href='story.xhtml'),
            SimpleNamespace(id='license', href='license.xhtml'),
        ]
        oeb = self._oeb(items)
        cache = _CacheOnlyNovelCache([
            self._paragraph(1, 'content.opf'),
            self._paragraph(2, 'toc.ncx'),
            self._paragraph(3, 'cover', text='THE BOOK'),
            self._paragraph(
                4, 'story', translation='Translated story', text='x' * 200),
            self._paragraph(
                5, 'license', text=(
                    'THE FULL PROJECT GUTENBERG LICENSE. '
                    'Project Gutenberg is a registered trademark. ' * 4)),
        ])

        output_convert, element_handler = self._convert_cache_only(
            oeb, cache, {'novel_front_matter_min_chars': 100})

        output_convert.assert_called_once()
        translated = element_handler.add_translations.call_args.args[0]
        self.assertEqual([4], [paragraph.id for paragraph in translated])
