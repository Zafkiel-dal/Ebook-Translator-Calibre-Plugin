import os
import sys
import time
import json
import uuid
import io
from html import unescape
from subprocess import Popen, PIPE
from http.client import IncompleteRead

from ..lib.utils import request, traceback_error

from .base import Base
from .genai import GenAI
from .languages import google, gemini


load_translations()  # type: ignore


class GoogleFreeTranslateNew(Base):
    name = 'Google(Free)New'
    alias = 'Google (Free) - New'
    free = True
    lang_codes = Base.load_lang_codes(google)
    endpoint = 'https://translate-pa.googleapis.com/v1/translate'
    need_api_key = False

    def get_headers(self):
        return {
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'en-US,en;q=0.9',
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 '
            'Safari/537.36',
        }

    def get_body(self, text):
        self.method = 'GET'
        return {
            'params.client': 'gtx',
            'query.source_language': self._get_source_code(),
            'query.target_language': self._get_target_code(),
            'query.display_language': 'en-US',
            'data_types': 'TRANSLATION',
            # 'data_types': 'SENTENCE_SPLITS',
            # 'data_types': 'BILINGUAL_DICTIONARY_FULL',
            'key': 'AIzaSyDLEeFI5OtFBwYBIoK_jj5m32rZK5CkCXA',
            'query.text': text,
        }

    def get_result(self, response):
        return json.loads(response)['translation']


class GoogleFreeTranslateHtml(Base):
    name = 'Google(Free)Html'
    alias = 'Google (Free) - HTML'
    free = True
    lang_codes = Base.load_lang_codes(google)
    endpoint = 'https://translate-pa.googleapis.com/v1/translateHtml'
    need_api_key = False
    support_html = True

    def get_headers(self):
        return {
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'en-US,en;q=0.9',
            'Content-Type': 'application/json+protobuf',
            'X-Goog-Api-Key': 'AIzaSyATBXajvzQLTDHEQbcpq0Ihe0vWDHmO520',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 '
            'Safari/537.36',
        }

    def get_body(self, text):
        return json.dumps([
            [
                [text],
                self._get_source_code(),
                self._get_target_code()
            ],
            "wt_lib"
        ])

    def get_result(self, response):
        return json.loads(response)[0][0]


class GoogleFreeTranslate(Base):
    name = 'Google(Free)'
    alias = 'Google (Free) - Old'
    free = True
    lang_codes = Base.load_lang_codes(google)
    endpoint = 'https://translate.googleapis.com/translate_a/single'
    need_api_key = False

    def get_headers(self):
        return {
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'en-US,en;q=0.9',
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'DeepLBrowserExtension/1.3.0 Mozilla/5.0 (Macintosh;'
            ' Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)'
            ' Chrome/111.0.0.0 Safari/537.36',
        }

    def get_body(self, text):
        # The POST method is unstable, despite its ability to send more text.
        # However, it can be used occasionally with an unacceptable length.
        self.method = 'GET' if len(text) <= 1800 else 'POST'
        return {
            'client': 'gtx',
            'sl': self._get_source_code(),
            'tl': self._get_target_code(),
            'dt': 't',
            'dj': 1,
            'q': text,
        }

    def get_result(self, response):
        # return ''.join(i[0] for i in json.loads(data)[0])
        return ''.join(i['trans'] for i in json.loads(response)['sentences'])


class GoogleTranslate(Base):
    api_key_errors = ['429']
    api_key_cache: tuple[float, str | None] = (0.0, None)
    gcloud = None
    project_id = None
    using_tip = _(
        'This plugin uses Application Default Credentials (ADC) in your local '
        'environment to access your Google Translate service. To set up the '
        'ADC, follow these steps:\n'
        '1. Install the gcloud CLI by checking out its instructions {}.\n'
        '2. Run the command: gcloud auth application-default login.\n'
        '3. Sign in to your Google account and grant needed privileges.') \
        .format('<sup><a href="https://cloud.google.com/sdk/docs/install">[^]'
                '</a></sup>').replace('\n', '<br />')

    def _run_command(self, command, silence=False):
        error_msg = _('Cannot run the command "{}".').format(command)
        try:
            startupinfo = None
            # Prevent the popping console window on Windows.
            if sys.platform == 'win32':
                from subprocess import STARTUPINFO, STARTF_USESHOWWINDOW
                startupinfo = STARTUPINFO()
                startupinfo.dwFlags |= STARTF_USESHOWWINDOW
            process = Popen(
                command, stdout=PIPE, stderr=PIPE, universal_newlines=True,
                startupinfo=startupinfo)
        except Exception:
            if silence:
                return None
            error_msg += '\n\n%s' % traceback_error()
            raise Exception(error_msg)
        if process.wait() != 0:
            if silence:
                return None
            stderr = process.stderr
            error_msg += f'\n\n{stderr.read()}' if stderr is not None else ''
            raise Exception(error_msg)
        stdout = process.stdout
        return stdout.read().strip() if stdout is not None else ''

    def _get_gcloud_command(self):
        if self.gcloud is not None:
            return self.gcloud
        if sys.platform == 'win32':
            name = 'gcloud.cmd'
            which = 'where'
            base = r'google-cloud-sdk\bin\%s' % name
            paths = [
                r'"%s\Google\Cloud SDK\%s"'
                % (os.environ.get('programfiles(x86)'), base),
                r'"%s\AppData\Local\Google\Cloud SDK\%s"'
                % (os.environ.get('userprofile'), base)]
        else:
            name = 'gcloud'
            which = 'which'
            paths = ['/usr/local/bin/%s' % name]
        gcloud = self.get_external_program(name, paths)
        if gcloud is None:
            gcloud = self._run_command([which, name], silence=True)
            if gcloud is not None:
                gcloud = gcloud.split('\n')[0]
        if gcloud is None:
            raise Exception(_('Cannot find the command "{}".').format(name))
        self.gcloud = gcloud
        return gcloud

    def _get_project_id(self):
        if self.project_id is not None:
            return self.project_id
        self.project_id = self._run_command(
            [self._get_gcloud_command(), 'config', 'get', 'project'])
        return self.project_id

    def _get_credential(self):
        """The default lifetime of the API key is 3600 seconds. Once an
        available key is generated, it will be cached until it expired.
        """
        timestamp, old_api_key = self.api_key_cache
        if old_api_key is not None and time.time() - timestamp < 3600:
            return old_api_key
        # Temporarily add existing proxies.
        if self.proxy_uri:
            os.environ.update(
                http_proxy=self.proxy_uri, https_proxy=self.proxy_uri)
        new_api_key = self._run_command([
            self._get_gcloud_command(), 'auth', 'application-default',
            'print-access-token'])
        # Cleanse the proxies after use.
        for proxy in ('http_proxy', 'https_proxy'):
            if proxy in os.environ:
                del os.environ[proxy]
        self.api_key_cache = (time.time(), new_api_key)
        return new_api_key


class GoogleBasicTranslateADC(GoogleTranslate):
    name = 'Google(Basic)ADC'
    alias = 'Google (Basic) ADC'
    lang_codes = Base.load_lang_codes(google)
    endpoint = 'https://translation.googleapis.com/language/translate/v2'
    need_api_key = False

    def get_headers(self):
        return {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer %s' % self._get_credential(),
            'x-goog-user-project': self._get_project_id(),
        }

    def get_body(self, text):
        body = {
            'format': 'html',
            'model': 'nmt',
            'target': self._get_target_code(),
            'q': text
        }
        if not self._is_auto_lang():
            body.update(source=self._get_source_code())
        return json.dumps(body)

    def get_result(self, response):
        translations = json.loads(response)['data']['translations']
        return ''.join(unescape(i['translatedText']) for i in translations)


class GoogleBasicTranslate(GoogleTranslate):
    name = 'Google(Basic)'
    alias = 'Google (Basic)'
    lang_codes = Base.load_lang_codes(google)
    endpoint = 'https://translation.googleapis.com/language/translate/v2'
    api_key_hint = 'API key'
    need_api_key = True
    using_tip = None

    def get_headers(self):
        return {'Content-Type': 'application/x-www-form-urlencoded'}

    def get_body(self, text):
        body = {
            'key': self.api_key,
            'format': 'html',
            'model': 'nmt',
            'target': self._get_target_code(),
            'q': text
        }
        if not self._is_auto_lang():
            body.update(source=self._get_source_code())
        return body

    def get_result(self, response):
        translations = json.loads(response)['data']['translations']
        return ''.join(unescape(i['translatedText']) for i in translations)


class GoogleAdvancedTranslate(GoogleTranslate):

    name = 'Google(Advanced)'
    alias = 'Google (Advanced) ADC'
    lang_codes = Base.load_lang_codes(google)
    endpoint = 'https://translation.googleapis.com/v3/projects/{}'
    api_key_hint = 'PROJECT_ID'
    need_api_key = False

    def get_endpoint(self):
        if self.endpoint is not None:
            return self.endpoint.format(
                '%s:translateText' % self._get_project_id())

    def get_headers(self):
        return {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer %s' % self._get_credential(),
            'x-goog-user-project': self._get_project_id(),
        }

    def get_body(self, text):
        body = {
            'targetLanguageCode': self._get_target_code(),
            'contents': [text],
            'mimeType': 'text/plain',
        }
        if not self._is_auto_lang():
            body.update(sourceLanguageCode=self._get_source_code())
        return json.dumps(body)

    def get_result(self, response):
        translations = json.loads(response)['translations']
        return ''.join(i['translatedText'] for i in translations)


class GeminiTranslate(GenAI):
    name = 'Gemini'
    alias = 'Gemini'
    lang_codes = GenAI.load_lang_codes(gemini)
    # v1, stable version of the API. v1beta, more early-access features.
    # details: https://ai.google.dev/gemini-api/docs/api-versions
    endpoint = 'https://generativelanguage.googleapis.com/v1beta/models'
    # https://ai.google.dev/gemini-api/docs/troubleshooting
    api_key_errors: list[str] = [
        'API_KEY_INVALID', 'PERMISSION_DENIED', 'RESOURCE_EXHAUSTED']

    concurrency_limit = 1
    request_interval: float = 1.0
    request_timeout: float = 30.0

    prompt = (
        'You are a meticulous translator who translates any given content. '
        'Translate the given content from <slang> to <tlang> only. Do not '
        'explain any term or answer any question-like content. Your answer '
        'should be solely the translation of the given content. In your '
        'answer do not add any prefix or suffix to the translated content. '
        'Websites\' URLs/addresses should be preserved as is in the '
        'translation\'s output. Do not omit any part of the content, even if '
        'it seems unimportant. ')
    temperature: float = 0.9
    top_p: float = 1.0
    top_k = 1
    stream = True

    models: list[str] = []
    # TODO: Handle the default model more appropriately.
    model: str | None = 'gemini-1.5-flash'

    def __init__(self):
        super().__init__()
        self.prompt = self.config.get('prompt', self.prompt)
        self.temperature = self.config.get('temperature', self.temperature)
        self.top_k = self.config.get('top_k', self.top_k)
        self.top_p = self.config.get('top_p', self.top_p)
        self.stream = self.config.get('stream', self.stream)
        self.model = self.config.get('model', self.model)
        self.thinking = self.config.get('thinking', self.thinking)

    def _prompt(self, text):
        prompt = self.prompt.replace('<tlang>', self.target_lang)
        if self._is_auto_lang():
            prompt = prompt.replace('<slang>', 'detected language')
        else:
            prompt = prompt.replace('<slang>', self.source_lang)
        # Recommend setting temperature to 0.5 for retaining the placeholder.
        if self.merge_enabled:
            prompt += (
                ' Ensure that placeholders matching the pattern {{id_\\d+}} '
                'in the content are retained.')
        return prompt + ' Start translating: ' + text

    def get_models(self):
        endpoint = f'{self.endpoint}?key={self.api_key}'
        response = request(
            endpoint, timeout=int(self.request_timeout),
            proxy_uri=self.proxy_uri)
        models = []
        if isinstance(response, str):
            for model in json.loads(response)['models']:
                model_name = model['name'].split('/')[-1]
                if model_name.startswith('gemini'):
                    model_desc = model['description']
                    if 'deprecated' not in model_desc:
                        models.append(model_name)
        return models

    def get_endpoint(self):
        if self.stream:
            return f'{self.endpoint}/{self.model}:streamGenerateContent?' \
                f'alt=sse&key={self.api_key}'
        else:
            return f'{self.endpoint}/{self.model}:generateContent?' \
                f'key={self.api_key}'

    def get_headers(self):
        return {'Content-Type': 'application/json'}

    def _is_thinking_model(self):
        """Check if the current model supports thinking configuration.
        Gemini 2.5+ and 3.x models support thinking. Older models (1.x) do not.
        """
        if not self.model or 'gemini' not in self.model:
            return False
        # Gemini 2.5+ and 3.x models support thinking
        for prefix in ('gemini-2.5', 'gemini-2.6', 'gemini-3'):
            if self.model.startswith(prefix):
                return True
        # Also check for future models like gemini-4, gemini-5, etc.
        import re
        match = re.match(r'gemini-(\d+)', self.model)
        if match and int(match.group(1)) >= 3:
            return True
        return False

    def _get_thinking_family(self):
        """Determine the thinking parameter family for the current model.
        Returns: 'gemini3' for thinkingLevel, 'gemini25' for thinkingBudget,
                 or None for non-thinking models.
        """
        if not self._is_thinking_model():
            return None
        # Gemini 3.x uses thinkingLevel
        if self.model.startswith('gemini-3'):
            return 'gemini3'
        # Gemini 2.5/2.6+ uses thinkingBudget
        return 'gemini25'

    def _supports_thinking_level(self):
        """Check if the model supports thinkingLevel (Gemini 3.0+)."""
        return self._get_thinking_family() == 'gemini3'

    @staticmethod
    def _normalize_thinking(val):
        """Normalize old/misspelled thinking values to current format."""
        if val in ('default[disable]',):
            return 'default'
        if val in ('med', 'meduime'):
            return 'medium'
        return val

    def get_body(self, text):
        thinking_config = None
        if self._is_thinking_model():
            level = self._normalize_thinking(self.thinking)
            # Always exclude thought text from response output
            thinking_config = {"includeThoughts": False}
            family = self._get_thinking_family()

            if family:
                if family == 'gemini3':
                    # Gemini 3.x: use thinkingLevel (MINIMAL, LOW, MEDIUM, HIGH)
                    # REST API requires UPPERCASE strings for these enums
                    if level != 'default':
                        thinking_config["thinkingLevel"] = level.upper()
                elif family == 'gemini25' and level != 'default':
                    # Gemini 2.5: use thinkingBudget (0=disable, -1=dynamic)
                    if level == 'disable':
                        thinking_config["thinkingBudget"] = 0
                    elif level == 'dynamic':
                        thinking_config["thinkingBudget"] = -1

        generation_config = {
            "temperature": self.temperature,
            "topP": self.top_p,
            "topK": self.top_k,
        }
        # Only add thinkingConfig if it actually contains a setting
        if thinking_config and len(thinking_config) > 1:
            generation_config["thinkingConfig"] = thinking_config

        body = {
            "contents": [
                {"role": "user", "parts": [{"text": self._prompt(text)}]},
            ],
            "generationConfig": generation_config,
            "safetySettings": [
                {
                    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_HATE_SPEECH",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_HARASSMENT",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "BLOCK_NONE"
                }
            ]
        }

        return json.dumps(body)

    def get_result(self, response):
        if self.stream:
            return self._parse_stream(response)
        data = json.loads(response)
        usage = data.get('usageMetadata', {})
        self.usage_data['prompt_tokens'] = usage.get('promptTokenCount', 0)
        self.usage_data['completion_tokens'] = usage.get('candidatesTokenCount', 0)
        self.usage_data['total_tokens'] = usage.get('totalTokenCount', 0)
        parts = data['candidates'][0]['content']['parts']
        return ''.join([part['text'] for part in parts
                        if not part.get('thought', False)])

    def _parse_stream(self, response):
        while True:
            try:
                line = response.readline().decode('utf-8').strip()
            except IncompleteRead:
                continue
            except Exception as e:
                raise Exception(
                    _('Can not parse returned response. Raw data: {}')
                    .format(str(e)))
            if line.startswith('data:'):
                item = json.loads(line.split('data: ')[1])
                candidate = item['candidates'][0]
                content = candidate['content']
                if 'parts' in content.keys():
                    for part in content['parts']:
                        if not part.get('thought', False):
                            yield part['text']
                usage = item.get('usageMetadata')
                if usage:
                    self.usage_data['prompt_tokens'] = usage.get('promptTokenCount', 0)
                    self.usage_data['completion_tokens'] = usage.get('candidatesTokenCount', 0)
                    self.usage_data['total_tokens'] = usage.get('totalTokenCount', 0)
                if candidate.get('finishReason') == 'STOP':
                    break


class GeminiBatchTranslate:
    def __init__(self, translator):
        self.translator = translator
        self.translator.stream = False
        # self.translator.endpoint = 'https://generativelanguage.googleapis.com/v1beta/models'
        self.base_url = self.translator.endpoint.split('/v1beta')[0]
        self.file_endpoint = f'{self.base_url}/upload/v1beta/files'
        self.batch_endpoint = f'{self.base_url}/v1beta/models'
        self.max_output_tokens = None  # None = default, int = explicit limit
        self.dynamic_chunking = False

    def get_model_output_limit(self):
        """Fetch the model's outputTokenLimit from the Gemini API."""
        try:
            url = f"{self.batch_endpoint}/{self.translator.model}?key={self.translator.api_key}"
            response = request(
                url, method='GET', proxy_uri=self.translator.proxy_uri)
            data = json.loads(response)
            return data.get('outputTokenLimit')
        except Exception:
            return 8192  # Safe default for most Gemini models

    def _get_reserve_percent(self):
        """Return the percentage of output tokens to reserve for thinking/safety."""
        level = self.translator.thinking
        if level == 'minimal': return 0.10
        if level == 'low': return 0.20
        if level == 'medium': return 0.35
        if level == 'high': return 0.55
        return 0.20 # Default fallback

    def get_chunking_details(self, paragraphs):
        """Return a list of dicts with chunking details for display.
        
        Each dict contains:
          - batch_index: int
          - num_paragraphs: int
          - input_chars: int
          - input_tokens_est: int (estimated)
          - output_tokens_est: int (estimated)
          - reserve_percent: float
          - reserve_tokens: int
          - safe_chars: int
          - has_context: bool
        """
        if not self.dynamic_chunking:
            return self._get_fixed_chunking_details(paragraphs)
        return self._get_dynamic_chunking_details(paragraphs)

    def _get_fixed_chunking_details(self, paragraphs):
        limit = self.max_output_tokens or self.get_model_output_limit() or 8192
        reserve = self._get_reserve_percent()
        safe_chars = int(((limit * (1.0 - reserve)) / 2.5) * 3.0)
        details = []
        for i in range(0, len(paragraphs), 10):
            chunk = paragraphs[i:i+10]
            input_chars = sum(len(p.original) for p in chunk)
            details.append({
                'batch_index': i,
                'num_paragraphs': len(chunk),
                'input_chars': input_chars,
                'input_tokens_est': max(1, input_chars // 3),
                'output_tokens_est': int(input_chars // 3 * 2.5),
                'reserve_percent': reserve,
                'reserve_tokens': int(limit * reserve),
                'safe_chars': safe_chars,
                'has_context': False,
            })
        return details

    def _get_dynamic_chunking_details(self, paragraphs):
        limit = self.max_output_tokens or self.get_model_output_limit() or 8192
        reserve = self._get_reserve_percent()
        safe_chars = int(((limit * (1.0 - reserve)) / 2.5) * 3.0)
        details = []
        current_chunk = []
        current_chars = 0
        last_context = []
        chunk_index = 0
        p_index = 0
        
        while p_index < len(paragraphs):
            p = paragraphs[p_index]
            p_len = len(p.original)
            
            if current_chunk and current_chars + p_len > safe_chars:
                has_context = len(last_context) > 0
                details.append({
                    'batch_index': chunk_index,
                    'num_paragraphs': len(current_chunk),
                    'input_chars': current_chars,
                    'input_tokens_est': max(1, current_chars // 3),
                    'output_tokens_est': int(current_chars // 3 * 2.5),
                    'reserve_percent': reserve,
                    'reserve_tokens': int(limit * reserve),
                    'safe_chars': safe_chars,
                    'has_context': has_context,
                })
                last_context = current_chunk[-2:] if len(current_chunk) >= 2 else current_chunk[:]
                chunk_index += len(current_chunk)
                current_chunk = []
                current_chars = 0
            
            current_chunk.append(p)
            current_chars += p_len
            p_index += 1
        
        if current_chunk:
            has_context = len(last_context) > 0
            details.append({
                'batch_index': chunk_index,
                'num_paragraphs': len(current_chunk),
                'input_chars': current_chars,
                'input_tokens_est': max(1, current_chars // 3),
                'output_tokens_est': int(current_chars // 3 * 2.5),
                'reserve_percent': reserve,
                'reserve_tokens': int(limit * reserve),
                'safe_chars': safe_chars,
                'has_context': has_context,
            })
        
        return details

    def build_jsonl(self, paragraphs):
        """Build the JSONL content for batch requests."""
        if not self.dynamic_chunking:
            return self._build_fixed_chunks(paragraphs)
        return self._build_dynamic_chunks(paragraphs)

    def _build_fixed_chunks(self, paragraphs):
        lines = []
        for i in range(0, len(paragraphs), 10):
            chunk = paragraphs[i:i+10]
            combined_text = ""
            for j, p in enumerate(chunk):
                combined_text += f"[{j}] {p.original}\n\n"
            lines.append(self._create_batch_request(combined_text, i))
        return lines

    def _build_dynamic_chunks(self, paragraphs):
        lines = []
        # If user selected a custom limit in UI, use it, otherwise fetch model default
        limit = self.max_output_tokens or self.get_model_output_limit() or 8192
        reserve = self._get_reserve_percent()
        # Safe input tokens = Total Limit * (1 - Reserve) / 2.5 (growth factor)
        # Growth factor 2.5 accounts for verbose target languages like Thai
        # We use characters as a proxy for tokens (1 token ~= 3 chars)
        safe_chars = int(((limit * (1.0 - reserve)) / 2.5) * 3.0)
        current_chunk = []
        current_chars = 0
        last_context = []
        chunk_index = 0
        p_index = 0
        
        while p_index < len(paragraphs):
            p = paragraphs[p_index]
            p_len = len(p.original)
            
            # Split if adding this paragraph would exceed the safe character threshold
            if current_chunk and current_chars + p_len > safe_chars:
                lines.append(self._create_dynamic_request(current_chunk, last_context, chunk_index))
                last_context = current_chunk[-2:] if len(current_chunk) >= 2 else current_chunk[:]
                chunk_index += len(current_chunk)
                current_chunk = []
                current_chars = 0
            
            current_chunk.append(p)
            current_chars += p_len
            p_index += 1
            
        if current_chunk:
            lines.append(self._create_dynamic_request(current_chunk, last_context, chunk_index))
        return lines

    def _create_dynamic_request(self, chunk, context, base_index):
        combined_text = ""
        if context:
            combined_text += "--- CONTEXT (Do not translate) ---\n"
            for p in context:
                combined_text += f"{p.original}\n\n"
            combined_text += "--- END CONTEXT ---\n\n"
        for i, p in enumerate(chunk):
            combined_text += f"[{i}] {p.original}\n\n"
        return self._create_batch_request(combined_text, base_index)

    def _create_batch_request(self, text, start_idx):
        body = json.loads(self.translator.get_body(text))
        prompt_ext = (
            "\nTranslate the following paragraphs separately. "
            "Maintain the index prefix [0], [1], etc. for each translation. "
            "Return them in the same order.")
        body['contents'][0]['parts'][0]['text'] += prompt_ext
        for item in body.get('contents', []):
            item.pop('role', None)

        # Build the standard GenerateContentRequest structure (Must use snake_case for REST batch API)
        batch_request = {
            "contents": body["contents"]
        }
        # Include generation_config if present (convert from camelCase)
        if "generationConfig" in body:
            gc = body["generationConfig"]
            if gc:
                # Convert camelCase keys to snake_case for REST batch API
                generation_config = {}
                for k, v in gc.items():
                    snake_key = ''.join(['_' + c.lower() if c.isupper() else c for c in k]).lstrip('_')
                    generation_config[snake_key] = v
                batch_request["generation_config"] = generation_config
            
            # Apply explicit token limit if set
            if self.max_output_tokens is not None:
                if "generation_config" not in batch_request:
                    batch_request["generation_config"] = {}
                # Respect the user's selected limit (e.g. Max)
                batch_request["generation_config"]["max_output_tokens"] = int(self.max_output_tokens)

        # Include safety_settings if present (convert from camelCase)
        if "safetySettings" in body:

            batch_request["safety_settings"] = body["safetySettings"]

        return json.dumps({
            "key": f"chunk_{start_idx}",
            "request": batch_request
        }, ensure_ascii=False)

    def upload(self, paragraphs):
        """Upload the original content and retrieve the file name."""
        self._paragraphs = paragraphs
        lines = self.build_jsonl(paragraphs)

        content = ('\n'.join(lines) + '\n').encode('utf-8')
        return self._upload_content(content)

    def _upload_content(self, content):
        """Internal helper to upload bytes to the File API."""
        num_bytes = len(content)

        # Initial resumable request
        headers = {
            'x-goog-api-key': self.translator.api_key,
            'X-Goog-Upload-Protocol': 'resumable',
            'X-Goog-Upload-Command': 'start',
            'X-Goog-Upload-Header-Content-Length': str(num_bytes),
            'X-Goog-Upload-Header-Content-Type': 'application/json',
            'Content-Type': 'application/json',
        }
        display_name = f"batch_{uuid.uuid4().hex[:8]}"
        payload = json.dumps({'file': {'displayName': display_name}})

        response = request(
            self.file_endpoint, data=payload, headers=headers, method='POST',
            proxy_uri=self.translator.proxy_uri, raw_object=True)

        upload_url = response.info().get('x-goog-upload-url')

        # Upload bytes
        headers = {
            'Content-Length': str(num_bytes),
            'X-Goog-Upload-Offset': '0',
            'X-Goog-Upload-Command': 'upload, finalize',
        }
        response = request(
            upload_url, data=content, headers=headers, method='POST',
            proxy_uri=self.translator.proxy_uri)

        return json.loads(response)['file']['name']  # e.g. "files/..."

    def create(self, file_id):
        url = f"{self.batch_endpoint}/{self.translator.model}:batchGenerateContent?key={self.translator.api_key}"
        # Correct structure for Gemini Batch API GenerateBatchContentRequest
        payload = {
            "batch": {
                "display_name": f"ebook_translator_batch_{uuid.uuid4().hex[:8]}",
                "input_config": {
                    "file_name": file_id
                }
            }
        }
        response = request(
            url, data=json.dumps(payload), headers={'Content-Type': 'application/json'},
            method='POST', proxy_uri=self.translator.proxy_uri)
        return json.loads(response)['name'] # e.g. "batches/..."

    def check(self, batch_id):
        url = f"{self.base_url}/v1beta/{batch_id}?key={self.translator.api_key}"
        response = request(
            url, method='GET', proxy_uri=self.translator.proxy_uri)

        data = json.loads(response)

        metadata = data.get('metadata') or {}
        state = data.get('state') or metadata.get('state')
        create_time = data.get('createTime') or metadata.get('createTime') or \
                      data.get('create_time') or metadata.get('create_time')

        status = 'unknown'
        if not state:
            status = 'processing'
        elif state.endswith('_PENDING'):
            status = 'pending'
        elif state.endswith('_RUNNING') or state.endswith('_ACTIVE'):
            status = 'processing'
        elif state.endswith('_SUCCEEDED') or state.endswith('_COMPLETED'):
            status = 'completed'
        elif state.endswith('_FAILED'):
            status = 'failed'
        elif state.endswith('_CANCELLED') or state.endswith('_CANCELED'):
            status = 'cancelled'
        elif state.endswith('_EXPIRED'):
            status = 'expired'
        else:
            status = 'processing'

        response_data = data.get('response') or {}
        output_file_id = response_data.get('responsesFile') or \
                         response_data.get('responses_file') or \
                         data.get('dest', {}).get('file_name')

        request_counts = data.get('requestCounts') or \
                         data.get('request_counts') or \
                         metadata.get('requestCounts') or \
                         metadata.get('request_counts') or \
                         data.get('metadata', {}).get('request_counts')

        result = {
            'status': status,
            'state': state,
            'create_time': create_time,
            'output_file_id': output_file_id,
            'request_counts': request_counts,
            'errors': data.get('error')
        }
        return result

    def retrieve(self, output_file_id):
        # System-generated files in Gemini (like batch results) require a specific /download/ path and :download suffix
        url = f"{self.base_url}/download/v1beta/{output_file_id}:download?alt=media&key={self.translator.api_key}"
        response = request(
            url, method='GET', proxy_uri=self.translator.proxy_uri, raw_object=True)

        translations = {}
        content = response.read().decode('utf-8')
        total_prompt = 0
        total_completion = 0
        total_tokens = 0
        total_thinking = 0
        
        for line in content.splitlines():
            if not line.strip():
                continue
            result = json.loads(line)
            key = result.get('key') # chunk_0, chunk_10, etc.
            if not key or not key.startswith('chunk_'):
                continue
            
            chunk_index = int(key.split('_')[1])
            resp = result.get('response')
            if resp:
                # Accumulate usage tokens
                usage = resp.get('usageMetadata', {})
                total_prompt += usage.get('promptTokenCount', 0)
                total_completion += usage.get('candidatesTokenCount', 0)
                total_tokens += usage.get('totalTokenCount', 0)
                total_thinking += usage.get('thoughtsTokenCount', 0)
                
                try:
                    parts = resp['candidates'][0]['content']['parts']
                    # Filter out thought parts — thinking models include
                    # parts with "thought": true that aren't translations
                    non_thought_parts = [p for p in parts if not p.get('thought', False)]
                    combined_translation = ''.join([p['text'] for p in non_thought_parts])
                    
                    # Explicitly look for [n] and the text following it
                    # This ensures that if the model skips an index (e.g. #3), the mapping doesn't shift.
                    import re
                    # Find all occurrences of [n] followed by content until the next [n] or end of string
                    matches = re.finditer(r'\[(\d+)\]\s*(.*?)(?=\s*\[\d+\]|$)', combined_translation, re.DOTALL)
                    
                    for match in matches:
                        j = int(match.group(1))
                        translated_text = match.group(2).strip()
                        
                        if chunk_index + j < len(self._paragraphs):
                            para = self._paragraphs[chunk_index + j]
                            translations[para.md5] = translated_text
                except Exception as e:
                    pass
        
        # Update translator's usage data with batch totals
        self.translator.usage_data['prompt_tokens'] = total_prompt
        self.translator.usage_data['completion_tokens'] = total_completion
        self.translator.usage_data['total_tokens'] = total_tokens
        self.translator.usage_data['thinking_tokens'] = total_thinking
        
        return translations

    def cancel(self, batch_id):
        url = f"{self.base_url}/v1beta/{batch_id}:cancel?key={self.translator.api_key}"
        request(url, data=b'', method='POST',
                headers={'Content-Type': 'application/json'},
                proxy_uri=self.translator.proxy_uri)
        return True

    def delete(self, file_id):
        url = f"{self.base_url}/v1beta/{file_id}?key={self.translator.api_key}"
        request(url, method='DELETE', proxy_uri=self.translator.proxy_uri)
        return True
