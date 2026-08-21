// app/react-katex.d.ts
declare module '@matejmazur/react-katex' {
  import React from 'react';
  
  export interface KatexProps {
    children?: string;
    math?: string;
    block?: boolean;
    errorColor?: string;
    renderError?: (error: Error | TypeError) => React.ReactNode;
    settings?: any;
  }
  
  export class InlineMath extends React.Component<KatexProps> {}
  export class BlockMath extends React.Component<KatexProps> {}
  
  // ✅ 添加默认导出
  const Katex: {
    InlineMath: typeof InlineMath;
    BlockMath: typeof BlockMath;
  };
  export default Katex;
}