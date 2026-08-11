import React from "react";
import {
  AbsoluteFill,
  Composition,
  Img,
  OffthreadVideo,
  Sequence,
  registerRoot,
  staticFile,
  useVideoConfig,
} from "remotion";

type BrandProps = {
  variant: "FB版" | "通用版";
  sourceVideo: string;
  imgTuYi?: string;
  imgTuEr?: string;
  imgEndcard?: string;
  captions?: CaptionCue[];
  captionStyle?: CaptionStyle;
  width: number;
  height: number;
  fps: number;
  durationSeconds: number;
  contentSeconds: number;
  promoSeconds: number;
  overlayMaxWidthRatio: number;
  overlayLeftMaxWidthRatio?: number;
  overlayRightMaxWidthRatio?: number;
  overlayMarginHRatio: number;
  overlayMarginVRatio: number;
  sourceFit?: "cover" | "contain";
  endcardFit?: "cover" | "contain";
  overlayPlacement?: "video_corners" | "mobile_top_band";
  sourceAspectRatio?: number;
};

type CaptionCue = {
  startSeconds: number;
  endSeconds: number;
  text: string;
};

type CaptionStyle = {
  position?: "top" | "bottom";
  maxWidthRatio?: number;
  fontSizeRatio?: number;
  backgroundOpacity?: number;
  maxLines?: number;
  textColor?: string;
  backgroundColor?: string;
  accentColor?: string;
};

const fallbackProps: BrandProps = {
  variant: "通用版",
  sourceVideo: "",
  imgTuYi: "",
  imgTuEr: "",
  imgEndcard: "",
  width: 1280,
  height: 720,
  fps: 30,
  durationSeconds: 30,
  contentSeconds: 28.5,
  promoSeconds: 1.5,
  overlayMaxWidthRatio: 0.18,
  overlayLeftMaxWidthRatio: 0.22,
  overlayRightMaxWidthRatio: 0.36,
  overlayMarginHRatio: 0.03,
  overlayMarginVRatio: 0.05,
  sourceFit: "cover",
  endcardFit: "cover",
  overlayPlacement: "video_corners",
  sourceAspectRatio: 16 / 9,
  captions: [],
  captionStyle: {
    position: "bottom",
    maxWidthRatio: 0.82,
    fontSizeRatio: 0.044,
    backgroundOpacity: 0.74,
    maxLines: 3,
    textColor: "#ffffff",
    backgroundColor: "#050505",
    accentColor: "#f2d14b",
  },
};

const assetSrc = (value?: string) => {
  if (!value) {
    return "";
  }
  if (value.startsWith("http://") || value.startsWith("https://")) {
    return value;
  }
  return staticFile(value);
};

function JaguarTVVariant(props: BrandProps) {
  const p = {...fallbackProps, ...props};
  const {width, height, fps} = useVideoConfig();
  const contentFrames = Math.round(p.contentSeconds * fps);
  const endcardFrames = Math.max(1, Math.round(p.promoSeconds * fps));
  const isGeneric = p.variant === "通用版";

  return (
    <AbsoluteFill style={{backgroundColor: "#000"}}>
      <Sequence durationInFrames={contentFrames}>
        {p.sourceVideo ? (
          <OffthreadVideo src={assetSrc(p.sourceVideo)} style={{width, height, objectFit: p.sourceFit || "cover"}} muted={false} />
        ) : (
          <AbsoluteFill style={{width, height, backgroundColor: "#050505"}} />
        )}
        {isGeneric ? <CornerOverlays {...p} /> : null}
        <CaptionOverlays captions={p.captions || []} style={p.captionStyle || fallbackProps.captionStyle} />
      </Sequence>
      {isGeneric && p.imgEndcard ? (
        <Sequence from={contentFrames} durationInFrames={endcardFrames}>
          <AbsoluteFill style={{backgroundColor: "#000", alignItems: "center", justifyContent: "center"}}>
            <Img src={assetSrc(p.imgEndcard)} style={{width, height, objectFit: p.endcardFit || "cover"}} />
          </AbsoluteFill>
        </Sequence>
      ) : null}
    </AbsoluteFill>
  );
}

function CaptionOverlays({captions, style}: {captions: CaptionCue[]; style?: CaptionStyle}) {
  const {width, height, fps} = useVideoConfig();
  if (!captions.length) {
    return null;
  }

  const maxWidth = Math.round(width * (style?.maxWidthRatio || 0.82));
  const fontSize = Math.max(24, Math.round(height * (style?.fontSizeRatio || 0.044)));
  const lineHeight = Math.round(fontSize * 1.22);
  const maxLines = Math.max(1, Math.min(4, Math.round(style?.maxLines || 3)));
  const verticalOffset = Math.round(height * 0.07);
  const placement: React.CSSProperties = style?.position === "top"
    ? {top: verticalOffset}
    : {bottom: verticalOffset};
  const background = hexToRgba(style?.backgroundColor || "#050505", style?.backgroundOpacity ?? 0.74);

  return (
    <AbsoluteFill style={{pointerEvents: "none"}}>
      {captions.map((caption, index) => {
        const from = Math.max(0, Math.round(caption.startSeconds * fps));
        const durationInFrames = Math.max(1, Math.round((caption.endSeconds - caption.startSeconds) * fps));
        return (
          <Sequence key={`${caption.startSeconds}-${index}`} from={from} durationInFrames={durationInFrames}>
            <AbsoluteFill style={{alignItems: "center"}}>
              <div
                style={{
                  position: "absolute",
                  ...placement,
                  maxWidth,
                  borderLeft: `${Math.max(6, Math.round(fontSize * 0.22))}px solid ${style?.accentColor || "#f2d14b"}`,
                  background,
                  color: style?.textColor || "#ffffff",
                  fontFamily: "Arial, Helvetica, sans-serif",
                  fontSize,
                  fontWeight: 800,
                  lineHeight: `${lineHeight}px`,
                  padding: `${Math.round(fontSize * 0.42)}px ${Math.round(fontSize * 0.62)}px`,
                  textAlign: "center",
                  textShadow: "0 2px 6px rgba(0,0,0,0.55)",
                  overflow: "hidden",
                  display: "-webkit-box",
                  WebkitBoxOrient: "vertical",
                  WebkitLineClamp: maxLines,
                }}
              >
                {caption.text}
              </div>
            </AbsoluteFill>
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
}

function hexToRgba(hex: string, opacity: number) {
  const normalized = hex.trim().replace(/^#/, "");
  const value = normalized.length === 3
    ? normalized.split("").map((part) => part + part).join("")
    : normalized;
  if (!/^[0-9a-fA-F]{6}$/.test(value)) {
    return `rgba(5, 5, 5, ${opacity})`;
  }
  const red = parseInt(value.slice(0, 2), 16);
  const green = parseInt(value.slice(2, 4), 16);
  const blue = parseInt(value.slice(4, 6), 16);
  return `rgba(${red}, ${green}, ${blue}, ${opacity})`;
}

function CornerOverlays(p: BrandProps) {
  const {width, height} = useVideoConfig();
  const isMobileTopBand = p.overlayPlacement === "mobile_top_band";
  const sourceAspect = Math.max(0.1, p.sourceAspectRatio || width / height);
  const containedHeight = Math.min(height, Math.round(width / sourceAspect));
  const topBand = Math.max(0, Math.floor((height - containedHeight) / 2));
  const leftWidth = Math.round(width * (isMobileTopBand ? (p.overlayLeftMaxWidthRatio || 0.22) : p.overlayMaxWidthRatio));
  const rightWidth = Math.round(width * (isMobileTopBand ? (p.overlayRightMaxWidthRatio || 0.36) : p.overlayMaxWidthRatio));
  const marginX = Math.round(width * p.overlayMarginHRatio);
  const marginY = isMobileTopBand && topBand > 80
    ? Math.max(18, Math.round(topBand * 0.18))
    : Math.round(height * p.overlayMarginVRatio);
  const common: React.CSSProperties = {
    position: "absolute",
    top: marginY,
    height: "auto",
    objectFit: "contain",
  };

  return (
    <AbsoluteFill>
      {p.imgTuYi ? <Img src={assetSrc(p.imgTuYi)} style={{...common, width: leftWidth, left: marginX}} /> : null}
      {p.imgTuEr ? <Img src={assetSrc(p.imgTuEr)} style={{...common, width: rightWidth, right: marginX}} /> : null}
    </AbsoluteFill>
  );
}

function Root() {
  const duration = Math.round(fallbackProps.durationSeconds * fallbackProps.fps);
  return (
    <Composition
      id="JaguarTVVariant"
      component={JaguarTVVariant}
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
