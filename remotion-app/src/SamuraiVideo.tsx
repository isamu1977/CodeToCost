/**
 * SamuraiVideo — Red Sun Japan video renderer (v2).
 *
 * Improvements over v1:
 * - Subtitles: sentence-level grouping (no more flickering 5-word chunks)
 * - Crossfade: smooth transitions between image slides
 * - Ending: fade-to-black + CTA overlay (Like, Share, Subscribe)
 * - BGM: slightly louder for better atmosphere
 */
import {
  AbsoluteFill,
  Audio,
  Img,
  continueRender,
  delayRender,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import React, { useEffect, useMemo, useState } from 'react';
import manifest from '../public/manifest.json';
import { TitleOverlay } from './TitleOverlay';
import { CinematicOpeningTitle } from './CinematicOpeningTitle';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
type Word = { word: string; start: number; end: number };
type Manifest = {
  project_id?: number;
  slug?: string;
  language_code?: string;
  title_en?: string;
  title_ja?: string;
  duration_seconds?: number;
  fps?: number;
  width?: number;
  height?: number;
  audio_filename?: string;
  timestamps_filename?: string;
  image_theme?: string;
  image_count?: number;
  image_filenames?: string[];
  bgm_filename?: string | null;
  bgm_mood?: string | null;
  archival_bridge_scene_index?: number;
  archival_bridge_start_sec?: number;
  archival_bridge_end_sec?: number;
};

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const M = manifest as Manifest;

export const cleanStatic = (p: string | null | undefined): string => {
  if (!p) return '';
  return p.replace(/^\/?public\//, '');
};

const AUDIO_URL = M.audio_filename ? staticFile(cleanStatic(M.audio_filename)) : staticFile('audio_main.wav');
const TIMESTAMPS_URL = staticFile('timestamps.json');
const IMAGE_NAMES: string[] = (M.image_filenames ?? []).map(cleanStatic);
const cleanBgmPath = M.bgm_filename ? cleanStatic(M.bgm_filename) : null;
const BGM_URL: string | null = cleanBgmPath ? staticFile(cleanBgmPath) : null;

// Ending & Cold Open config
const FADE_IN_SECONDS = 1.5;      // smooth cold open fade-in at 00:00
const CROSSFADE_SECONDS = 1.5;    // crossfade duration between slides
const BGM_VOLUME = 0.05;          // BGM volume — lowered so it never competes with narration
const SUBTITLE_SYNC_OFFSET = 0;      // No offset — Whisper timestamps already account for intro padding (3s silence at start of main.wav). Previous 6.47s caused 4-6s subtitle lag.

// Scene timing — loaded from scene_timing.json for image transitions
type SceneTiming = {
  scene_index: number;
  start_sec: number;
  end_sec: number;
  duration_sec: number;
  merged_from: number[];
  isArchivalBridge?: boolean;
};

// Sentence-ending punctuation for subtitle grouping
const SENTENCE_ENDERS = /[.!?]+$/;
const CLAUSE_ENDERS = /[,:;]+$/;

// Image-to-scene mapping: which image to show for each scene
// merged_from tells us which original scene indices were merged
// We use the first image from merged_from for the merged scene

// ---------------------------------------------------------------------------
// Probes — fail gracefully if a file is missing in public/.
// ---------------------------------------------------------------------------
const probeExists = (url: string): Promise<boolean> =>
  new Promise<boolean>((resolve) => {
    const img = new Image();
    img.onload = () => resolve(true);
    img.onerror = () => resolve(false);
    img.src = url;
  });

// ---------------------------------------------------------------------------
// Subtitle logic: group words into sentences/clauses
// ---------------------------------------------------------------------------
function buildSubtitleGroups(words: Word[]): { text: string; start: number; end: number }[] {
  if (words.length === 0) return [];

  const groups: { text: string; start: number; end: number }[] = [];
  let currentWords: Word[] = [];
  let currentText = '';

  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    currentWords.push(w);
    currentText += (currentText ? ' ' : '') + w.word;

    // Check if this word ends a sentence or clause
    const endsSentence = SENTENCE_ENDERS.test(w.word);
    const endsClause = CLAUSE_ENDERS.test(w.word);

    // Also check for long gaps (>0.5s) between words — natural pause
    const nextWord = words[i + 1];
    const hasGap = nextWord && (nextWord.start - w.end) > 0.5;

    // Group boundaries: sentence end, clause end + enough words, or long gap
    const wordCount = currentWords.length;
    const shouldBreak =
      endsSentence ||
      (endsClause && wordCount >= 4) ||
      (hasGap && wordCount >= 3) ||
      wordCount >= 15; // safety cap — never show more than 15 words

    if (shouldBreak || i === words.length - 1) {
      groups.push({
        text: currentText,
        start: currentWords[0].start,
        end: currentWords[currentWords.length - 1].end,
      });
      currentWords = [];
      currentText = '';
    }
  }

  return groups;
}

// ---------------------------------------------------------------------------
export interface SamuraiVideoProps {
  titleJa?: string;
  titleEn?: string;
}

export const SamuraiVideo: React.FC<SamuraiVideoProps> = ({ titleJa, titleEn }) => {
  const effectiveTitleJa = titleJa ?? M.title_ja;
  const effectiveTitleEn = titleEn ?? M.title_en;
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const currentTime = frame / fps;

  // ── State: timestamps loaded at runtime ───────────────────────────────
  const [timestamps, setTimestamps] = useState<Word[] | null>(null);
  const [availableImageNames, setAvailableImageNames] = useState<string[]>(
    () => IMAGE_NAMES,
  );
  const [sceneTimings, setSceneTimings] = useState<SceneTiming[]>([]);

  // ── Fetch timestamps + probe images + scene timings ──────────────────
  useEffect(() => {
    const handle = delayRender('Loading timestamps + image probe + scene timings');

    Promise.all([
      fetch(TIMESTAMPS_URL)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .catch((err) => {
          console.error('Timestamps fetch failed:', err);
          return [];
        }),
      Promise.all(IMAGE_NAMES.map((n) => probeExists(staticFile(n)))).then(
        (results) => IMAGE_NAMES.filter((_, i) => results[i]),
      ),
      fetch(staticFile('scene_timing.json'))
        .then((r) => (r.ok ? r.json() : []))
        .catch(() => []),
    ])
      .then(([ts, available, timings]) => {
        setTimestamps(ts as Word[]);
        setAvailableImageNames(available.length > 0 ? available : IMAGE_NAMES.slice(0, 1));
        setSceneTimings(timings as SceneTiming[]);
        continueRender(handle);
      })
      .catch((err) => {
        console.error('Asset load failed:', err);
        setTimestamps([]);
        setAvailableImageNames(IMAGE_NAMES.slice(0, 1));
        setSceneTimings([]);
        continueRender(handle);
      });
  }, []);

  // ── Subtitle groups (sentence-level) ─────────────────────────────────
  const subtitleGroups = useMemo(() => {
    if (!timestamps || timestamps.length === 0) return [];
    return buildSubtitleGroups(timestamps);
  }, [timestamps]);

  const currentSubtitle = useMemo(() => {
    if (subtitleGroups.length === 0) return null;
    // Apply sync offset to align with audio (negative = subtitles earlier)
    const adjustedTime = currentTime - SUBTITLE_SYNC_OFFSET;
    return subtitleGroups.find(
      (g) => adjustedTime >= g.start && adjustedTime <= g.end + 0.3, // 0.3s grace period
    ) ?? null;
  }, [currentTime, subtitleGroups]);

  // ── Slide / Ken Burns with scene-based timing ─────────────────────────
  // Use scene timings if available, otherwise fall back to equal division
  const sceneCount = sceneTimings.length > 0 ? sceneTimings.length : availableImageNames.length;
  const availableCount = Math.max(sceneCount, 1);

  // Find current scene based on time
  const currentSceneIdx = useMemo(() => {
    if (sceneTimings.length === 0) {
      // Fallback: equal division
      const framesPerImage = Math.max(Math.floor(durationInFrames / availableImageNames.length), 1);
      return Math.min(Math.floor(frame / framesPerImage), availableImageNames.length - 1);
    }
    // Find scene that contains current time
    for (let i = 0; i < sceneTimings.length; i++) {
      const st = sceneTimings[i];
      if (currentTime >= st.start_sec && currentTime < st.end_sec) {
        return i;
      }
    }
    return sceneTimings.length - 1; // last scene
  }, [currentTime, sceneTimings, frame, durationInFrames, availableImageNames.length]);

  // Get the image name for the current scene
  const currentImageName = useMemo(() => {
    if (!availableImageNames || availableImageNames.length === 0) return '';
    if (sceneTimings.length > 0 && currentSceneIdx < sceneTimings.length) {
      const st = sceneTimings[currentSceneIdx];
      // Use the first image from merged_from
      const imgIdx = (st.merged_from[0] || st.scene_index) - 1; // 0-indexed
      if (imgIdx >= 0 && imgIdx < availableImageNames.length) {
        return availableImageNames[imgIdx];
      }
    }
    return availableImageNames[currentSceneIdx] || availableImageNames[0] || '';
  }, [sceneTimings, currentSceneIdx, availableImageNames]);

  // Ken Burns — TRULY continuous (no snap). Each scene's image:
  //   • Enters at the scale where the previous image exited (1.12 at start of scene)
  //   • Grows to 1.18 by the end of the scene (6% growth per scene)
  //   • Holds at 1.18 during the crossfade (matched on both sides)
  // Result: scale never decreases between scenes → no visible "shrink/snap".
  const sceneStartFrame = sceneTimings.length > 0 && currentSceneIdx < sceneTimings.length
    ? Math.round(sceneTimings[currentSceneIdx].start_sec * fps)
    : currentSceneIdx * Math.floor(durationInFrames / availableCount);
  const sceneEndFrame = sceneTimings.length > 0 && currentSceneIdx < sceneTimings.length
    ? Math.round(sceneTimings[currentSceneIdx].end_sec * fps)
    : (currentSceneIdx + 1) * Math.floor(durationInFrames / availableCount);
  const sceneDuration = Math.max(sceneEndFrame - sceneStartFrame, 1);
  const frameInScene = frame - sceneStartFrame;
  const sceneProgress = Math.min(Math.max(frameInScene / sceneDuration, 0), 1);

  // Previous scene: used to drive the previous image during crossfade.
  const prevSceneIdx = currentSceneIdx > 0 ? currentSceneIdx - 1 : 0;

  // Each image enters already zoomed (1.12) and grows to 1.18 — never resets.
  // The previous image is held at its end-state (1.18) while fading out;
  // the new image is at 1.12 (entering) — small mismatch (0.06) but ONLY
  // while the previous is mostly transparent (opacity < 0.3 in first 30% of
  // crossfade), so the slight difference is hidden under the fade.
  // Final visual: monotonic zoom forward, no snap.
  const KEN_BURNS_START = 1.12;
  const KEN_BURNS_END = 1.18;
  const scale = KEN_BURNS_START + sceneProgress * (KEN_BURNS_END - KEN_BURNS_START);
  const panX = (sceneProgress - 0.5) * 2;
  // Previous image: exact scale it had at end of its scene (1.18) — matches its
  // last drawn frame before the new image took over.
  const prevScale = KEN_BURNS_END;
  const prevPanX = 1;

  // Crossfade: detect scene transitions
  const isTransitioning = sceneTimings.length > 0 && currentSceneIdx < sceneTimings.length
    ? (currentTime - (sceneTimings[currentSceneIdx]?.start_sec || 0)) < CROSSFADE_SECONDS
    : false;

  // Get previous image for crossfade
  const prevImageName = useMemo(() => {
    if (!isTransitioning || prevSceneIdx === currentSceneIdx) return null;
    if (sceneTimings.length > 0 && prevSceneIdx < sceneTimings.length) {
      const st = sceneTimings[prevSceneIdx];
      const imgIdx = (st.merged_from[0] || st.scene_index) - 1;
      if (imgIdx >= 0 && imgIdx < availableImageNames.length) {
        return availableImageNames[imgIdx];
      }
    }
    return availableImageNames[prevSceneIdx] || null;
  }, [isTransitioning, prevSceneIdx, currentSceneIdx, sceneTimings, availableImageNames]);

  // ── Ending & Intro effects ──────────────────────────────────────────
  const isFadingIn = currentTime < FADE_IN_SECONDS;
  const fadeInProgress = isFadingIn ? Math.min(currentTime / FADE_IN_SECONDS, 1) : 1;

  // Mid-roll CTA timing (at 30% of video duration, for 8 seconds / 240 frames)
  const ctaStartFrame = Math.floor(durationInFrames * 0.30);
  const ctaEndFrame = ctaStartFrame + 240;

  // Sleep-Safe End Screen timing (last 15 seconds of the composition)
  const END_SCREEN_FRAMES = Math.round(15 * fps);
  const endScreenStartFrame = Math.max(0, durationInFrames - END_SCREEN_FRAMES);
  const isEndScreen = frame >= endScreenStartFrame;

  // BGM volume: steady at BGM_VOLUME, with a slow fade-out to 0 across the last 15s
  const bgmVolume = useMemo(() => {
    if (frame < endScreenStartFrame) return BGM_VOLUME;
    const progress = Math.min(Math.max((frame - endScreenStartFrame) / END_SCREEN_FRAMES, 0), 1);
    return BGM_VOLUME * (1 - progress);
  }, [frame, endScreenStartFrame, END_SCREEN_FRAMES]);

  return (
    <AbsoluteFill style={{ backgroundColor: '#0c0d0e' }}>
      {/* Image slideshow with Ken Burns + scene-based crossfade */}
      {prevImageName && isTransitioning && (
        <Img
          key={`prev-${prevImageName}`}
          src={staticFile(prevImageName)}
          style={{
            position: 'absolute',
            inset: 0,
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            transform: `scale(${prevScale}) translateX(${prevPanX}%)`,
            transformOrigin: 'center center',
            opacity: Math.max(0, 1 - (currentTime - (sceneTimings[currentSceneIdx]?.start_sec || 0)) / CROSSFADE_SECONDS),
          }}
        />
      )}
      {currentImageName && (
        <Img
          key={`curr-${currentImageName}`}
          src={staticFile(currentImageName)}
          style={{
            position: 'absolute',
            inset: 0,
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            transform: `scale(${scale}) translateX(${panX}%)`,
            transformOrigin: 'center center',
            opacity: isTransitioning
              ? Math.min(1, (currentTime - (sceneTimings[currentSceneIdx]?.start_sec || 0)) / CROSSFADE_SECONDS)
              : 1,
          }}
        />
      )}

      {/* Vignette overlay — dark edges for cinematic look */}
      <AbsoluteFill
        style={{
          pointerEvents: 'none',
          background: 'radial-gradient(ellipse at center, transparent 30%, rgba(0,0,0,0.85) 95%)',
        }}
      />

      {/* Narration audio — begins immediately at frame 0 */}
      <Audio src={AUDIO_URL} volume={1} />

      {/* Background music — dynamic fade-out to silence over last 15s */}
      {BGM_URL && <Audio src={BGM_URL} volume={bgmVolume} loop />}

      {/* Brand watermark — top-right (fades out during end screen) */}
      {!isEndScreen && <WatermarkLogo frame={frame} />}

      {/* Video title — upper-left bilingual (日本語 + Romaji) — synchronized to start at frame 435 (14.5s) */}
      <TitleOverlay
        frame={frame}
        titleJa={effectiveTitleJa}
        titleEn={effectiveTitleEn}
        endScreenStartFrame={endScreenStartFrame}
      />

      {/* Cinematic Story Title Reveal on Opening Scene (00:05 to 00:14 / Frames 150 to 420) */}
      <CinematicOpeningTitle
        frame={frame}
        titleJa={effectiveTitleJa}
        titleEn={effectiveTitleEn}
      />

      {/* Mid-Roll CTA Card (aos 30% da duração, durante 8s / 240 frames — Like, Subscribe & Share) */}
      {frame >= ctaStartFrame && frame <= ctaEndFrame && (() => {
        let ctaProgress = 1;
        if (frame < ctaStartFrame + 25) {
          ctaProgress = (frame - ctaStartFrame) / 25;
        } else if (frame > ctaEndFrame - 25) {
          ctaProgress = (ctaEndFrame - frame) / 25;
        }
        const clamped = Math.max(0, Math.min(ctaProgress, 1));
        return (
          <div
            style={{
              position: 'absolute',
              bottom: '140px',
              right: '48px',
              minWidth: '480px',
              maxWidth: '540px',
              padding: '22px 28px',
              backgroundColor: 'rgba(12, 13, 15, 0.90)',
              borderLeft: '5px solid #b91c1c',
              borderRadius: '8px',
              boxShadow: '0 12px 32px rgba(0, 0, 0, 0.85)',
              backdropFilter: 'blur(8px)',
              WebkitBackdropFilter: 'blur(8px)',
              display: 'flex',
              flexDirection: 'column',
              opacity: clamped,
              transform: `translateX(${(1 - clamped) * 40}px)`,
              zIndex: 30,
              pointerEvents: 'none',
            }}
          >
            <div
              style={{
                fontFamily: '"Cinzel", "Georgia", "Times New Roman", serif',
                fontSize: '16px',
                fontWeight: 700,
                letterSpacing: '0.22em',
                color: '#e5a93c',
                textTransform: 'uppercase',
                marginBottom: '8px',
              }}
            >
              🏮 RED SUN JAPAN ARCHIVES
            </div>
            <div
              style={{
                fontFamily: "'Cinzel', Georgia, serif",
                fontSize: '25px',
                fontWeight: 700,
                letterSpacing: '0.08em',
                color: '#ffffff',
                marginBottom: '8px',
                whiteSpace: 'nowrap',
              }}
            >
              👍 LIKE &nbsp; • &nbsp; 🔔 SUBSCRIBE &nbsp; • &nbsp; ↗ SHARE
            </div>
            <div
              style={{
                fontFamily: '"Georgia", "Times New Roman", serif',
                fontSize: '17px',
                fontWeight: 400,
                color: '#d4d4d8',
                lineHeight: 1.35,
              }}
            >
              Support authentic translations • Full essay &amp; books in description
            </div>
          </div>
        );
      })()}

      {/* Sleep-Safe End Screen (últimos 15 segundos): Escurecimento cinematográfico suave + Bloco Superior + Indicador de Descrição */}
      {isEndScreen && (() => {
        const overlayProgress = Math.min((frame - endScreenStartFrame) / (1.5 * fps), 1);
        return (
          <>
            {/* Overlay de Escurecimento Suave (1.5s fade-in) */}
            <AbsoluteFill
              style={{
                backgroundColor: 'rgba(8, 9, 11, 0.75)',
                opacity: overlayProgress,
                zIndex: 42,
                pointerEvents: 'none',
              }}
            />

            {/* Bloco Superior Centralizado */}
            <div
              style={{
                position: 'absolute',
                top: '64px',
                left: 0,
                right: 0,
                textAlign: 'center',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                opacity: overlayProgress,
                zIndex: 50,
                pointerEvents: 'none',
              }}
            >
              <div
                style={{
                  fontFamily: '"Cinzel", "Georgia", "Times New Roman", serif',
                  fontSize: '18px',
                  letterSpacing: '0.28em',
                  color: '#e5a93c',
                  marginBottom: '12px',
                  textTransform: 'uppercase',
                  fontWeight: 600,
                }}
              >
                RED SUN JAPAN &nbsp;•&nbsp; ARCHIVAL CHRONICLE
              </div>
              <div
                style={{
                  fontFamily: "'Cinzel', Georgia, serif",
                  fontSize: '42px',
                  fontWeight: 700,
                  letterSpacing: '0.14em',
                  color: '#f4f1ea',
                  textShadow: '0 4px 24px rgba(0,0,0,0.95)',
                  marginBottom: '14px',
                }}
              >
                CONTINUE EXPLORING THE ARCHIVE
              </div>
              <div
                style={{
                  height: '2px',
                  width: '220px',
                  backgroundColor: '#b91c1c',
                  margin: '0 auto 14px auto',
                }}
              />
              <div
                style={{
                  fontFamily: '"Georgia", "Times New Roman", serif',
                  fontSize: '22px',
                  fontStyle: 'italic',
                  color: '#e4e4e7',
                  letterSpacing: '0.04em',
                }}
              >
                If this chronicle moved you, please leave a Like, Share &amp; Subscribe
              </div>
            </div>

            {/* Indicador de Descrição (Acima da faixa de legenda, em bottom: 145px, centralizado) */}
            <div
              style={{
                position: 'absolute',
                bottom: '145px',
                left: 0,
                right: 0,
                textAlign: 'center',
                opacity: overlayProgress * 0.9,
                zIndex: 50,
                pointerEvents: 'none',
              }}
            >
              <div
                style={{
                  fontFamily: '"Cinzel", "Georgia", "Times New Roman", serif',
                  fontSize: '18px',
                  letterSpacing: '0.08em',
                  color: '#e5a93c',
                  fontWeight: 600,
                  textShadow: '0 2px 8px rgba(0,0,0,0.9)',
                }}
              >
                📜 Primary Sources, Companion Essay &amp; Recommended Books in the Description Below
              </div>
            </div>
          </>
        );
      })()}

      {/* Subtitles — sentence-level, bottom-safe margin (bottom: 48px), fontSize 42px (-20%), dark blur backdrop */}
      {currentSubtitle && (
        <div
          style={{
            position: 'absolute',
            bottom: '48px',
            left: 0,
            right: 0,
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            pointerEvents: 'none',
            zIndex: 44,
          }}
        >
          <div
            style={{
              fontSize: '42px',
              color: '#F8F6F0',
              textShadow: '2px 2px 10px rgba(0,0,0,1), 0 0 4px rgba(0,0,0,0.8)',
              fontFamily: '"Georgia", "Times New Roman", serif',
              backgroundColor: 'rgba(10, 10, 12, 0.72)',
              backdropFilter: 'blur(4px)',
              WebkitBackdropFilter: 'blur(4px)',
              padding: '12px 32px',
              borderRadius: '8px',
              maxWidth: '78%',
              textAlign: 'center',
              lineHeight: 1.35,
              letterSpacing: '0.4px',
            }}
          >
            {currentSubtitle.text}
          </div>
        </div>
      )}

      {/* Smooth Cold Open 1.5s fade-in from #0c0d0e at 00:00 */}
      {isFadingIn && (
        <AbsoluteFill
          style={{
            backgroundColor: '#0c0d0e',
            opacity: 1 - fadeInProgress,
            pointerEvents: 'none',
            zIndex: 45,
          }}
        />
      )}

      {/* Transição final serena nos últimos 45 frames (1.5s) para #0c0d0e */}
      {frame >= durationInFrames - 45 && (
        <AbsoluteFill
          style={{
            backgroundColor: '#0c0d0e',
            opacity: (frame - (durationInFrames - 45)) / 45,
            pointerEvents: 'none',
            zIndex: 65,
          }}
        />
      )}
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// WatermarkLogo — top-right brand watermark with fade-in
// ---------------------------------------------------------------------------
const WatermarkLogo = ({ frame }: { frame: number }) => {
  const FADE_FRAMES = 90;
  const opacity = Math.min(frame / FADE_FRAMES, 1) * 0.45;
  const [errored, setErrored] = useState(false);

  if (errored) return null;

  return (
    <Img
      src={staticFile('red-sun-logo-tate.png')}
      onError={() => setErrored(true)}
      style={{
        position: 'absolute',
        top: 30,
        right: 30,
        width: 180,
        opacity,
        pointerEvents: 'none',
      }}
    />
  );
};
