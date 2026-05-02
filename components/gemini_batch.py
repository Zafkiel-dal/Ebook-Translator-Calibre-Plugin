
import json
import time as _time

from qt.core import (  # type: ignore
    QDialog, QStackedLayout, QWidget, QVBoxLayout, Qt, pyqtSlot,
    QLabel, QPushButton, QGroupBox, QFormLayout, QPlainTextEdit, QThread,
    QProgressBar, QTimer, pyqtSignal, QComboBox, QHBoxLayout, QCheckBox)

from calibre.utils.localization import _  # type: ignore

from ..lib.utils import log
from .alert import AlertMessage
from .chatgpt import (
    ChatgptBatchTranslationWorker, ChatgptBatchTranslationManager, request)
from ..lib.token_usage import log_token_usage


load_translations()  # type: ignore


class GeminiBatchTranslationWorker(ChatgptBatchTranslationWorker):
    """Worker tailored for the Gemini batch API.

    Overrides status checking and result retrieval to handle Gemini-specific
    response formats (e.g. normalised statuses, dest file reference).
    """

    # Gemini normalised statuses returned by GeminiBatchTranslate.check()
    _completed_status = 'completed'
    _cancelling_statuses = (
        'cancelling', 'cancelled', 'completed', 'failed', 'expired')

    apply_progress = pyqtSignal(int, int)  # current, total
    token_log = pyqtSignal(str)  # token usage log message
    log_message = pyqtSignal(str)  # real-time status log

    def __init__(self, batch_translator):
        super().__init__(batch_translator)
        self.book_title = 'Unknown'
        self._auto_polling = False  # flag to avoid page flicker during poll
        self._cached_translations = None  # cache translations after retrieve
        self._poll_start_time = None  # track elapsed time during polling
        self._total_batches = 0  # total number of request chunks
        self._last_logged_batch = -1  # track last logged batch count to avoid duplicates

    @pyqtSlot()
    @request
    def create_batch(self):
        self.process_tip.emit(_('processing...'))
        self.stack_index.emit(1)
        
        # Build JSONL lines first to get the actual total batches (especially for dynamic mode)
        lines = self._batch_translator.build_jsonl(self._paragraphs)
        self._total_batches = len(lines)
        # Store paragraphs reference on the batch translator for later use in retrieve()
        self._batch_translator._paragraphs = self._paragraphs
        
        self.log_message.emit(
            _('Preparing {} paragraphs in {} batches...').format(
                len(self._paragraphs), self._total_batches))
        
        if self._file_id is None:
            # Re-join lines for upload to avoid double calculation
            content = ('\n'.join(lines) + '\n').encode('utf-8')
            self._file_id = self._batch_translator._upload_content(content)
            log.debug('A new file was uploaded: %s' % self._file_id)
            self.save_file_id.emit(self._file_id)
            self.log_message.emit(_('File uploaded: {}').format(self._file_id))
        self._batch_id = self._batch_translator.create(self._file_id)
        log.debug('A batch translation was created: %s' % self._batch_id)
        self.save_batch_id.emit(self._batch_id)
        self.log_message.emit(
            _('Batch created: {}').format(self._batch_id))
        self._auto_polling = False
        self._last_logged_batch = -1
        self._poll_start_time = _time.time()
        self.check.emit()

    @pyqtSlot()
    @request
    def check_details(self):
        # Only switch to processing page if user manually triggered (not auto-poll)
        if not self._auto_polling:
            self.process_tip.emit(_('checking...'))
            self.stack_index.emit(1)
            self._poll_start_time = None  # reset for manual check

        if self._poll_start_time is None:
            self._poll_start_time = _time.time()
        elapsed = int(_time.time() - self._poll_start_time)

        self._batch_info = self._batch_translator.check(self._batch_id)
        batch_status = self._batch_info.get('status', 'unknown')
        state = self._batch_info.get('state', '')
        request_counts = self._batch_info.get('request_counts')

        # Log raw state for debugging
        log.debug('Batch check: status=%s, state=%s, counts=%s',
                  batch_status, state, request_counts)

        if request_counts:
            total = int(request_counts.get('totalRequestCount') or 0)
            succeeded = int(request_counts.get('succeededRequestCount') or 0)
            failed = int(request_counts.get('failedRequestCount') or 0)
            done = succeeded + failed
            if done != self._last_logged_batch:
                self._last_logged_batch = done
                self.log_message.emit(
                    _('Batch {}/{} — {} succeeded, {} failed').format(
                        done, total or self._total_batches, succeeded, failed))
        elif batch_status == 'completed' and self._cached_translations is None:
            # Show batch progression (fill in steps Gemini skipped)
            for step in range(1, self._total_batches):
                self.log_message.emit(
                    _('Batch {}/{} — Processing...').format(
                        step, self._total_batches))
            self.log_message.emit(
                _('Batch {}/{} — Completed, retrieving results...').format(
                    self._total_batches, self._total_batches))
            # Auto-retrieve results and show token count before Apply
            output_file_id = self._batch_info.get('output_file_id')
            if output_file_id:
                self._cached_translations = self._batch_translator.retrieve(output_file_id)
                translator = self._batch_translator.translator
                total_tokens = translator.usage_data.get('total_tokens', 0)
                if total_tokens > 0:
                    input_tokens = translator.usage_data.get('prompt_tokens', 0)
                    output_tokens = translator.usage_data.get('completion_tokens', 0)
                    thinking_tokens = translator.usage_data.get('thinking_tokens', 0)
                    model = getattr(translator, 'model', 'unknown')
                    self.log_message.emit(
                        _('Retrieved {} translations from {} paragraphs').format(
                            len(self._cached_translations),
                            len(self._paragraphs)))
                    token_summary = _(
                        '✓ Complete — {} total tokens ({} in / {} out)').format(
                        total_tokens, input_tokens, output_tokens)
                    if thinking_tokens > 0:
                        token_summary += _(
                            ' ({} thinking)').format(thinking_tokens)
                    self.log_message.emit(token_summary)
                    token_lines = [
                        _('Batch Token Usage ({}):').format(model),
                        _('  Input tokens:     {}').format(input_tokens),
                        _('  Output tokens:    {}').format(output_tokens),
                        _('  Total tokens:     {}').format(total_tokens),
                    ]
                    if thinking_tokens > 0:
                        token_lines.insert(
                            3, _('  Thinking tokens:  {}').format(thinking_tokens))
                    self.token_log.emit('\n'.join(token_lines))
                    log_token_usage(
                        engine_name=translator.name,
                        book_title=self.book_title,
                        model=model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                        thinking_tokens=thinking_tokens,
                        is_batch=True)
                self.log_message.emit(_('Ready to apply — click Apply to continue'))
        else:
            # Pending / processing with no counts yet — only log if status changed
            if self._last_logged_batch != 0:
                self._last_logged_batch = 0
                self.log_message.emit(
                    _('Queued ({} batches) — {}').format(
                        self._total_batches, batch_status.capitalize()))
        if self._batch_info.get('status') == self._completed_status:
            self.enable_apply_button.emit(True)
        self.trans_details.emit(self._batch_info)
        self.stack_index.emit(2)

    @pyqtSlot()
    @request
    def cancel_batch(self):
        self.process_tip.emit(_('canceling...'))
        self.stack_index.emit(1)
        self._batch_info = self._batch_translator.check(self._batch_id)
        if self._batch_info.get('status') not in self._cancelling_statuses:
            self._batch_translator.cancel(self._batch_id)
            self._batch_translator.delete(self._file_id)
        self.remove_batch.emit()
        self.finished.emit()

    @pyqtSlot()
    @request
    def apply_batch(self):
        self.enable_apply_button.emit(False)
        self.process_tip.emit(_('applying...'))
        self.stack_index.emit(1)
        translator = self._batch_translator.translator
        # Gemini stores the output file reference under 'output_file_id'
        # which is normalised by GeminiBatchTranslate.check()
        output_file_id = self._batch_info.get('output_file_id')
        total_paras = len(self._paragraphs)
        # Use cached translations if already retrieved during check
        if self._cached_translations is not None:
            translations = self._cached_translations
            self.log_message.emit(
                _('Applying {} cached translations to {} paragraphs...').format(
                    len(translations), total_paras))
        else:
            self.log_message.emit(_('Retrieving translations...'))
            translations = self._batch_translator.retrieve(output_file_id)
            self.log_message.emit(
                _('Applying {} translations to {} paragraphs...').format(
                    len(translations), total_paras))
        for i, paragraph in enumerate(self._paragraphs):
            if paragraph.md5 not in translations.keys():
                continue
            paragraph.translation = translations.get(paragraph.md5)
            paragraph.engine_name = translator.name
            paragraph.target_lang = translator.get_target_lang()
            self.paragraph_sig.emit(paragraph)
            self.apply_progress.emit(i + 1, total_paras)
            if (i + 1) % 50 == 0 or (i + 1) == total_paras:
                self.log_message.emit(
                    _('Applied paragraph {}/{}').format(i + 1, total_paras))

        self.log_message.emit(_('Batch translation applied successfully.'))
        self.finished.emit()


class GeminiBatchTranslationManager(ChatgptBatchTranslationManager):
    """Batch translation dialog tailored for the Gemini batch API."""

    batch_thread = QThread()

    def __init__(self, translator, cache, table, parent=None):
        # Call QDialog.__init__ directly – we build everything from scratch
        # with Gemini-specific settings, not reusing ChatGPT's init logic.
        QDialog.__init__(self, parent=parent)
        self.setWindowTitle(_('Gemini Batch Translation'))
        self.setMinimumWidth(500)
        self.setMinimumHeight(300)

        self.cache = cache
        self.table = table

        self.alert = AlertMessage(self)

        self.batch_worker = GeminiBatchTranslationWorker(translator)
        self.batch_worker.moveToThread(self.batch_thread)
        self.batch_thread.finished.connect(self.batch_worker.deleteLater)
        self.batch_thread.start()

        self.stack = QStackedLayout(self)
        self.stack.setContentsMargins(100, 30, 30, 30)
        self.stack.addWidget(self.layout_create())
        self.stack.addWidget(self.layout_process())
        self.stack.addWidget(self.layout_details())
        self.stack.addWidget(self.layout_information())

        self.batch_worker.stack_index.connect(self.stack.setCurrentIndex)

        # Gemini uses its own cache keys to avoid collisions with ChatGPT
        self._cache_batch_key = 'gemini_batch_id'
        self._cache_file_key = 'gemini_file_id'

        self.batch_id = self.cache.get_info(self._cache_batch_key)
        self.file_id = self.cache.get_info(self._cache_file_key)

        log.debug('Initialized Gemini file id: %s' % self.file_id)

        self.batch_worker.book_title = self.cache.get_info('title') or 'Unknown'
        self.batch_worker.set_paragraphs(
            self.table.get_selected_paragraphs(True, True))
        self.batch_worker.set_batch_id(self.batch_id)
        self.batch_worker.set_file_id(self.file_id)

        def set_batch_id(batch_id):
            self.batch_id = batch_id
            self.cache.set_info(self._cache_batch_key, batch_id)
        self.batch_worker.save_batch_id.connect(set_batch_id)

        def set_file_id(file_id):
            self.file_id = file_id
            self.cache.set_info(self._cache_file_key, file_id)
        self.batch_worker.save_file_id.connect(set_file_id)

        def remove_batch():
            self.file_id = None
            self.cache.del_info(self._cache_batch_key)
            self.cache.del_info(self._cache_file_key)
        self.batch_worker.remove_batch.connect(remove_batch)

        def apply_paragraph(paragraph):
            self.table.row.emit(paragraph.row)
            self.cache.update_paragraph(paragraph)
        self.batch_worker.paragraph_sig.connect(apply_paragraph)

        self.batch_worker.finished.connect(lambda: (self._poll_timer.stop(), self.done(0)))
        
        if self.batch_id is not None:
            self.batch_worker._auto_polling = True
            self.batch_worker.check.emit()
            self._poll_timer.start(5000)

    def layout_create(self):
        title = QLabel(_('Create a new batch translation'))
        title.setStyleSheet('font-size:16px;font-weight:bold;')
        message = QLabel(_(
            'All original content must be uploaded to Google AI for batch '
            'translation, and you will need to wait up to 24 hours to '
            'continue the translation process. '
            '<a href="https://ai.google.dev/gemini-api/docs/batch-api">'
            'more details</a>'))
        message.setWordWrap(True)
        message.setOpenExternalLinks(True)

        # Output token limit dropdown
        token_combo = QComboBox()
        token_combo.addItem(_('Default (model default)'), None)
        self._token_combo = token_combo

        # Dynamic Chunking checkbox
        dynamic_chk = QCheckBox(_('Dynamic Chunking'))
        dynamic_chk.setToolTip(_('Smart chunking based on thinking tokens and context overlap.'))
        # Only show for models that support thinking_level (Gemini 3.0+)
        is_thinking_level = False
        try:
            is_thinking_level = self.batch_worker._batch_translator.translator._supports_thinking_level()
        except Exception:
            pass
        dynamic_chk.setVisible(is_thinking_level)
        self._dynamic_chk = dynamic_chk

        # Fetch max token limit from Gemini API dynamically
        try:
            max_limit = self.batch_worker._batch_translator.get_model_output_limit()
            if max_limit is not None:
                token_combo.addItem(
                    _('Max ({} tokens)').format(max_limit), max_limit)
                # Set default to Max
                token_combo.setCurrentIndex(token_combo.count() - 1)
        except Exception:
            pass

        def on_create_clicked():
            # Pass selected settings to batch translator before creating
            selected = token_combo.currentData()
            self.batch_worker._batch_translator.max_output_tokens = selected
            self.batch_worker._batch_translator.dynamic_chunking = dynamic_chk.isChecked()

        button = QPushButton('Create Batch Translation')
        button.clicked.connect(on_create_clicked)
        button.clicked.connect(self.batch_worker.create)

        details_btn = QPushButton(_('Details'))

        def show_chunking_details():
            paragraphs = self.table.get_selected_paragraphs(True, True)
            if not paragraphs:
                return
            # Apply current settings
            selected = token_combo.currentData()
            self.batch_worker._batch_translator.max_output_tokens = selected
            self.batch_worker._batch_translator.dynamic_chunking = dynamic_chk.isChecked()
            try:
                details = self.batch_worker._batch_translator.get_chunking_details(paragraphs)
            except Exception as e:
                details = []

            if not details:
                return

            dlg = QDialog(self)
            dlg.setWindowTitle(_('Chunking Details'))
            dlg.resize(650, 400)
            layout = QVBoxLayout(dlg)

            # Summary header
            total_paras = len(paragraphs)
            total_batches = len(details)
            total_input_chars = sum(d['input_chars'] for d in details)
            limit = details[0].get('reserve_tokens', 0) + details[0].get('input_tokens_est', 0)
            reserve_pct = details[0]['reserve_percent'] * 100 if details else 0

            summary = _(
                'Total: {} paragraphs → {} batches | '
                'Model limit: {} tokens | '
                'Reserve: {}% for thinking'
            ).format(total_paras, total_batches, 
                     self.batch_worker._batch_translator.max_output_tokens or 
                     _('model default'), int(reserve_pct))
            summary_label = QLabel(summary)
            summary_label.setWordWrap(True)
            summary_label.setStyleSheet('font-weight:bold; margin-bottom:8px;')
            layout.addWidget(summary_label)

            # Table header
            header = _(
                '{:<8} {:<14} {:<12} {:<14} {:<14} {:<14} {:<10}'
            ).format('Batch', 'Paragraphs', 'Input chars', 'Input tokens',
                     'Output est.', 'Reserve', 'Context')
            header_label = QLabel('<pre>' + header + '</pre>')
            layout.addWidget(header_label)

            # Table rows
            text_lines = []
            for d in details:
                context_str = _('Yes') if d['has_context'] else _('No')
                line = _(
                    '{:<8} {:<14} {:<12} {:<14} {:<14} {:<14} {:<10}'
                ).format(
                    d['batch_index'],
                    d['num_paragraphs'],
                    d['input_chars'],
                    d['input_tokens_est'],
                    d['output_tokens_est'],
                    d['reserve_tokens'],
                    context_str,
                )
                text_lines.append(line)

            text_edit = QPlainTextEdit()
            text_edit.setReadOnly(True)
            text_edit.setPlainText('\n'.join(text_lines))
            text_edit.setMaximumHeight(250)
            text_edit.setStyleSheet('font-family: monospace;')
            layout.addWidget(text_edit)

            close_btn = QPushButton(_('Close'))
            close_btn.clicked.connect(dlg.accept)
            layout.addWidget(close_btn)

            dlg.exec()

        details_btn.clicked.connect(show_chunking_details)

        preview_btn = QPushButton(_('Preview JSONL'))

        def show_jsonl_preview():

            paragraphs = self.table.get_selected_paragraphs(True, True)
            if not paragraphs:
                return
            # Apply current settings
            selected = token_combo.currentData()
            self.batch_worker._batch_translator.max_output_tokens = selected
            self.batch_worker._batch_translator.dynamic_chunking = dynamic_chk.isChecked()
            try:
                lines = self.batch_worker._batch_translator.build_jsonl(paragraphs)
            except Exception as e:
                lines = [json.dumps({"error": str(e)})]

            preview_dlg = QDialog(self)
            preview_dlg.setWindowTitle(_('JSONL Preview — {} requests').format(len(lines)))
            preview_dlp_layout = QVBoxLayout(preview_dlg)
            preview_text = QPlainTextEdit()
            preview_text.setReadOnly(True)
            # Show each line pretty-printed
            formatted = []
            for idx, line in enumerate(lines):
                parsed = json.loads(line)
                formatted.append(_('--- Request {} ---').format(idx + 1))
                formatted.append(json.dumps(parsed, indent=2, ensure_ascii=False))
                formatted.append('')
            preview_text.setPlainText('\n'.join(formatted))
            preview_dlp_layout.addWidget(preview_text)

            close_btn = QPushButton(_('Close'))
            close_btn.clicked.connect(preview_dlg.accept)
            preview_dlp_layout.addWidget(close_btn)

            preview_dlg.resize(700, 500)
            preview_dlg.exec()

        preview_btn.clicked.connect(show_jsonl_preview)

        # Button row
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(preview_btn)
        btn_layout.addWidget(details_btn)
        btn_layout.addStretch(1)
        btn_layout.addWidget(button)


        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(message)
        layout.addSpacing(10)

        # Token limit row
        token_layout = QFormLayout()
        token_layout.addRow(_('Output Token Limit'), token_combo)
        token_layout.addRow('', dynamic_chk)
        layout.addLayout(token_layout)
        layout.addSpacing(20)
        layout.addLayout(btn_layout)
        layout.addStretch(1)

        return widget

    def layout_data(self):
        status = QLabel()
        detail = QPlainTextEdit()
        detail.setReadOnly(True)

        def set_details_data(data):
            try:
                detail.clear()
                batch_status = data.get('status') or 'unknown'
                status.setText(str(batch_status))
                status_label = str(batch_status).capitalize()

                # Show internal state and creation time
                state = data.get('state')
                create_time = data.get('create_time')
                if state:
                    detail.appendPlainText(_('State: {}').format(state))
                if create_time:
                    detail.appendPlainText(_('Created: {}').format(create_time))

                request_counts = data.get('request_counts')
                if request_counts and isinstance(request_counts, dict):
                    total = int(request_counts.get('totalRequestCount') or request_counts.get('total_request_count') or 0)
                    succeeded = int(request_counts.get('succeededRequestCount') or request_counts.get('succeeded_request_count') or 0)
                    failed = int(request_counts.get('failedRequestCount') or request_counts.get('failed_request_count') or 0)

                    if total > 0:
                        progress.setRange(0, total)
                        progress.setValue(succeeded + failed)
                        pct = int((succeeded + failed) / total * 100)
                        progress.setFormat(
                            "{} — %v/%m ({}%)".format(status_label, pct))
                    else:
                        progress.setRange(0, 0)
                        progress.setFormat(
                            "{} — ".format(status_label)
                            + _("Waiting for Google to start..."))
                else:
                    progress.setRange(0, 0)
                    progress.setFormat(
                        "{} — ".format(status_label)
                        + _("Waiting for progress data..."))

                if batch_status == 'completed':
                    progress.setFormat(_("Completed — Ready to apply"))
                    detail.appendPlainText(_("Status: COMPLETED. You can now click Apply."))
                elif batch_status == 'processing':
                    detail.appendPlainText(_("Progress: Google is processing your batch..."))
                elif data.get('errors'):
                    detail.appendPlainText(_('Errors: {}').format(data.get('errors')))

                if batch_status in ('completed', 'failed', 'cancelled', 'expired'):
                    self._poll_timer.stop()
                elif batch_status in ('processing', 'pending') and not self._poll_timer.isActive():
                    self._poll_timer.start(5000)
            except Exception as e:
                log.error('Error updating batch details: %s', e)
        
        self.batch_worker.trans_details.connect(set_details_data)

        self._poll_timer = QTimer(self)

        def auto_poll():
            self.batch_worker._auto_polling = True
            self.batch_worker.check.emit()
        self._poll_timer.timeout.connect(auto_poll)

        progress = QProgressBar()
        progress.setTextVisible(True)
        progress.setFormat("%v/%m")
        
        apply_progress = QProgressBar()
        apply_progress.setTextVisible(True)
        apply_progress.setVisible(False)
        apply_progress.setFormat("%v/%m")
        
        def set_apply_progress(current, total):
            apply_progress.setRange(0, total)
            apply_progress.setValue(current)
            apply_progress.setFormat(f"%v/%m ({{}}%)".format(int(current/total*100)))
            apply_progress.setVisible(True)
        self.batch_worker.apply_progress.connect(set_apply_progress)

        def on_token_log(msg):
            detail.appendPlainText('')
            detail.appendPlainText(_('── Token Usage ──'))
            detail.appendPlainText(msg)
            # Also write to calibre job log at info level for visibility
            log.info(msg)
        self.batch_worker.token_log.connect(on_token_log)

        # Real-time log panel
        log_panel = QPlainTextEdit()
        log_panel.setReadOnly(True)
        log_panel.setMaximumBlockCount(200)
        log_panel.setPlaceholderText(_('Activity log will appear here...'))

        def on_log_message(msg):
            from datetime import datetime
            timestamp = datetime.now().strftime('%H:%M:%S')
            log_panel.appendPlainText('[{}] {}'.format(timestamp, msg))
        self.batch_worker.log_message.connect(on_log_message)

        widget = QGroupBox(_('Batch translation details'))
        layout = QFormLayout(widget)
        layout.addRow(_('Status'), status)
        layout.addRow(_('Google Progress'), progress)
        layout.addRow(_('Applying Results'), apply_progress)
        layout.addRow(_('Detail'), detail)
        layout.addRow(_('Log'), log_panel)

        self.set_form_layout_policy(layout)

        return widget
