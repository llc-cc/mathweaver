declare module "pdfjs-dist/build/pdf.mjs" {
  export const GlobalWorkerOptions: { workerSrc: string };
  export const Util: { transform: (first: number[], second: number[]) => number[] };
  export function getDocument(source: { url: string; [key: string]: unknown }): { promise: Promise<any> };
}
