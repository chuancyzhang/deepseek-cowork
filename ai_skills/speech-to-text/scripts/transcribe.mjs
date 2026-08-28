import {spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';


function fail(code, message) {
  const payload = JSON.stringify({ok: false, error: {code, message}});
  process.stdout.write(payload);
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
}

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith('--') || value === undefined) {
      throw new Error(`Invalid argument near ${key || '<empty>'}`);
    }
    values[key.slice(2)] = value;
  }
  return values;
}

function nodeModulesRoot() {
  const candidates = String(process.env.NODE_PATH || '').split(path.delimiter).filter(Boolean);
  const root = candidates[0];
  if (!root) {
    throw new Error('NODE_PATH is missing for the speech-to-text Skill runtime.');
  }
  return root;
}

function run(executable, args, options = {}) {
  const result = spawnSync(executable, args, {
    encoding: 'utf8',
    windowsHide: true,
    maxBuffer: 256 * 1024 * 1024,
    ...options,
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    const detail = String(result.stderr || result.stdout || `exit code ${result.status}`).trim();
    throw new Error(detail.slice(-4000));
  }
  return String(result.stdout || '');
}

function timestampValue(value) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (Array.isArray(value) && value.length) {
    const numbers = value.map(Number).filter(Number.isFinite);
    if (numbers.length) {
      return numbers.reduce((sum, item) => sum + item, 0) / numbers.length;
    }
  }
  return Number.NaN;
}

function speakerAt(timestamp, segments) {
  const active = [...new Set(
    segments
      .filter(segment => timestamp >= segment.start && timestamp <= segment.end)
      .map(segment => Number(segment.speaker)),
  )].sort((left, right) => left - right);
  if (!active.length) {
    return {label: 'Speaker ?', overlap: false};
  }
  return {
    label: active.map(speaker => `Speaker ${speaker + 1}`).join(' + '),
    overlap: active.length > 1,
  };
}

function cleanJoinedTokens(tokens) {
  return tokens
    .join('')
    .replace(/\s+/g, ' ')
    .replace(/\s+([,.;:!?，。；：！？])/g, '$1')
    .trim();
}

function alignTokens(tokens, timestamps, segments, duration) {
  if (!Array.isArray(tokens) || !Array.isArray(timestamps) || tokens.length !== timestamps.length) {
    throw new Error('ASR token timestamps are unavailable; speaker/text alignment cannot be performed reliably.');
  }
  const usable = [];
  for (let index = 0; index < tokens.length; index += 1) {
    const token = String(tokens[index] ?? '');
    if (!token || /^<\|.*\|>$/.test(token)) {
      continue;
    }
    const start = timestampValue(timestamps[index]);
    if (!Number.isFinite(start)) {
      throw new Error('ASR returned an invalid token timestamp.');
    }
    const next = index + 1 < timestamps.length ? timestampValue(timestamps[index + 1]) : Number.NaN;
    const end = Number.isFinite(next) && next >= start ? next : Math.min(Number(duration) || start + 0.3, start + 0.3);
    const speaker = speakerAt(start, segments);
    usable.push({token, start, end, ...speaker});
  }
  if (!usable.length) {
    throw new Error('ASR returned no timestamped text tokens.');
  }
  const turns = [];
  for (const item of usable) {
    const previous = turns.at(-1);
    if (previous && previous.speaker === item.label && previous.overlap === item.overlap) {
      previous.tokens.push(item.token);
      previous.end = Math.max(previous.end, item.end);
    } else {
      turns.push({
        speaker: item.label,
        overlap: item.overlap,
        start: item.start,
        end: item.end,
        tokens: [item.token],
      });
    }
  }
  return turns.map(turn => ({
    speaker: turn.speaker,
    overlap: turn.overlap,
    start: turn.start,
    end: turn.end,
    text: cleanJoinedTokens(turn.tokens),
  })).filter(turn => turn.text);
}

function formatTime(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(value / 60);
  const remainder = value - minutes * 60;
  return `${String(minutes).padStart(2, '0')}:${remainder.toFixed(2).padStart(5, '0')}`;
}

function formatTurns(turns) {
  return turns.map(turn => (
    `[${formatTime(turn.start)}–${formatTime(turn.end)}] ${turn.speaker}: ${turn.text}`
  )).join('\n\n');
}

function selfTest() {
  const turns = alignTokens(
    ['你', '好', ' hello'],
    [0.1, 0.4, 1.2],
    [
      {speaker: 0, start: 0, end: 0.9},
      {speaker: 1, start: 1.0, end: 2.0},
    ],
    2,
  );
  if (turns.length !== 2 || turns[0].speaker !== 'Speaker 1' || turns[1].speaker !== 'Speaker 2') {
    throw new Error('Speaker alignment self-test failed.');
  }
  process.stdout.write(JSON.stringify({ok: true, turns}));
}

async function main() {
  if (process.argv.includes('--self-test')) {
    selfTest();
    return;
  }
  const args = parseArgs(process.argv.slice(2));
  const input = path.resolve(args.input || '');
  const language = args.language || 'auto';
  const diarize = args.diarize !== 'false';
  const speakerCount = Number.parseInt(args['speaker-count'] || '0', 10);
  const nodeModules = nodeModulesRoot();
  const require = createRequire(import.meta.url);
  const ffmpegPath = require(path.join(nodeModules, 'ffmpeg-static'));
  const sherpa = require(path.join(nodeModules, 'sherpa-onnx-node'));
  const tempRoot = mkdtempSync(path.join(tmpdir(), 'cowork-speech-to-text-'));
  const wavePath = path.join(tempRoot, 'normalized.wav');
  try {
    run(ffmpegPath, [
      '-hide_banner', '-loglevel', 'error', '-y', '-i', input,
      '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', wavePath,
    ]);
    const wave = sherpa.readWave(wavePath);
    const duration = wave.samples.length / wave.sampleRate;
    const recognizer = new sherpa.OfflineRecognizer({
      featConfig: {
        sampleRate: 16000,
        featureDim: 80,
      },
      modelConfig: {
        senseVoice: {
          model: path.resolve(args['sensevoice-model'] || ''),
          language,
          useInverseTextNormalization: 1,
        },
        tokens: path.resolve(args['sensevoice-tokens'] || ''),
        numThreads: 2,
        provider: 'cpu',
        debug: 0,
      },
      decodingMethod: 'greedy_search',
    });
    const asrStream = recognizer.createStream();
    asrStream.acceptWaveform({sampleRate: wave.sampleRate, samples: wave.samples});
    recognizer.decode(asrStream);
    const asr = recognizer.getResult(asrStream);
    const rawText = String(asr.text || '').trim();
    if (!rawText) {
      throw new Error('The local ASR model returned empty text.');
    }

    let transcript = rawText;
    let turns = [];
    let segments = [];
    let actualSpeakerCount = 0;
    if (diarize) {
      const diarizer = new sherpa.OfflineSpeakerDiarization({
        segmentation: {
          pyannote: {
            model: path.resolve(args['segmentation-model'] || ''),
            windowShiftRatio: 0.1,
          },
        },
        embedding: {model: path.resolve(args['embedding-model'] || '')},
        clustering: {
          numClusters: speakerCount > 0 ? speakerCount : -1,
          threshold: 0.5,
        },
        minDurationOn: 0.2,
        minDurationOff: 0.5,
      });
      if (diarizer.sampleRate !== wave.sampleRate) {
        throw new Error(`Diarization expected ${diarizer.sampleRate} Hz but received ${wave.sampleRate} Hz.`);
      }
      segments = diarizer.process(wave.samples).map(segment => ({
        speaker: Number(segment.speaker),
        start: Number(segment.start),
        end: Number(segment.end),
      }));
      if (!segments.length) {
        throw new Error('Speaker diarization returned no segments.');
      }
      turns = alignTokens(asr.tokens, asr.timestamps, segments, duration);
      transcript = formatTurns(turns);
      actualSpeakerCount = new Set(segments.map(segment => segment.speaker)).size;
      if (!actualSpeakerCount) {
        throw new Error('Speaker diarization did not identify any speaker cluster.');
      }
    }

    process.stdout.write(JSON.stringify({
      ok: true,
      transcript,
      raw_text: rawText,
      model: 'sensevoice',
      lang: asr.lang || '',
      emotion: asr.emotion || '',
      event: asr.event || '',
      duration,
      diarized: diarize,
      speaker_count: actualSpeakerCount,
      turn_count: turns.length,
      segment_count: segments.length,
    }));
  } finally {
    rmSync(tempRoot, {recursive: true, force: true});
  }
}

main().catch(error => fail('local_transcription_failed', error?.message || String(error)));
