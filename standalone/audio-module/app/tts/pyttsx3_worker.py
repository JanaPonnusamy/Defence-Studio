"""One OS speech engine per child process: bounds hangs and COM thread lifetime."""
import json
import locale
import sys


def languages(voice):
    result = []
    for raw in voice.languages:
        value = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw)
        value = value.lstrip(''.join(chr(i) for i in range(32)))
        try:
            if '-' not in value and '_' not in value:
                value = locale.windows_locale.get(int(value, 16), value)
        except ValueError:
            pass
        result.append(value.replace('_', '-').lower())
    return result


def run(payload):
    import pyttsx3
    engine = pyttsx3.init()
    try:
        voices = [{'id': v.id, 'name': v.name, 'languages': languages(v)}
                  for v in engine.getProperty('voices')]
        if payload.get('inventory'):
            return {'voices': voices}
        requested = payload['language'].lower().replace('_', '-')
        candidates = voices
        if payload.get('voice_id'):
            candidates = [v for v in candidates if payload['voice_id'].lower() in
                          (v['id'].lower(), (v['name'] or '').lower())]
        candidates = [v for v in candidates if any(
            lang == requested or ('-' not in requested and lang.split('-')[0] == requested)
            for lang in v['languages'])]
        if not candidates:
            raise ValueError('No installed voice matches the requested voice and language')
        voice = candidates[0]
        engine.setProperty('voice', voice['id'])
        engine.setProperty('rate', payload['rate'])
        engine.setProperty('volume', payload['volume'])
        engine.save_to_file(payload['text'], payload['output'])
        engine.runAndWait()
        return {'voice': voice, 'rate': payload['rate'], 'volume': payload['volume']}
    finally:
        engine.stop()


if __name__ == '__main__':
    payload = json.loads(sys.stdin.read())
    try:
        result = run(payload)
    except Exception as exc:
        result = {'error': type(exc).__name__ + ': ' + str(exc)}
    with open(payload['metadata'], 'w', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False)
