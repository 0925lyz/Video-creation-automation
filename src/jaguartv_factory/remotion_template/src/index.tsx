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
  imgLogo?: string;
  imgTuYi?: string;
  imgTuEr?: string;
  imgBottomBanner?: string;
  imgEndcard?: string;
  topBadge?: string;
  bottomHeadline?: string;
  bottomSubline?: string;
  endcardCta?: string;
  customDesign?: boolean;
  designLayers?: DesignLayer[];
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
  bottomBannerAspectRatio?: number;
};

type DesignLayer = {
  id: string;
  type: "text" | "image";
  x: number;
  y: number;
  text?: string;
  color?: string;
  font_size_ratio?: number;
  max_width?: number;
  font_weight?: number;
  src?: string;
  width?: number;
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
  safeInsetRatio?: number;
  backgroundOpacity?: number;
  maxLines?: number;
  textColor?: string;
  backgroundColor?: string;
  accentColor?: string;
};

const fallbackProps: BrandProps = {
  variant: "通用版",
  sourceVideo: "",
  imgLogo: "",
  imgTuYi: "",
  imgTuEr: "",
  imgBottomBanner: "",
  imgEndcard: "",
  topBadge: "",
  bottomHeadline: "",
  bottomSubline: "",
  endcardCta: "",
  customDesign: false,
  designLayers: [],
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
  bottomBannerAspectRatio: 992 / 136,
  captions: [],
  captionStyle: {
    position: "bottom",
    maxWidthRatio: 0.90,
    fontSizeRatio: 0.028,
    safeInsetRatio: 0.05,
    backgroundOpacity: 0,
    maxLines: 2,
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
        {isGeneric ? (
          <GenericContentLayout {...p} />
        ) : p.sourceVideo ? (
          <OffthreadVideo src={assetSrc(p.sourceVideo)} style={{width, height, objectFit: p.sourceFit || "cover"}} muted={false} />
        ) : <AbsoluteFill style={{width, height, backgroundColor: "#050505"}} />}
        {p.customDesign ? <FreeformDesignOverlay layers={p.designLayers || []} /> : null}
        <CaptionOverlays
          captions={p.captions || []}
          style={p.captionStyle || fallbackProps.captionStyle}
          sourceFit={p.sourceFit || "cover"}
          sourceAspectRatio={p.sourceAspectRatio || width / height}
          frameRect={isGeneric ? genericContentRects(p, width, height).video : undefined}
        />
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

type ContentRect = {x: number; y: number; width: number; height: number};

function genericContentRects(p: BrandProps, width: number, height: number) {
  const sourceAspect = Math.max(0.1, p.sourceAspectRatio || width / height);
  const bannerAspect = Math.max(1, p.bottomBannerAspectRatio || 992 / 136);
  const videoWidth = Math.min(width, height / (1 / sourceAspect + 1 / bannerAspect));
  const videoHeight = videoWidth / sourceAspect;
  const genericBannerHeight = videoWidth / bannerAspect;
  const groupHeight = videoHeight + genericBannerHeight;
  const x = (width - videoWidth) / 2;
  const y = (height - groupHeight) / 2;
  return {
    video: {x, y, width: videoWidth, height: videoHeight},
    banner: {x, y: y + videoHeight, width: videoWidth, height: genericBannerHeight},
  };
}

function GenericContentLayout(p: BrandProps) {
  const {width, height} = useVideoConfig();
  const rects = genericContentRects(p, width, height);
  return (
    <AbsoluteFill style={{backgroundColor: "#000"}}>
      {p.sourceVideo ? <OffthreadVideo src={assetSrc(p.sourceVideo)} style={{
        position: "absolute",
        left: rects.video.x,
        top: rects.video.y,
        width: rects.video.width,
        height: rects.video.height,
        objectFit: "contain",
      }} muted={false} /> : null}
      {p.imgBottomBanner ? <Img src={assetSrc(p.imgBottomBanner)} style={{
        position: "absolute",
        left: rects.banner.x,
        top: rects.banner.y,
        width: rects.banner.width,
        height: rects.banner.height,
        objectFit: "contain",
      }} /> : null}
    </AbsoluteFill>
  );
}

function FreeformDesignOverlay({layers}: {layers: DesignLayer[]}) {
  const {width, height} = useVideoConfig();
  return (
    <AbsoluteFill style={{pointerEvents: "none", fontFamily: "Arial, Helvetica, sans-serif"}}>
      {layers.map((layer) => {
        const common: React.CSSProperties = {
          position: "absolute",
          left: Math.round(width * Math.max(0, Math.min(1, Number(layer.x) || 0))),
          top: Math.round(height * Math.max(0, Math.min(1, Number(layer.y) || 0))),
        };
        if (layer.type === "image" && layer.src) {
          return (
            <Img
              key={layer.id}
              src={assetSrc(layer.src)}
              style={{
                ...common,
                width: Math.round(width * Math.max(0.03, Math.min(1, Number(layer.width) || 0.2))),
                height: "auto",
                objectFit: "contain",
              }}
            />
          );
        }
        if (layer.type === "text" && layer.text) {
          return (
            <div key={layer.id} style={{
              ...common,
              maxWidth: Math.round(width * Math.max(0.1, Math.min(1, Number(layer.max_width) || 0.9))),
              color: layer.color || "#ffffff",
              fontSize: Math.max(8, Math.round(height * Math.max(0.01, Math.min(0.25, Number(layer.font_size_ratio) || 0.05)))),
              fontWeight: Math.max(100, Math.min(900, Number(layer.font_weight) || 800)),
              lineHeight: 1.15,
              whiteSpace: "pre-wrap",
              overflowWrap: "normal",
              textShadow: "0 2px 7px rgba(0,0,0,.72)",
            }}>{layer.text}</div>
          );
        }
        return null;
      })}
    </AbsoluteFill>
  );
}

function sourceVideoRect(width: number, height: number, sourceAspect: number, fit: "cover" | "contain") {
  const canvasAspect = width / Math.max(1, height);
  if (fit === "contain") {
    if (sourceAspect >= canvasAspect) {
      const displayWidth = width;
      const displayHeight = width / sourceAspect;
      return {x: 0, y: (height - displayHeight) / 2, width: displayWidth, height: displayHeight};
    }
    const displayHeight = height;
    const displayWidth = height * sourceAspect;
    return {x: (width - displayWidth) / 2, y: 0, width: displayWidth, height: displayHeight};
  }
  if (sourceAspect >= canvasAspect) {
    const displayHeight = height;
    const displayWidth = height * sourceAspect;
    return {x: (width - displayWidth) / 2, y: 0, width: displayWidth, height: displayHeight};
  }
  const displayWidth = width;
  const displayHeight = width / sourceAspect;
  return {x: 0, y: (height - displayHeight) / 2, width: displayWidth, height: displayHeight};
}

function CaptionOverlays({
  captions,
  style,
  sourceFit,
  sourceAspectRatio,
  frameRect,
}: {
  captions: CaptionCue[];
  style?: CaptionStyle;
  sourceFit: "cover" | "contain";
  sourceAspectRatio: number;
  frameRect?: ContentRect;
}) {
  const {width, height, fps} = useVideoConfig();
  if (!captions.length) {
    return null;
  }

  const maxWidth = Math.round(width * (style?.maxWidthRatio || 0.90));
  const fontSize = Math.max(20, Math.round(height * (style?.fontSizeRatio || 0.028)));
  const lineHeight = Math.round(fontSize * 1.16);
  const maxLines = Math.max(1, Math.min(2, Math.round(style?.maxLines || 2)));
  const background = hexToRgba(style?.backgroundColor || "#050505", style?.backgroundOpacity ?? 0);
  const rect = frameRect || sourceVideoRect(width, height, sourceAspectRatio || width / height, sourceFit || "cover");
  const safeInset = Math.max(Math.round(height * 0.01), Math.round(rect.height * (style?.safeInsetRatio || 0.05)));
  const placement: React.CSSProperties = style?.position === "top" ? {
    top: rect.y + safeInset,
  } : {
    bottom: height - (rect.y + rect.height) + safeInset,
  };

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
                  maxWidth: Math.min(maxWidth, rect.width * 0.90),
                  background,
                  color: style?.textColor || "#ffffff",
                  fontFamily: "Arial, Helvetica, sans-serif",
                  fontSize,
                  fontWeight: 700,
                  lineHeight: `${lineHeight}px`,
                  padding: 0,
                  borderRadius: Math.max(4, Math.round(fontSize * 0.12)),
                  boxSizing: "border-box",
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
