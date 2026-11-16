"""Measure native locate and decision memory on this GPU, with explicit length failures."""

import argparse
import json
from pathlib import Path

from benefitfinder.application.decider import DeciderError
from benefitfinder.infrastructure.decider_client import LocalDecider, private_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, default=Path('data/local/decider/checkpoint'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-length', type=int, default=2048)
    parser.add_argument('--batch-tokens', type=int, default=2048)
    args = parser.parse_args()
    client = LocalDecider(args.checkpoint, max_length=args.max_length, batch_tokens=args.batch_tokens)
    probes = []
    # Synthetic text measures capacity, never policy extraction accuracy.
    for length in (128, 512, 1024, 2048):
        state = '가상 공고입니다. 매월 10만원을 지급합니다.\n' * (length // 24 + 1)
        for batch_size in (1, 2, 4):
            request = {'state': state, 'questions': {
                'amount': {'type': 'locate', 'instructions': '지급 금액'},
                'fixed': {'type': 'noul', 'instructions': '고정 금액을 명시했는가?'},
            }}
            error = None
            try:
                responses = client.decide([request for _ in range(batch_size)])
                usage = sum(response['usage']['input_tokens'] for response in responses)
            except DeciderError as exc:
                error, usage = str(exc), 0
            probe = {'synthetic_characters': len(state), 'requests': batch_size,
                     'input_tokens': usage, 'error': error, 'measurement': client.metrics[-1]}
            probes.append(probe)
            private_json(args.output, {'model': client.identity, 'probes': probes,
                                       'all_measurements': client.metrics})
            print(json.dumps(probe), flush=True)
            if error:
                break
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
