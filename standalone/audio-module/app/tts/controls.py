"""Original delivery targets; no reference identity or native emotion claim."""
import hashlib
import json
import math

PRESETS = {
    'neutral': {'rate_wpm': 170, 'volume': 0.85, 'pitch_semitones': 0},
    'serious': {'rate_wpm': 150, 'volume': 0.85, 'pitch_semitones': 0},
    'urgent': {'rate_wpm': 195, 'volume': 0.95, 'pitch_semitones': 0},
    'calm': {'rate_wpm': 140, 'volume': 0.75, 'pitch_semitones': 0},
    'documentary': {'rate_wpm': 160, 'volume': 0.95, 'pitch_semitones': 0},
}
PROFILE = {
    'id': 'original-defence-briefing', 'version': '1.0',
    'description': 'Independent OS narrator for clear defence briefings; not a speaker imitation.',
    'reference_analysis': 'unavailable; editorial targets, not extracted speaker measurements',
    'provider': 'local-pyttsx3', 'language': 'en', 'presets': PRESETS,
    'controls': {'rate_wpm': {'range': [80, 260], 'kind': 'native requested WPM; measure output'},
                 'volume': {'range': [0, 1], 'kind': 'native amplitude'},
                 'pitch_semitones': {'range': [-3, 3], 'kind': 'librosa post-processing; not native'}},
    'emotion': 'rate/volume approximation only; no native emotional TTS',
    'pauses': 'provider punctuation; explicit pause-duration control unsupported',
    'pronunciation': 'verbatim Unicode NFC; spell acronyms in input for operator-controlled pronunciation',
    'emphasis': 'provider punctuation only; no guaranteed word emphasis',
}
PROFILE['sha256'] = hashlib.sha256(json.dumps(PROFILE, sort_keys=True).encode()).hexdigest()


def resolve_controls(controls=None, plan=None):
    controls = controls or {}
    unknown = set(controls) - {'preset', 'rate_wpm', 'volume', 'pitch_semitones'}
    if unknown:
        raise ValueError('Unsupported controls: ' + ', '.join(sorted(unknown)))
    preset = controls.get('preset', 'neutral')
    if preset not in PRESETS:
        raise ValueError('Unknown delivery preset')
    result = dict(PRESETS[preset])
    if plan and 'preset' not in controls:
        first = plan[0]
        result['rate_wpm'] = {'slow': 130, 'moderate': 170, 'fast': 210}.get(first.get('pace'), 170)
        result['volume'] = {'low': .6, 'controlled-high': .85, 'high': 1.0}.get(first.get('energy'), .85)
    result.update({k: v for k, v in controls.items() if k != 'preset'})
    for key, low, high in [('rate_wpm', 80, 260), ('volume', 0, 1), ('pitch_semitones', -3, 3)]:
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'{key} must be finite and between {low} and {high}')
    return result
