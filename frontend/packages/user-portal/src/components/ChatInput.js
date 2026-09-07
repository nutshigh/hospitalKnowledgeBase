import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from 'react';
import { Input, Button } from 'antd';
import { SendOutlined } from '@ant-design/icons';
export default function ChatInput({ onSend, disabled, placeholder }) {
    const [value, setValue] = useState('');
    const handleSend = () => {
        if (!value.trim() || disabled)
            return;
        onSend(value.trim());
        setValue('');
    };
    return (_jsxs("div", { style: { display: 'flex', gap: 8, padding: '8px 0' }, children: [_jsx(Input.TextArea, { value: value, onChange: (e) => setValue(e.target.value), onPressEnter: (e) => {
                    if (!e.shiftKey) {
                        e.preventDefault();
                        handleSend();
                    }
                }, placeholder: placeholder || '输入健康问题...', autoSize: { minRows: 1, maxRows: 4 }, disabled: disabled, style: { flex: 1, borderRadius: 8 } }), _jsx(Button, { type: "primary", icon: _jsx(SendOutlined, {}), onClick: handleSend, disabled: disabled || !value.trim(), style: { borderRadius: 8 } })] }));
}
