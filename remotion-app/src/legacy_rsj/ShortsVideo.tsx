/**
 * ShortsVideo — Red Sun Japan High-Retention Vertical Video (9:16, 1080x1920, 30fps).
 *
 * Implements:
 * 1. Kinetic Subtitles at 62% screen height (2-4 words per group, dynamic active-word highlight).
 * 2. Multi-Shot Dynamic Pacing (cycles 2-3 canonical project images with Ken Burns and smooth crossfades).
 * 3. End-Screen CTA Card with gradual background and audio fade-out during the last 3.5s (105 frames).
 */
import React, { useEffect, useMemo, useState } from 'react';
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

const cleanStatic = (p: string | null | undefined): string => {
  if (!p) return '';
  return p.replace(/^\/?public\//, '');
};

// Word-level timestamps from Whisper / Forced Alignment
export type Word = { word: string; start: number; end: number };

// Kinetic Subtitle Group (2 to 4 words per group for fast mobile retention)
export interface KineticGroup {
  words: Word[];
  start: number;
  end: number;
  text: string;
}

const SENTENCE_ENDERS = /[.!?]+$/;
const CLAUSE_ENDERS = /[,:;]+$/;

export function buildKineticGroups(words: Word[], clipStart: number, clipEnd: number): KineticGroup[] {
  if (!words || words.length === 0) return [];
  // Filter words within clip window with a small 0.5s margin
  const clipWords = words.filter((w) => w.end >= clipStart - 0.2 && w.start <= clipEnd + 0.2);
  if (clipWords.length === 0) return [];

  const groups: KineticGroup[] = [];
  let current: Word[] = [];

  for (let i = 0; i < clipWords.length; i++) {
    const w = clipWords[i];
    current.push(w);

    const endsSentence = SENTENCE_ENDERS.test(w.word);
    const endsClause = CLAUSE_ENDERS.test(w.word);
    const count = current.length;

    // Break at 2-3 words (max 4 if clause/sentence ender)
    const shouldBreak = endsSentence || (endsClause && count >= 2) || count >= 3 || i === clipWords.length - 1;

    if (shouldBreak && current.length > 0) {
      groups.push({
        words: current,
        start: current[0].start,
        end: current[current.length - 1].end,
        text: current.map((item) => item.word).join(' '),
      });
      current = [];
    }
  }

  return groups;
}

// ---------------------------------------------------------------------------
// Multi-Shot Dynamic Background with Ken Burns & Smooth Crossfade
// ---------------------------------------------------------------------------
interface MultiShotBackgroundProps {
  images: string[];
  frame: number;
  totalFrames: number;
  fps: number;
}

const MultiShotBackground: React.FC<MultiShotBackgroundProps> = ({ images, frame, totalFrames, fps }) => {
  const validImages = images && images.length > 0
    ? images
    : ['intro_01.png', 'intro_02.jpg', 'intro_03.jpg'];

  const numShots = Math.max(1, Math.min(validImages.length, 3));
  const activeImages = validImages.slice(0, numShots);
  const framesPerShot = totalFrames / numShots;

  const currentShotIdx = Math.min(Math.floor(frame / framesPerShot), numShots - 1);
  const shotProgress = (frame - currentShotIdx * framesPerShot) / framesPerShot;
  const currentImg = activeImages[currentShotIdx];

  // Alternating directional Ken Burns per shot
  const isEven = currentShotIdx % 2 === 0;
  const scale = isEven ? 1.05 + shotProgress * 0.12 : 1.17 - shotProgress * 0.10;
  const panX = isEven ? (shotProgress - 0.5) * 3 : (0.5 - shotProgress) * 3;
  const panY = isEven ? (shotProgress - 0.5) * 4 : (0.5 - shotProgress) * 4;

  // Crossfade transition (15 frames = 0.5s)
  const CROSSFADE_FRAMES = 15;
  const frameInShot = frame - currentShotIdx * framesPerShot;
  const isTransitioning = currentShotIdx > 0 && frameInShot < CROSSFADE_FRAMES;
  const transitionProgress = isTransitioning ? frameInShot / CROSSFADE_FRAMES : 1;

  const prevImg = currentShotIdx > 0 ? activeImages[currentShotIdx - 1] : null;

  return (
    <AbsoluteFill style={{ overflow: 'hidden', backgroundColor: '#07080a' }}>
      {/* Previous Shot during crossfade */}
      {isTransitioning && prevImg && (
        <Img
          key={`prev-shot-${prevImg}`}
          src={staticFile(cleanStatic(prevImg))}
          style={{
            position: 'absolute',
            inset: 0,
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            transform: `scale(1.15) translate(${panX}%, ${panY}%)`,
            filter: 'brightness(0.72) contrast(1.08) saturate(1.12)',
            opacity: 1 - transitionProgress,
          }}
        />
      )}

      {/* Current Shot */}
      <Img
        key={`curr-shot-${currentImg}`}
        src={staticFile(cleanStatic(currentImg))}
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          transform: `scale(${scale}) translate(${panX}%, ${panY}%)`,
          filter: 'brightness(0.72) contrast(1.08) saturate(1.12)',
          opacity: transitionProgress,
        }}
      />

      {/* Cinematic Vignette Overlay */}
      <AbsoluteFill
        style={{
          background:
            'linear-gradient(180deg, rgba(5,6,8,0.72) 0%, rgba(5,6,8,0.18) 32%, rgba(5,6,8,0.35) 65%, rgba(5,6,8,0.88) 100%)',
          pointerEvents: 'none',
        }}
      />
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// Mobile Safe Zone Shorts Header Overlay (1080x1920: top 160px, sides 48px)
// ---------------------------------------------------------------------------
export const BrandBadge: React.FC = () => (
  <div
    style={{
      width: 68,
      height: 68,
      borderRadius: '50%',
      overflow: 'hidden',
      boxShadow: '0 4px 16px rgba(0, 0, 0, 0.75)',
      filter: 'drop-shadow(0 4px 16px rgba(0, 0, 0, 0.75))',
      backgroundColor: 'rgba(10, 11, 14, 0.65)',
      flexShrink: 0,
      marginLeft: 'auto',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      pointerEvents: 'none',
    }}
  >
    <Img
      src={staticFile('red-sun-logo-background-tate.png')}
      style={{
        width: '100%',
        height: '100%',
        objectFit: 'cover',
      }}
    />
  </div>
);

export const TopHook: React.FC<{ text: string }> = ({ text }) => {
  if (!text) return null;
  return (
    <div
      style={{
        maxWidth: '75%',
        backgroundColor: 'rgba(10, 11, 14, 0.82)',
        borderLeft: '5px solid #b91c1c',
        padding: '12px 20px',
        borderRadius: '8px',
        backdropFilter: 'blur(8px)',
        WebkitBackdropFilter: 'blur(8px)',
        boxShadow: '0 8px 24px rgba(0, 0, 0, 0.85)',
        pointerEvents: 'none',
      }}
    >
      <span
        style={{
          fontSize: '30px',
          fontWeight: 800,
          color: '#f4f1ea',
          textTransform: 'uppercase',
          letterSpacing: '1.2px',
          fontFamily: '"RedSunCinzel", "Cinzel", "Montserrat", "Georgia", serif',
          lineHeight: 1.25,
          textShadow: '0 4px 16px rgba(0, 0, 0, 0.95), 0 2px 6px rgba(0, 0, 0, 0.85)',
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
      >
        {text}
      </span>
    </div>
  );
};

export const ShortsHeader: React.FC<{ title: string }> = ({ title }) => {
  return (
    <div
      style={{
        position: 'absolute',
        top: 160,
        left: 48,
        right: 48,
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-between',
        gap: '24px',
        zIndex: 40,
        pointerEvents: 'none',
      }}
    >
      {title ? <TopHook text={title} /> : <div />}
      <BrandBadge />
    </div>
  );
};

// ---------------------------------------------------------------------------
// Main Shorts Video Types
// ---------------------------------------------------------------------------
export interface ShortsClip {
  startSec: number;
  endSec: number;
  captionLines?: string[];
  highlight?: string;
  hookText: string;
}

export interface ShortsConfig {
  clip: ShortsClip;
  audioFilename: string;
  imageTheme: string;
  imageFilenames: string[];
}

export const ShortsVideo: React.FC<{ config: ShortsConfig }> = ({ config }) => {
  const { fps } = useVideoConfig();
  const frame = useCurrentFrame();
  const clipDur = Math.max(config.clip.endSec - config.clip.startSec, 5);
  const totalFrames = Math.round(clipDur * fps);
  const clipFrame = Math.min(frame, totalFrames);
  const currentTime = clipFrame / fps;
  const absoluteTime = config.clip.startSec + currentTime;

  // Load word timestamps
  const [timestamps, setTimestamps] = useState<Word[] | null>(null);

  // Ensure web fonts with Latin Extended are ready before rendering frames
  useEffect(() => {
    if (typeof document !== 'undefined' && document.fonts) {
      const fontHandle = delayRender('Loading web fonts with Latin Extended');
      document.fonts.ready
        .then(() => continueRender(fontHandle))
        .catch(() => continueRender(fontHandle));
    }
  }, []);

  useEffect(() => {
    const handle = delayRender('Loading timestamps for Short');
    fetch(staticFile('timestamps.json'))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((ts) => {
        setTimestamps(ts as Word[]);
        continueRender(handle);
      })
      .catch((err) => {
        console.error('Timestamps fetch failed for Short:', err);
        setTimestamps([]);
        continueRender(handle);
      });
  }, []);

  // Build kinetic 2-4 word groups
  const kineticGroups = useMemo(() => {
    if (!timestamps || timestamps.length === 0) return [];
    return buildKineticGroups(timestamps, config.clip.startSec, config.clip.endSec);
  }, [timestamps, config.clip.startSec, config.clip.endSec]);

  // Current active word group
  const activeGroup = useMemo(() => {
    if (kineticGroups.length === 0) return null;
    return kineticGroups.find(
      (g) => absoluteTime >= g.start - 0.05 && absoluteTime <= g.end + 0.35,
    ) ?? null;
  }, [absoluteTime, kineticGroups]);

  // ── Final CTA Card Timing: Last 3.5s (105 frames) ───────────────────
  const CTA_FRAMES = 105;
  const ctaStartFrame = Math.max(0, totalFrames - CTA_FRAMES);
  const isEndCta = frame >= ctaStartFrame;
  const ctaProgress = isEndCta ? Math.min((frame - ctaStartFrame) / 25, 1) : 0;
  const subtitleFadeOut = isEndCta ? Math.max(0, 1 - (frame - ctaStartFrame) / 15) : 1;

  return (
    <AbsoluteFill style={{ backgroundColor: '#07080a' }}>
      {/* Dynamic Multi-Shot Background (cycling 2-3 images with Ken Burns) */}
      <MultiShotBackground
        images={config.imageFilenames}
        frame={clipFrame}
        totalFrames={totalFrames}
        fps={fps}
      />

      {/* ───────────────────────────────────────────────────────────── */}
      {/* Mobile Safe Zone Header (top: 160px, sides: 48px, logo: 68px)  */}
      {/* ───────────────────────────────────────────────────────────── */}
      <ShortsHeader title={config.clip.hookText} />

      {/* ───────────────────────────────────────────────────────────── */}
      {/* Kinetic Subtitles — Centered at 62% screen height (Mobile Safe) */}
      {/* ───────────────────────────────────────────────────────────── */}
      {activeGroup && subtitleFadeOut > 0 && (
        <div
          style={{
            position: 'absolute',
            top: '62%',
            left: 0,
            right: 0,
            transform: 'translateY(-50%)',
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            pointerEvents: 'none',
            zIndex: 45,
            opacity: subtitleFadeOut,
          }}
        >
          <div
            style={{
              backgroundColor: 'rgba(9, 10, 14, 0.78)',
              borderLeft: '4px solid #e5a93c',
              padding: '16px 28px',
              borderRadius: '14px',
              boxShadow: '0 10px 32px rgba(0, 0, 0, 0.90)',
              backdropFilter: 'blur(8px)',
              WebkitBackdropFilter: 'blur(8px)',
              maxWidth: '86%',
              display: 'flex',
              flexWrap: 'wrap',
              justifyContent: 'center',
              alignItems: 'center',
              gap: '12px',
            }}
          >
            {activeGroup.words.map((w, idx) => {
              const isWordActive = absoluteTime >= w.start && absoluteTime <= w.end;
              return (
                <span
                  key={`w-${idx}-${w.word}`}
                  style={{
                    fontSize: '48px',
                    fontWeight: 900,
                    fontFamily: '"RedSunKinetic", "Montserrat", "Inter", sans-serif',
                    textTransform: 'uppercase',
                    letterSpacing: '1px',
                    lineHeight: 1.25,
                    color: isWordActive ? '#e5a93c' : '#ffffff',
                    transform: isWordActive ? 'scale(1.08)' : 'scale(1.0)',
                    transition: 'transform 0.08s ease-out, color 0.08s ease-out',
                    textShadow: isWordActive
                      ? '0 0 24px rgba(229,169,60,0.9), 0 4px 18px rgba(0,0,0,1)'
                      : '0 4px 18px rgba(0,0,0,0.95), 0 0 8px rgba(0,0,0,1)',
                  }}
                >
                  {w.word}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* ───────────────────────────────────────────────────────────── */}
      {/* End Screen CTA Card (Last 3.5s / 105 frames) with Fade-Out      */}
      {/* ───────────────────────────────────────────────────────────── */}
      {isEndCta && (
        <>
          {/* Smooth Darkening Charcoal Overlay */}
          <AbsoluteFill
            style={{
              backgroundColor: '#090a0d',
              opacity: ctaProgress * 0.92,
              zIndex: 60,
              pointerEvents: 'none',
            }}
          />

          {/* Elegant Central Conversion Card */}
          <div
            style={{
              position: 'absolute',
              top: '48%',
              left: '44px',
              right: '44px',
              transform: 'translateY(-50%)',
              backgroundColor: 'rgba(12, 13, 17, 0.92)',
              borderLeft: '5px solid #b91c1c',
              borderRadius: '16px',
              padding: '36px 30px',
              boxShadow: '0 16px 48px rgba(0, 0, 0, 0.95)',
              backdropFilter: 'blur(10px)',
              WebkitBackdropFilter: 'blur(10px)',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              textAlign: 'center',
              opacity: ctaProgress,
              zIndex: 70,
              pointerEvents: 'none',
            }}
          >
            {/* Line 1: Kicker */}
            <div
              style={{
                fontFamily: '"RedSunCinzel", "Cinzel", "Georgia", serif',
                fontSize: '24px',
                fontWeight: 700,
                letterSpacing: '0.24em',
                color: '#e5a93c',
                textTransform: 'uppercase',
                marginBottom: '16px',
              }}
            >
              🏮 RED SUN JAPAN
            </div>

            {/* Line 2: Main Headline */}
            <div
              style={{
                fontFamily: '"RedSunCinzel", "Cinzel", "Georgia", serif',
                fontSize: '36px',
                fontWeight: 800,
                letterSpacing: '0.10em',
                color: '#ffffff',
                marginBottom: '16px',
                textShadow: '0 4px 20px rgba(0,0,0,0.95)',
                lineHeight: 1.25,
              }}
            >
              WATCH FULL CHRONICLE
            </div>

            {/* Crimson Divider */}
            <div
              style={{
                width: '130px',
                height: '3px',
                backgroundColor: '#b91c1c',
                marginBottom: '18px',
                borderRadius: '2px',
              }}
            />

            {/* Line 3: Supporting Subtitle */}
            <div
              style={{
                fontFamily: '"Georgia", serif',
                fontSize: '22px',
                fontStyle: 'italic',
                color: '#e4e4e7',
                marginBottom: '26px',
                lineHeight: 1.35,
              }}
            >
              Tap &quot;Related Video&quot; below or visit the channel
            </div>

            {/* Subtle Pulsing Down Arrow */}
            <div
              style={{
                fontSize: '38px',
                color: '#e5a93c',
                transform: `translateY(${Math.sin(((frame - ctaStartFrame) / 14) * Math.PI) * 8}px)`,
                textShadow: '0 2px 10px rgba(0,0,0,0.85)',
              }}
            >
              ⬇
            </div>
          </div>
        </>
      )}
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// ShortsComposition — Root Wrapper for CLI / Remotion Rendering
// ---------------------------------------------------------------------------
export const ShortsComposition: React.FC<{
  short: ShortsClip;
  audioFilename?: string;
  imageFilenames?: string[];
}> = ({ short, audioFilename = 'audio_en.wav', imageFilenames = [] }) => {
  const config: ShortsConfig = {
    clip: short,
    audioFilename,
    imageTheme: 'yokai_ukiyo',
    imageFilenames,
  };

  const { fps } = useVideoConfig();
  const frame = useCurrentFrame();
  const clipDur = Math.max(config.clip.endSec - config.clip.startSec, 5);
  const totalFrames = Math.round(clipDur * fps);

  const audioStartSec = config.clip.startSec;
  const audioOffsetFrames = Math.round(audioStartSec * fps);

  // Audio fade-out during the last 45 frames (1.5s) of the CTA
  const audioFadeFrames = 45;
  const audioVolume = frame >= totalFrames - audioFadeFrames
    ? Math.max(0, (totalFrames - frame) / audioFadeFrames)
    : 1;

  return (
    <AbsoluteFill style={{ backgroundColor: '#000' }}>
      <ShortsVideo config={config} />
      <Audio
        src={staticFile(cleanStatic(config.audioFilename))}
        startFrom={audioOffsetFrames}
        endAt={Math.round(config.clip.endSec * fps)}
        volume={audioVolume}
      />
    </AbsoluteFill>
  );
};
