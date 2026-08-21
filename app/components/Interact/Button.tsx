// 1. 在类型定义中添加 disabled 属性（可选，默认值为 false）
export function Button(
  { children, onClick, disabled = false } : 
  { 
    children?: React.ReactNode; 
    onClick?: () => void; 
    disabled?: boolean; // 新增 disabled 类型声明
  }
) {
  return (
    // 2. 将 disabled 属性传递给原生 button 标签
    <button 
      onClick={onClick} 
      disabled={disabled}  // 原生 button 支持 disabled，会自动禁用点击事件
      style={{ 
        // 3. 可选：添加禁用状态的样式（视觉反馈）
        opacity: disabled ? 0.6 : 1,
        cursor: disabled ? 'not-allowed' : 'pointer',
        
      }}
    >
      {children}
    </button>
  );
}