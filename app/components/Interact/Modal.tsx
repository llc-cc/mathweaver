import React, { useRef, type FormEvent } from "react";

/**
 * 弹窗组件属性
 * @param children - 弹窗内容
 * @param display - 是否显示弹窗
 * @param onSubmit - 提交表单时的回调函数
 */
interface ModalProps {
    children: React.ReactNode;
    display: boolean;
    onSubmit: (data: FormData) => void;
    onClose?: () => void;
}

/**
 * 弹窗组件
 * @param param0 - 弹窗组件属性
 * @returns - 弹窗组件
 */
export function Modal({ children, display, onSubmit, onClose }: ModalProps) {
    const formRef = useRef<HTMLFormElement | null>(null);

    if (!display) return null;

    function handleClose() {
        if (onClose) onClose();
    }

    /**
     * 处理表单提交事件
     * @param e - 表单事件
     */
    function handleSubmit(e: FormEvent<HTMLFormElement>) {
        e.preventDefault();                                     // 阻止默认提交行为
        const el = formRef.current;                             // 获取表单元素
        const data = el ? new FormData(el) : new FormData();    // 创建 FormData 对象
        onSubmit(data);                                         // 调用提交回调函数
        handleClose();                                          // 关闭弹窗
    }

    return (
        <div className="modal" role="dialog" aria-modal="true">
            <form ref={formRef} onSubmit={handleSubmit}>
                {children}
                <div className="row-box">
                    <button type="submit">确认</button>
                    <button type="button" onClick={handleClose}>取消</button>
                </div>
            </form>
        </div>
    );
}