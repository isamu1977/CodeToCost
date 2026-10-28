/**
 * ChannelIntro — Red Sun Japan channel bumper / ident.
 *
 * Phase 8: plays before every video as a brand intro. Two modes:
 *
 *   - "long"  (10s, default): logo + 4-image ukiyo-e slideshow with
 *     Ken Burns + "RED SUN JAPAN" wordmark underneath. Used for
 *     long-form videos.
 *
 *   - "short" (5s): just the logo, quick scale + fade. Used for
 *     YouTube Shorts / TikTok / Reels.
 *
 * The component is data-driven from props:
 *   - variant: "long" | "short"
 *   - intro_image_filenames: string[] (filenames in render-remotion/public/)
 *   - logo_filename: string (default "red-sun-logo-tate.png")
 *
 * Trigger: the manifest's `intro_variant` field. Backend decides
 * which one to use based on output kind (long-form vs short).
 */
import {
  AbsoluteFill,
  Audio,
  Img,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import { useState } from 'react';

const cleanStatic = (p: string | null | undefined): string => {
  if (!p) return '';
  return p.replace(/^\/?public\//, '');
};

type IntroVariant = 'long' | 'short';

type Props = {
  variant?: IntroVariant;
  intro_image_filenames?: string[];
  logo_filename?: string;
  wordmark_text?: string;
  taiko_filename?: string | null;
  title_ja?: string;
  title_en?: string;
};

const LONG_DURATION_SEC = 10;
const SHORT_DURATION_SEC = 5;
const FPS_DEFAULT = 30;

const LONG_IMAGES_DEFAULT = [
  'intro_01.png',
  'intro_02.jpg',
  'intro_03.jpg',
  'intro_04.png',
];

export const ChannelIntro: React.FC<Props> = ({
  variant = 'long',
  intro_image_filenames = LONG_IMAGES_DEFAULT,
  logo_filename = 'red-sun-logo-background-tate.png',
  wordmark_text = 'RED SUN JAPAN',
  taiko_filename = null as string | null,
  title_ja,
  title_en,
}) => {
  const frame = useCurrentFrame();
  const { fps: videoFps } = useVideoConfig();
  const fps = videoFps || FPS_DEFAULT;
  const durationSec = variant === 'short' ? SHORT_DURATION_SEC : LONG_DURATION_SEC;
  const totalFrames = durationSec * fps;

  // Logo animation parameters
  //   - Phase 1 (0-0.5s, frames 0-15): scale 0.7→1.0, opacity 0→1 (entrance)
  //   - Phase 2 (middle): stable, with subtle breathing pulse
  //   - Phase 3 (last 0.5s, frames last-15 to last): opacity 1→0 (exit)
  const entranceFrames = 0.5 * fps;
  const exitFrames = 0.5 * fps;
  const exitStart = totalFrames - exitFrames;

  let logoOpacity: number;
  let logoScale: number;
  if (frame < entranceFrames) {
    const t = frame / entranceFrames;
    logoOpacity = t;
    logoScale = 0.7 + 0.3 * t;
  } else if (frame > exitStart) {
    const t = (frame - exitStart) / exitFrames;
    logoOpacity = 1 - t;
    logoScale = 1.0;
  } else {
    logoOpacity = 1;
    // Subtle breathing pulse after entrance, before exit
    const pulseT = (frame - entranceFrames) / (exitStart - entranceFrames);
    logoScale = 1.0 + 0.025 * Math.sin(pulseT * Math.PI * 4);
  }

  // Background slideshow (long variant only) — 4 images, ~2.5s each.
  // For the short variant, we use a single hero image (the first one)
  // since there's no time to cycle.
  const visibleImages = variant === 'long' ? intro_image_filenames : [intro_image_filenames[0]];
  const secondsPerImage = durationSec / visibleImages.length;
  const framesPerImage = secondsPerImage * fps;

  // Current image index + Ken Burns progress
  const currentImageIdx = Math.min(
    Math.floor(frame / framesPerImage),
    visibleImages.length - 1,
  );
  const frameInImage = frame - currentImageIdx * framesPerImage;
  const imageProgress = Math.min(frameInImage / framesPerImage, 1);
  // Slow Ken Burns for intro: 1.0 → 1.06 (subtler than the main video)
  const imageScale = 1 + imageProgress * 0.06;
  const imagePanX = (imageProgress - 0.5) * 1.5;

  // Wordmark: DISABLED per Isamu's request (logo already communicates the brand).
  // The wordmark_text prop and computation are kept alive for future reactivation.
  // To re-enable: uncomment the wordmarkOpacity computation below and the
  // JSX block at the bottom of the return.
  //
  // let wordmarkOpacity: number;
  // if (variant === 'short') {
  //   wordmarkOpacity = 0;
  // } else if (frame < entranceFrames) {
  //   wordmarkOpacity = 0;
  // } else if (frame > exitStart) {
  //   wordmarkOpacity = logoOpacity;
  // } else {
  //   const wordmarkStart = 0.3 * totalFrames;
  //   const wordmarkEnd = 0.5 * totalFrames;
  //   if (frame < wordmarkStart) {
  //     wordmarkOpacity = 0;
  //   } else if (frame < wordmarkEnd) {
  //     wordmarkOpacity = (frame - wordmarkStart) / (wordmarkEnd - wordmarkStart);
  //   } else {
  //     wordmarkOpacity = 1;
  //   }
  // }

  // Suppress broken-image icon if any file fails to load
  const [logoErrored, setLogoErrored] = useState(false);
  const [imageErrored, setImageErrored] = useState<Set<number>>(new Set());

  return (
    <AbsoluteFill style={{ backgroundColor: 'black' }}>
      {/* Background image(s) with Ken Burns */}
      {visibleImages.map((imageName, idx) => {
        const isCurrent = idx === currentImageIdx;
        const isErrored = imageErrored.has(idx);
        if (isErrored && !isCurrent) return null;
        return (
          <Img
            key={imageName}
            src={staticFile(cleanStatic(imageName))}
            onError={() => {
              setImageErrored((prev) => {
                const next = new Set(prev);
                next.add(idx);
                return next;
              });
            }}
            style={{
              position: 'absolute',
              inset: 0,
              width: '100%',
              height: '100%',
              objectFit: 'cover',
              transform: `scale(${imageScale}) translateX(${imagePanX}%)`,
              transformOrigin: 'center center',
              opacity: isCurrent ? 0.55 : 0, // slightly darker so logo pops
            }}
          />
        );
      })}

      {/* Dark vignette overlay for logo contrast */}
      <AbsoluteFill
        style={{
          background:
            'radial-gradient(ellipse at center, rgba(0,0,0,0.1) 0%, rgba(0,0,0,0.55) 100%)',
        }}
      />

      {/* Logo, centered */}
      {!logoErrored && (
        <AbsoluteFill
          style={{
            justifyContent: 'center',
            alignItems: 'center',
          }}
        >
          <Img
            src={staticFile(cleanStatic(logo_filename))}
            onError={() => setLogoErrored(true)}
            style={{
              width: variant === 'short' ? 280 : 380,
              height: 'auto',
              opacity: logoOpacity,
              transform: `scale(${logoScale})`,
              filter: 'drop-shadow(0 8px 24px rgba(0,0,0,0.7))',
              pointerEvents: 'none',
            }}
          />
        </AbsoluteFill>
      )}

      {/* Wordmark: DISABLED per Isamu's request — logo already communicates the brand.
          The wordmark_text prop is kept alive for future reactivation if needed.
          To re-enable, uncomment the block below and set wordmarkOpacity. */}

      {/* Video title — DISABLED in intro (title shows in main video only).
          The intro is brand-only: logo + ukiyo-e slideshow + taiko hit.
          Uncomment below to re-enable title in intro. */}
      {/*
      {(title_ja || title_en) && variant === 'long' && (() => {
        const titleFadeStart = 0.3 * totalFrames;
        const titleFadeEnd = 0.5 * totalFrames;
        let titleOpacity: number;
        if (frame < titleFadeStart) {
          titleOpacity = 0;
        } else if (frame < titleFadeEnd) {
          titleOpacity = (frame - titleFadeStart) / (titleFadeEnd - titleFadeStart);
        } else if (frame > exitStart) {
          titleOpacity = logoOpacity;
        } else {
          titleOpacity = 1;
        }
        return (
          <AbsoluteFill
            style={{
              justifyContent: 'center',
              alignItems: 'center',
              paddingTop: '440px',
            }}
          >
            {title_ja && (
              <div
                style={{
                  fontSize: '36px',
                  fontWeight: 700,
                  color: '#FFFFFF',
                  fontFamily: 'sans-serif',
                  letterSpacing: '0.2em',
                  opacity: titleOpacity,
                  textShadow: '0 4px 16px rgba(0,0,0,0.9)',
                  textAlign: 'center',
                }}
              >
                {title_ja}
              </div>
            )}
            {title_en && (
              <div
                style={{
                  fontSize: '22px',
                  fontWeight: 500,
                  color: 'rgba(255,255,255,0.9)',
                  fontFamily: 'sans-serif',
                  letterSpacing: '0.1em',
                  opacity: titleOpacity,
                  textShadow: '0 2px 10px rgba(0,0,0,0.9)',
                  marginTop: title_ja ? '8px' : '0',
                  textAlign: 'center',
                  maxWidth: '70%',
                }}
              >
                {title_en}
              </div>
            )}
          </AbsoluteFill>
        );
      })()}
      */}

      {/* Taiko hit (long variant only) — 1 note at the entrance peak */}
      {variant === 'long' && taiko_filename && (
        <Audio
          src={staticFile(cleanStatic(taiko_filename))}
          startFrom={Math.floor(entranceFrames * 0.7)} // hit just before logo fully formed
          volume={0.7}
        />
      )}
    </AbsoluteFill>
  );
};
