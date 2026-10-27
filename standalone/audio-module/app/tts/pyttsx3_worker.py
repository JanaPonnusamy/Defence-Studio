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


ONECORE_VOICES = r'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices'


def onecore_tokens():
    """Windows OneCore voices (e.g. Tamil 'Valluvar') are SAPI-compatible tokens
    that the classic SAPI5 category used by pyttsx3 does not list."""
    if sys.platform != 'win32':
        return []
    try:
        import comtypes.client
        category = comtypes.client.CreateObject('SAPI.SpObjectTokenCategory')
        category.SetId(ONECORE_VOICES, False)
        tokens = category.EnumerateTokens()
        out = []
        for i in range(tokens.Count):
            token = tokens.Item(i)
            lcid = token.GetAttribute('Language').split(';')[0]
            lang = locale.windows_locale.get(int(lcid, 16), lcid).replace('_', '-').lower()
            out.append(({'id': token.Id, 'name': token.GetDescription(), 'languages': [lang],
                         'engine': 'onecore'}, token))
        return out
    except Exception:
        return []


def matching(voices, payload):
    requested = payload['language'].lower().replace('_', '-')
    if payload.get('voice_id'):
        voices = [v for v in voices if payload['voice_id'].lower() in
                  (v['id'].lower(), (v['name'] or '').lower())]
    return [v for v in voices if any(
        lang == requested or ('-' not in requested and lang.split('-')[0] == requested)
        for lang in v['languages'])]


def speak_onecore(token, payload):
    import math
    import comtypes.client
    voice = comtypes.client.CreateObject('SAPI.SpVoice')
    voice.Voice = token
    # SAPI rate is -10..10 around a ~200 wpm default, roughly 10% per step.
    voice.Rate = max(-10, min(10, round(math.log(payload['rate'] / 200) / math.log(1.1))))
    voice.Volume = max(0, min(100, round(payload['volume'] * 100)))
    stream = comtypes.client.CreateObject('SAPI.SpFileStream')
    stream.Open(payload['output'], 3, False)
    try:
        voice.AudioOutputStream = stream
        voice.Speak(payload['text'], 0)
    finally:
        stream.Close()


def run(payload):
    import pyttsx3
    engine = pyttsx3.init()
    try:
        voices = [{'id': v.id, 'name': v.name, 'languages': languages(v)}
                  for v in engine.getProperty('voices')]
        onecore = onecore_tokens()
        if payload.get('inventory'):
            return {'voices': voices + [v for v, _ in onecore]}
        candidates = matching(voices, payload)
        if not candidates:
            fallback = matching([v for v, _ in onecore], payload)
            if not fallback:
                raise ValueError('No installed voice matches the requested voice and language')
            voice = fallback[0]
            speak_onecore(dict((v['id'], t) for v, t in onecore)[voice['id']], payload)
            return {'voice': voice, 'rate': payload['rate'], 'volume': payload['volume']}
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
