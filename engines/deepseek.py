import json
from typing import Any

from .openai import ChatgptTranslate

load_translations()  # type: ignore


class DeepseekTranslate(ChatgptTranslate):
    name = 'DeepSeek'
    alias = 'DeepSeek (Chat)'
    endpoint = 'https://api.deepseek.com/v1/chat/completions'
    temperature = 1.3

    concurrency_limit = 0
    request_interval = 0.0

    models: list[str] = [
        'deepseek-chat', 'deepseek-reasoner',
        'deepseek-v4-flash', 'deepseek-v4-pro',
    ]
    model: str | None = models[0]

    # Valid DeepSeek reasoning_effort values: 'high', 'max'
    valid_reasoning_efforts = ('high', 'max')

    def __init__(self):
        super().__init__()
        self.model = self.config.get('model', self.model)

    def get_models(self):
        return self.models

    def get_body(self, text):
        """Build request body with proper DeepSeek thinking mode parameters.

        DeepSeek API spec:
        - thinking.type: 'enabled' or 'disabled' (default: enabled for reasoner)
        - reasoning_effort: 'high' or 'max' (default: 'high')
          These are the ONLY valid values — no 'low', 'medium', etc.
        """
        body: dict[str, Any] = {
            'model': self.model,
            'messages': [
                {'role': 'system', 'content': self.get_prompt()},
                {'role': 'user', 'content': text}
            ],
        }

        # DeepSeek thinking mode configuration
        if self.thinking in ('default[disable]', 'disabled', 'disable'):
            body['thinking'] = {'type': 'disabled'}
        elif self.thinking and self.thinking != 'default':
            body['thinking'] = {'type': 'enabled'}
            # Only accept valid DeepSeek reasoning_effort values
            if self.thinking in self.valid_reasoning_efforts:
                body['reasoning_effort'] = self.thinking
            else:
                # Fallback to 'high' for any unrecognized value
                body['reasoning_effort'] = 'high'

        if self.stream:
            body.update(stream=True)
        sampling_value = getattr(self, self.sampling)
        body.update({self.sampling: sampling_value})
        return json.dumps(body)
