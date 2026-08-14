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
        {p.customDesign ? <FreeformDesignOverlay layers={p.designLayers || []} /> : null}
        {isGeneric && !p.customDesign ? <CornerOverlays {...p} /> : null}
        {isGeneric && !p.customDesign ? <DesignCopyOverlay {...p} /> : null}
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

function DesignCopyOverlay(p: BrandProps) {
  const {width, height} = useVideoConfig();
  const isMobileTopBand = p.overlayPlacement === "mobile_top_band";
  const sourceAspect = Math.max(0.1, p.sourceAspectRatio || width / height);
  const containedHeight = Math.min(height, Math.round(width / sourceAspect));
  const topBand = Math.max(0, Math.floor((height - containedHeight) / 2));
  const lowerBand = Math.max(0, height - containedHeight - topBand);
  const topY = isMobileTopBand && topBand > 96 ? Math.round(topBand * 0.2) : Math.round(height * 0.035);
  const bottomY = isMobileTopBand && lowerBand > 96 ? Math.round(lowerBand * 0.2) : Math.round(height * 0.035);
  const headline = String(p.bottomHeadline || "").trim();
  const subline = String(p.bottomSubline || "").trim();
  const cta = String(p.endcardCta || "").trim();
  return (
    <AbsoluteFill style={{pointerEvents: "none", fontFamily: "Arial, Helvetica, sans-serif"}}>
      <div style={{
        position: "absolute",
        left: Math.round(width * 0.035),
        top: topY,
        display: "flex",
        alignItems: "center",
        gap: Math.round(width * 0.016),
        maxWidth: Math.round(width * 0.54),
      }}>
        {p.imgLogo ? (
          <Img src={assetSrc(p.imgLogo)} style={{
            width: Math.round(width * 0.12),
            maxHeight: Math.round(height * 0.07),
            objectFit: "contain",
            filter: "drop-shadow(0 3px 10px rgba(0,0,0,.55))",
          }} />
        ) : null}
        {p.topBadge ? (
          <div style={{
            padding: `${Math.round(height * 0.006)}px ${Math.round(width * 0.018)}px`,
            borderRadius: Math.round(width * 0.012),
            background: "rgba(5, 5, 5, .72)",
            color: "#fff",
            fontSize: Math.round(height * 0.024),
            fontWeight: 900,
            lineHeight: 1,
            textShadow: "0 2px 6px rgba(0,0,0,.65)",
          }}>{p.topBadge}</div>
        ) : null}
      </div>
      {(headline || subline || cta) ? (
        <div style={{
          position: "absolute",
          left: Math.round(width * 0.05),
          right: Math.round(width * 0.05),
          bottom: bottomY,
          padding: `${Math.round(height * 0.014)}px ${Math.round(width * 0.028)}px`,
          borderRadius: Math.round(width * 0.018),
          background: "linear-gradient(90deg, rgba(3, 84, 62, .88), rgba(5, 5, 5, .7))",
          color: "#fff",
          boxShadow: "0 10px 30px rgba(0,0,0,.32)",
          textShadow: "0 2px 6px rgba(0,0,0,.55)",
        }}>
          {headline ? <div style={{fontSize: Math.round(height * 0.033), fontWeight: 900, lineHeight: 1.05}}>{headline}</div> : null}
          {subline ? <div style={{marginTop: Math.round(height * 0.006), fontSize: Math.round(height * 0.021), fontWeight: 700, opacity: .92}}>{subline}</div> : null}
          {cta ? <div style={{marginTop: Math.round(height * 0.008), color: "#f2d14b", fontSize: Math.round(height * 0.022), fontWeight: 900}}>{cta}</div> : null}
        </div>
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
