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
  overlayMarginHRatio: number;
  overlayMarginVRatio: number;
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
  overlayMarginHRatio: 0.03,
  overlayMarginVRatio: 0.05,
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
        <OffthreadVideo src={assetSrc(p.sourceVideo)} style={{width, height, objectFit: "cover"}} muted={false} />
        {isGeneric ? <CornerOverlays {...p} /> : null}
      </Sequence>
      {isGeneric && p.imgEndcard ? (
        <Sequence from={contentFrames} durationInFrames={endcardFrames}>
          <Img src={assetSrc(p.imgEndcard)} style={{width, height, objectFit: "cover"}} />
        </Sequence>
      ) : null}
    </AbsoluteFill>
  );
}

function CornerOverlays(p: BrandProps) {
  const {width, height} = useVideoConfig();
  const maxWidth = Math.round(width * p.overlayMaxWidthRatio);
  const marginX = Math.round(width * p.overlayMarginHRatio);
  const marginY = Math.round(height * p.overlayMarginVRatio);
  const common: React.CSSProperties = {
    position: "absolute",
    top: marginY,
    width: maxWidth,
    height: "auto",
    objectFit: "contain",
  };

  return (
    <AbsoluteFill>
      {p.imgTuYi ? <Img src={assetSrc(p.imgTuYi)} style={{...common, left: marginX}} /> : null}
      {p.imgTuEr ? <Img src={assetSrc(p.imgTuEr)} style={{...common, right: marginX}} /> : null}
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
