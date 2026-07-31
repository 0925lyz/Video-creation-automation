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
        <OffthreadVideo src={assetSrc(p.sourceVideo)} style={{width, height, objectFit: p.sourceFit || "cover"}} muted={false} />
        {isGeneric ? <CornerOverlays {...p} /> : null}
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
