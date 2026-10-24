"""Acoustic word timestamps, bounded to local verified PCM and a pinned CPU model."""
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from difflib import SequenceMatcher

MODEL_REVISION = '536b0662742c02347bc0e980a01041f333bce120'
MODEL_PATH = os.getenv('VIDEO_ALIGNMENT_MODEL_PATH', '/opt/postbridge-models/whisper-small')


def normalized(text):
    return ''.join(c for c in text.casefold().replace('ё', 'е') if c.isalnum())


def matched_words(text, recognized, duration):
    """Keep the approved transcript; reject missing or unreliable acoustic words."""
    tokens = re.findall(r'\S+', text)
    expected = ''.join(normalized(t) for t in tokens)
    actual, clocks = '', []
    previous = 0
    for word in recognized:
        start, end, probability = (float(word[k]) for k in ('start', 'end', 'probability'))
        if not all(math.isfinite(v) for v in (start, end, probability)) or not 0 <= start < end <= duration + .1 or start < previous - .1 or not 0 <= probability <= 1:
            return None
        value = normalized(word['word'])
        actual += value; clocks.extend([(start, min(duration, end))] * len(value)); previous = end
    if not recognized or sum(float(w['probability']) for w in recognized) / len(recognized) < .35:
        return None
    if not expected or not actual:
        return None
    matcher = SequenceMatcher(None, expected, actual, autojunk=False)
    if matcher.ratio() < .9:
        return None
    positions = {a+i: b+i for a,b,n in matcher.get_matching_blocks() for i in range(n)}
    cues, offset, previous = [], 0, 0
    for token in tokens:
        size = len(normalized(token))
        matches = [clocks[positions[i]] for i in range(offset, offset+size) if i in positions]
        offset += size
        if not size:  # Attach standalone punctuation to the preceding spoken token.
            if cues: cues[-1]['text'] += ' '+token
            continue
        if len(matches) < max(1, math.ceil(size * .7)):
            return None
        start, end = max(previous, matches[0][0]), matches[-1][1]
        if end <= start:
            return None
        cues.append({'text':token,'start':round(start,3),'end':round(end,3)}); previous=end
    return cues or None


def align_words(path, text, language):
    if not Path(MODEL_PATH, 'model.bin').is_file():
        return None
    try:
        result = subprocess.run([sys.executable, __file__, '--align'],
            input=json.dumps({'path':str(path),'text':text,'language':language}), text=True,
            capture_output=True, timeout=90, check=True,
            env={**os.environ, 'OMP_NUM_THREADS':'2', 'HF_HUB_OFFLINE':'1'})
        body=json.loads(result.stdout)
        from postbridge.services.video_media import inspect_speech
        return matched_words(text, body, inspect_speech(path)/24000)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None  # Preserve measured phrase subtitles; never fabricate word clocks.


def main():
    if '--download-model' in sys.argv:
        from huggingface_hub import snapshot_download
        snapshot_download('Systran/faster-whisper-small', revision=MODEL_REVISION,
            local_dir=MODEL_PATH, allow_patterns=['model.bin','config.json','tokenizer.json','vocabulary.*'])
        return
    from faster_whisper import WhisperModel
    body=json.load(sys.stdin)
    import wave
    with wave.open(body['path'], 'rb') as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1,2,24000)
        assert 0 < audio.getnframes() <= 360000
    model=WhisperModel(MODEL_PATH, device='cpu', compute_type='int8', cpu_threads=2, num_workers=1, local_files_only=True)
    from faster_whisper.audio import decode_audio, pad_or_trim
    from faster_whisper.tokenizer import Tokenizer
    audio=decode_audio(body['path'], sampling_rate=16000)
    features=model.feature_extractor(audio)
    tokenizer=Tokenizer(model.hf_tokenizer, model.model.is_multilingual, task='transcribe', language=body['language'])
    # Align the known approved transcript directly against acoustic attention.
    # Do not substitute an independently recognized transcript.
    aligned=model.find_alignment(tokenizer,[tokenizer.encode(body['text'])],model.encode(pad_or_trim(features)),features.shape[-1]-1)
    print(json.dumps([{k:float(w[k]) if k != 'word' else w[k] for k in ('word','start','end','probability')}
        for w in aligned[0] if normalized(w['word'])]))



if __name__ == '__main__':
    main()
