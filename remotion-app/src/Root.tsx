import { Composition } from 'remotion';
import { SamuraiVideo } from './SamuraiVideo';
import { ChannelIntro } from './ChannelIntro';
import { ShortsComposition } from './ShortsVideo';
import manifest from '../public/manifest.json';

/**
 * Phase 8.5 expansion: three composition types.
 *   - "ChannelIntro"      — brand bumper (10s)
 *   - "RedSunJapanVideo"  — long-form 16:9 video
 *   - Shorts              — 9:16 Shorts (configurable per project)
 *
 * Each Short re-uses Whisper timestamps from the long-form audio via
 * /timestamps.json (already in public/). imageFilenames are project scenes
 * chosen for visual relevance to the narration in each clip window.
 *
 * NOTE: Currently configured for HOICHI (5 shorts). For Okiku, swap SHORTS
 * to the Okiku data (see git history for Okiku SHORTS array).
 */
type Manifest = {
  fps?: number;
  duration_seconds?: number;
  audio_duration_seconds?: number;
  intro_variant?: 'long' | 'short';
  intro_duration_sec?: number;
  intro_image_filenames?: string[];
};

const FALLBACK_DURATION_SEC = 60;
const FALLBACK_FPS = 30;

const M = manifest as Manifest;
const fps = M.fps ?? FALLBACK_FPS;
const durationSec = M.duration_seconds ?? ((M.audio_duration_seconds ?? FALLBACK_DURATION_SEC) + 5);
const introVariant: 'long' | 'short' = M.intro_variant ?? 'long';
const introDurationSec = M.intro_duration_sec ?? (introVariant === 'long' ? 10 : 5);

// HOICHI the Earless — 5 shorts
const SHORTS = [
  {
    id: 'Shorts1-footsteps',
    startSec: 75.59,
    endSec: 105.59,
    imageFilenames: [
      'hoichi-the-earless_scene03_00003_.png',
      'hoichi-the-earless_scene07_00003_.png',
      'hoichi-the-earless_scene11_00003_.png',
    ],
    highlight: 'The grip was like iron wrapped in silk',
    hookText: 'footsteps in the rain, and then a voice called his name.',
  },
  {
    id: 'Shorts2-he',
    startSec: 374.76,
    endSec: 404.76,
    imageFilenames: [
      'hoichi-the-earless_scene12_00003_.png',
      'hoichi-the-earless_scene16_00003_.png',
      'hoichi-the-earless_scene20_00003_.png',
    ],
    highlight: 'kyōgaku shita by the intensity of the response',
    hookText: 'he played the Heike, and the dead began to weep.',
  },
  {
    id: 'Shorts3-two',
    startSec: 490.53,
    endSec: 520.53,
    imageFilenames: [
      'hoichi-the-earless_scene16_00003_.png',
      'hoichi-the-earless_scene20_00003_.png',
      'hoichi-the-earless_scene24_00003_.png',
    ],
    highlight: 'a blind man at their center, who was playing his biwa with a fervor that bordered on possession',
    hookText: 'two servants followed him into the storm and saw the ghost-fires.',
  },
  {
    id: 'Shorts4-they',
    startSec: 627.59,
    endSec: 657.59,
    imageFilenames: [
      'hoichi-the-earless_scene20_00003_.png',
      'hoichi-the-earless_scene24_00003_.png',
      'hoichi-the-earless_scene28_00003_.png',
    ],
    highlight: 'the soles of his feet, the palms of his hands, his face',
    hookText: 'they painted his entire body with sacred text to hide him from spirits.',
  },
  {
    id: 'Shorts5-the',
    startSec: 945.36,
    endSec: 975.36,
    imageFilenames: [
      'hoichi-the-earless_scene30_00003_.png',
      'hoichi-the-earless_scene32_00003_.png',
      'hoichi-the-earless_scene32_00003_.png',
    ],
    highlight: 'jikai, the external ears alone, had been missed',
    hookText: 'the sutra covered him completely, except for his ears.',
  },
];

export const RemotionRoot: React.FC = () => {
  return (
    <>
      {/* Channel intro / bumper — plays before the main video */}
      <Composition
        id="ChannelIntro"
        component={ChannelIntro}
        durationInFrames={Math.floor(introDurationSec * fps)}
        fps={fps}
        width={1920}
        height={1080}
        defaultProps={{
          variant: introVariant,
          intro_image_filenames: M.intro_image_filenames ?? [
            'intro_01.png',
            'intro_02.jpg',
            'intro_03.jpg',
            'intro_04.png',
          ],
          logo_filename: 'red-sun-logo-background-tate.png',
          wordmark_text: '',
          taiko_filename: null,
        }}
      />

      {/* Main long-form video */}
      <Composition
        id="RedSunJapanVideo"
        component={SamuraiVideo}
        durationInFrames={Math.floor(durationSec * fps)}
        fps={fps}
        width={1920}
        height={1080}
        defaultProps={{
          titleJa: (M as any).title_ja ?? '',
          titleEn: (M as any).title_en ?? '',
        }}
      />

      {/* Dynamic 9:16 Shorts Composition (Passo #15) */}
      <Composition
        id="ShortVideo"
        component={ShortsComposition}
        durationInFrames={Math.round(30 * fps)}
        fps={fps}
        width={1080}
        height={1920}
        calculateMetadata={({ props }) => {
          const start = props?.short?.startSec ?? 0;
          const end = props?.short?.endSec ?? 30;
          const currentFps = (props as { fps?: number })?.fps ?? fps;
          const durFrames = Math.max(Math.round((end - start) * currentFps), 30);
          return {
            durationInFrames: durFrames,
            fps: currentFps,
          };
        }}
        defaultProps={{
          short: {
            startSec: 0,
            endSec: 30,
            captionLines: [],
            highlight: 'Dramatic Japanese Kaidan Tale',
            hookText: 'Legend of Edo Japan',
          },
          audioFilename: 'audio_en.wav',
          imageFilenames: [],
        }}
      />

      {/* Shorts — vertical 9:16 (1080x1920) */}
      {SHORTS.map((s) => {
        const dur = Math.round((s.endSec - s.startSec) * fps);
        return (
          <Composition
            key={s.id}
            id={s.id}
            component={ShortsComposition}
            durationInFrames={dur}
            fps={fps}
            width={1080}
            height={1920}
            defaultProps={{
              short: {
                startSec: s.startSec,
                endSec: s.endSec,
                captionLines: [],
                highlight: s.highlight,
                hookText: s.hookText,
              },
              audioFilename: 'audio_en.wav',
              imageFilenames: s.imageFilenames,
            }}
          />
        );
      })}
    </>
  );
};
