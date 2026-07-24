import React from "react";
import {
  AbsoluteFill,
  Audio,
  Composition,
  Img,
  OffthreadVideo,
  Sequence,
  interpolate,
  registerRoot,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

type BrandProps = {
  sourceVideo: string;
  logoImage: string;
  bgmAudio?: string;
  width: number;
  height: number;
  fps: number;
  durationSeconds: number;
  contentSeconds: number;
  contentBgmVolume: number;
  endcardBgmVolume: number;
  title: string;
  tagline: string;
  topBadge: string;
  bottomHeadline: string;
  bottomSubline: string;
  site: string;
  endcardCta: string;
  subtitles?: Array<{start: number; end: number; text: string}>;
};

const fallbackProps: BrandProps = {
  sourceVideo: "",
  logoImage: "",
  width: 1280,
  height: 720,
  fps: 30,
  durationSeconds: 30,
  contentSeconds: 26,
  contentBgmVolume: 0.12,
  endcardBgmVolume: 0.24,
  title: "Jaguar TV",
  tagline: "O melhor app de TV ao vivo e esportes",
  topBadge: "VÍDEO DO DIA 🔥",
  bottomHeadline: "Assista esportes ao vivo",
  bottomSubline: "Canais, jogos e entretenimento em um só app",
  site: "Jarg.top",
  endcardCta: "BAIXE EM Jarg.top",
};

const brandGreen = "#0b3b20";
const neon = "#9cff18";
const yellow = "#ffd72e";

const assetSrc = (value?: string) => {
  if (!value) {
    return "";
  }
  if (value.startsWith("http://") || value.startsWith("https://")) {
    return value;
  }
  return staticFile(value);
};

function JaguarTVBrand(props: BrandProps) {
  const p = {...fallbackProps, ...props};
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const contentFrames = Math.round(p.contentSeconds * fps);
  const fadeOpacity = interpolate(frame, [contentFrames - 10, contentFrames + 8], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{backgroundColor: "#06160d", fontFamily: "Inter, Arial, sans-serif"}}>
      <Sequence durationInFrames={contentFrames}>
        <OffthreadVideo src={assetSrc(p.sourceVideo)} style={{width, height, objectFit: "cover"}} muted={false} />
        {p.bgmAudio && p.contentBgmVolume > 0 ? <Audio src={assetSrc(p.bgmAudio)} volume={p.contentBgmVolume} /> : null}
        <VideoOverlays {...p} />
      </Sequence>

      <Sequence from={contentFrames}>
        {p.bgmAudio && p.endcardBgmVolume > 0 ? (
          <Audio src={assetSrc(p.bgmAudio)} volume={p.endcardBgmVolume} startFrom={contentFrames} />
        ) : null}
        <Endcard {...p} />
      </Sequence>

      <AbsoluteFill
        style={{
          pointerEvents: "none",
          opacity: fadeOpacity,
          background: "radial-gradient(circle at 50% 20%, rgba(156,255,24,.18), transparent 34%)",
        }}
      />
    </AbsoluteFill>
  );
}

function VideoOverlays(p: BrandProps) {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const time = frame / fps;
  const activeSubtitle = (p.subtitles || []).find((item) => time >= item.start && time <= item.end);
  const logoIn = spring({frame, fps, config: {damping: 14, stiffness: 120}});
  const topY = interpolate(logoIn, [0, 1], [-70, 0]);
  const pulse = 1 + Math.sin(frame / 9) * 0.018;
  const bottomIn = spring({frame: frame - 18, fps, config: {damping: 16, stiffness: 110}});
  const bottomY = interpolate(bottomIn, [0, 1], [96, 0]);
  const flash = interpolate(frame % 90, [0, 8, 30], [0.2, 1, 0.2], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill>
      <div style={{position: "absolute", inset: 0, boxShadow: "inset 0 0 120px rgba(0,0,0,.42)"}} />
      <div
        style={{
          position: "absolute",
          left: 28,
          top: 22 + topY,
          display: "flex",
          alignItems: "center",
          gap: 14,
          padding: "10px 18px 10px 10px",
          borderRadius: 999,
          background: "rgba(4, 35, 18, .72)",
          border: "1px solid rgba(156,255,24,.38)",
          backdropFilter: "blur(8px)",
          color: "white",
          transform: `scale(${pulse})`,
        }}
      >
        {p.logoImage ? (
          <Img src={assetSrc(p.logoImage)} style={{width: 58, height: 58, borderRadius: 14, objectFit: "cover"}} />
        ) : null}
        <div>
          <div style={{fontSize: 24, fontWeight: 900, letterSpacing: -0.6}}>{p.title}</div>
          <div style={{fontSize: 14, color: neon, fontWeight: 800}}>Futebol ao vivo no Brasil</div>
        </div>
      </div>

      <div
        style={{
          position: "absolute",
          right: 28,
          top: 30,
          padding: "13px 18px",
          borderRadius: 16,
          background: `rgba(255, 215, 46, ${0.78 + flash * 0.1})`,
          color: "#082011",
          fontSize: 22,
          fontWeight: 1000,
          boxShadow: "0 12px 40px rgba(0,0,0,.28)",
        }}
      >
        {p.topBadge}
      </div>

      <div
        style={{
          position: "absolute",
          left: 36,
          right: 36,
          bottom: 28 + bottomY,
          minHeight: 72,
          padding: "16px 24px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 24,
          borderRadius: 22,
          background: "linear-gradient(90deg, rgba(4,35,18,.88), rgba(10,84,39,.84))",
          border: "1px solid rgba(156,255,24,.55)",
          boxShadow: "0 18px 60px rgba(0,0,0,.34)",
          color: "white",
        }}
      >
        <div>
          <div style={{fontSize: 30, lineHeight: 1.05, fontWeight: 1000}}>{p.bottomHeadline}</div>
          <div style={{marginTop: 5, fontSize: 17, color: "#d9ffd0", fontWeight: 700}}>{p.bottomSubline}</div>
        </div>
        <div
          style={{
            flex: "0 0 auto",
            padding: "14px 24px",
            borderRadius: 14,
            background: neon,
            color: "#062010",
            fontSize: 30,
            fontWeight: 1000,
            letterSpacing: 0.3,
          }}
        >
          {p.site}
        </div>
      </div>

      {activeSubtitle ? (
        <div
          style={{
            position: "absolute",
            left: 170,
            right: 170,
            bottom: 130,
            padding: "13px 22px",
            borderRadius: 18,
            background: "rgba(0,0,0,.62)",
            color: "white",
            textAlign: "center",
            fontSize: 31,
            lineHeight: 1.18,
            fontWeight: 1000,
            textShadow: "0 3px 0 #000, 0 0 18px rgba(0,0,0,.75)",
            border: "1px solid rgba(255,255,255,.16)",
          }}
        >
          {activeSubtitle.text}
        </div>
      ) : null}
    </AbsoluteFill>
  );
}

function Endcard(p: BrandProps) {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const pop = spring({frame, fps, config: {damping: 12, stiffness: 130}});
  const y = interpolate(pop, [0, 1], [42, 0]);
  const glow = 0.35 + Math.sin(frame / 6) * 0.12;

  return (
    <AbsoluteFill
      style={{
        background:
          "radial-gradient(circle at 50% 15%, rgba(156,255,24,.35), transparent 30%), linear-gradient(135deg, #03140a 0%, #0b3b20 48%, #03140a 100%)",
        alignItems: "center",
        justifyContent: "center",
        color: "white",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 34,
          border: "2px solid rgba(156,255,24,.28)",
          borderRadius: 36,
          boxShadow: `0 0 ${90 + glow * 80}px rgba(156,255,24,${glow})`,
        }}
      />
      {p.logoImage ? (
        <Img
          src={assetSrc(p.logoImage)}
          style={{
            width: 170,
            height: 170,
            borderRadius: 34,
            objectFit: "cover",
            transform: `translateY(${y}px) scale(${0.9 + pop * 0.1})`,
            boxShadow: "0 22px 60px rgba(0,0,0,.45)",
          }}
        />
      ) : null}
      <div style={{marginTop: 26, fontSize: 66, fontWeight: 1000, letterSpacing: -2}}>{p.title}</div>
      <div style={{marginTop: 10, fontSize: 32, color: neon, fontWeight: 900}}>{p.tagline}</div>
      <div
        style={{
          marginTop: 34,
          width: 650,
          padding: "18px 28px",
          borderRadius: 20,
          background: "rgba(255,255,255,.1)",
          border: "1px solid rgba(255,255,255,.22)",
          display: "grid",
          gridTemplateColumns: "1fr 1fr 1fr",
          gap: 14,
          textAlign: "center",
          fontSize: 22,
          fontWeight: 900,
        }}
      >
        <span style={{color: yellow}}>Anti-Freeze</span>
        <span style={{color: neon}}>4K Real</span>
        <span style={{color: "#7ab6ff"}}>Futebol Total</span>
      </div>
      <div
        style={{
          marginTop: 34,
          padding: "18px 54px",
          borderRadius: 18,
          background: neon,
          color: brandGreen,
          fontSize: 46,
          fontWeight: 1000,
          letterSpacing: 0.5,
          boxShadow: "0 18px 50px rgba(156,255,24,.25)",
        }}
      >
        {p.endcardCta}
      </div>
      <div style={{marginTop: 20, fontSize: 20, color: "#c7ffd7", fontWeight: 700}}>
        Conteúdo para fãs brasileiros 🇧🇷
      </div>
    </AbsoluteFill>
  );
}

function Root() {
  const duration = Math.round(fallbackProps.durationSeconds * fallbackProps.fps);
  return (
    <Composition
      id="JaguarTVBrand"
      component={JaguarTVBrand}
      durationInFrames={duration}
      fps={fallbackProps.fps}
      width={fallbackProps.width}
      height={fallbackProps.height}
      defaultProps={fallbackProps}
      calculateMetadata={({props}) => ({
        durationInFrames: Math.round((props.durationSeconds || fallbackProps.durationSeconds) * (props.fps || fallbackProps.fps)),
        fps: props.fps || fallbackProps.fps,
        width: props.width || fallbackProps.width,
        height: props.height || fallbackProps.height,
      })}
    />
  );
}

registerRoot(Root);
