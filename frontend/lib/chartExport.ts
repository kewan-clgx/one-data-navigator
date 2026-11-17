/** Saves an on-screen SVG chart as a PNG, resolving theme CSS variables into inline styles first. */

const STYLE_PROPS = ["fill", "stroke", "stroke-width", "opacity", "font-size", "font-family", "font-weight"];

function inlineStyles(source: Element, target: Element) {
  const computed = getComputedStyle(source);
  const style = STYLE_PROPS.map((p) => `${p}:${computed.getPropertyValue(p)}`).join(";");
  target.setAttribute("style", style);
  for (let i = 0; i < source.children.length; i++) inlineStyles(source.children[i], target.children[i]);
}

export async function downloadSvgAsPng(svg: SVGSVGElement, filename: string, scale = 2): Promise<void> {
  const box = svg.viewBox.baseVal;
  const width = box?.width || svg.clientWidth;
  const height = box?.height || svg.clientHeight;
  const clone = svg.cloneNode(true) as SVGSVGElement;
  inlineStyles(svg, clone);
  clone.setAttribute("width", String(width));
  clone.setAttribute("height", String(height));
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");

  const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(clone)], { type: "image/svg+xml" }));
  try {
    const img = new Image();
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve();
      img.onerror = () => reject(new Error("could not render the chart"));
      img.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = width * scale;
    canvas.height = height * scale;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0, width, height);
    const png = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
    if (!png) throw new Error("could not export the chart");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(png);
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  } finally {
    URL.revokeObjectURL(url);
  }
}
