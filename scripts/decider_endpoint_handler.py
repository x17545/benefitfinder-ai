"""HF custom-handler template. Deploy only to an explicitly approved endpoint.

Install this project and its locked decider dependency group in the serving image;
copy the verified pinned snapshot (including revision.txt) to the handler's path.
The endpoint's platform owns authentication, pricing and concurrency limits.
"""

from pathlib import Path

from benefitfinder.infrastructure.decider_client import LocalDecider


class EndpointHandler:
    def __init__(self, path: str = '') -> None:
        self.client = LocalDecider(Path(path), max_length=4096, batch_tokens=4096)

    def __call__(self, data: dict) -> dict:
        value = data.get('inputs')
        if not isinstance(value, dict):
            raise ValueError('decider_invalid_request')
        if value.get('operation') == 'capabilities':
            identity = self.client.identity
            return {'model': identity['id'], 'revision': identity['revision'],
                    'offsets': 'unicode_codepoints', 'fast': False,
                    'tasks': ['locate', 'choice', 'noul', 'score'],
                    'limits': {'max_length': 4096, 'max_requests': 8}}
        if value.get('operation') != 'decide':
            raise ValueError('decider_unsupported_operation')
        requests = value.get('requests')
        if not isinstance(requests, list) or not 1 <= len(requests) <= 8:
            raise ValueError('decider_invalid_batch')
        return {'responses': self.client.decide(requests)}
