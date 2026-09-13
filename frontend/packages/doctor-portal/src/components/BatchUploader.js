import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from 'react';
import { Upload, Button, Progress, message } from 'antd';
import { InboxOutlined } from '@ant-design/icons';
const CHUNK_SIZE = 5 * 1024 * 1024;
export default function BatchUploader({ api, onCreated }) {
    const [file, setFile] = useState(null);
    const [uploading, setUploading] = useState(false);
    const [uploaded, setUploaded] = useState(0);
    const start = async () => {
        if (!file || uploading)
            return;
        setUploading(true);
        setUploaded(0);
        const total = Math.max(1, Math.ceil(file.size / CHUNK_SIZE));
        try {
            const createForm = new FormData();
            createForm.append('filename', file.name);
            const { data: cd } = await api.post('/reports/batches', createForm);
            const bid = cd.batch_id;
            for (let i = 0; i < total; i++) {
                const blob = file.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
                const form = new FormData();
                form.append('index', String(i));
                form.append('total', String(total));
                form.append('data', blob, `${file.name}.part${i}`);
                await api.post(`/reports/batches/${bid}/chunk`, form, {
                    headers: { 'Content-Type': 'multipart/form-data' },
                    onUploadProgress: (e) => setUploaded(Math.min(file.size, i * CHUNK_SIZE + (e.loaded || 0))),
                });
            }
            await api.post(`/reports/batches/${bid}/complete`, {
                expected_total: total, expected_size: file.size,
            });
            setFile(null);
            setUploaded(0);
            onCreated(bid);
        }
        catch (err) {
            const code = err?.response?.data?.detail;
            message.error(code ? `上传失败: ${code}` : '上传失败,请重试');
        }
        finally {
            setUploading(false);
        }
    };
    const pct = file && file.size ? Math.round((uploaded / file.size) * 100) : 0;
    return (_jsxs("div", { children: [_jsxs("div", { style: {
                    border: '1px solid var(--color-border)', borderRadius: 8,
                    padding: '12px 16px', marginBottom: 16, background: 'var(--color-surface)',
                    fontSize: 13, color: 'var(--color-text-secondary)',
                }, children: [_jsx("div", { style: { fontWeight: 600, color: 'var(--color-text)', marginBottom: 6 }, children: "\u6587\u4EF6\u547D\u540D\u8981\u6C42(\u5FC5\u987B\u4E25\u683C\u9075\u5FAA)" }), _jsxs("div", { children: ["\u6BCF\u4EFD\u6587\u4EF6\u540D\u5FC5\u987B\u5F62\u5982:", _jsx("code", { children: "\u5F20\u4E09_011234.pdf" }), " \u5373 ", _jsx("code", { children: "<\u59D3\u540D>_<\u8EAB\u4EFD\u8BC1\u540E\u516D\u4F4D>.ext" })] }), _jsxs("ul", { style: { margin: '6px 0 0 20px', padding: 0 }, children: [_jsxs("li", { children: ["\u59D3\u540D + \u8EAB\u4EFD\u8BC1\u540E\u516D\u4F4D(", _jsx("code", { children: "5 \u4F4D\u6570\u5B57 + \u672B\u4F4D 0-9/X" }), ")\u4EE5\u534A\u89D2\u4E0B\u5212\u7EBF ", _jsx("code", { children: "_" }), " \u5206\u9694;\u59D3\u540D\u4E0D\u80FD\u542B\u4E0B\u5212\u7EBF"] }), _jsxs("li", { children: ["\u5206\u53D1\u65F6\u6309 ", _jsx("code", { children: "\u59D3\u540D + \u540E\u516D\u4F4D" }), " \u5230\u5916\u90E8 HIS \u7CBE\u786E\u5339\u914D\u5B9A\u4F4D\u6240\u5C5E\u533B\u9662,\u5339\u914D\u4E0D\u5230\u5C06\u88AB\u6807\u8BB0\u4E3A \u5931\u8D25\u7C7B\u578B ", _jsx("code", { children: "hospital_not_found" }), ",\u4E0D\u89E3\u6790\u3001\u4E0D\u53EF\u91CD\u8BD5"] }), _jsxs("li", { children: ["\u547D\u540D\u4E0D\u5408\u89C4\u7684\u6587\u4EF6\u5C06\u88AB\u6807\u8BB0\u4E3A ", _jsx("code", { children: "dispatch_unmatched" }), ",\u4E0D\u89E3\u6790\u3001\u4E0D\u53EF\u91CD\u8BD5"] }), _jsx("li", { children: "\u6269\u5C55\u540D\u4EC5\u652F\u6301 pdf / doc / jpg / jpeg / png(\u4E0D\u542B docx)" }), _jsx("li", { children: "\u5355\u6587\u4EF6 \u2264 50MB,\u6574\u5305 \u2264 10GB" })] })] }), _jsxs(Upload.Dragger, { beforeUpload: (f) => { setFile(f); return false; }, showUploadList: false, accept: ".zip,.tar,.gz,.tgz", disabled: uploading, style: { background: 'var(--color-surface)', border: file ? '2px solid var(--color-primary)' : undefined }, children: [_jsx(InboxOutlined, { style: { fontSize: 48, color: 'var(--color-text-secondary)', marginBottom: 16 } }), _jsx("p", { style: { fontWeight: 600 }, children: file ? file.name : '点击或拖拽上传 zip/tar 包' }), _jsx("p", { style: { fontSize: 12, color: 'var(--color-text-secondary)' }, children: "\u5305\u5185\u6587\u4EF6\u540D\u987B\u7B26\u5408\u4E0A\u8FF0\u7EA6\u5B9A" })] }), file && !uploading && (_jsx(Button, { type: "primary", block: true, size: "large", onClick: start, style: { height: 48, marginTop: 16, background: 'var(--color-primary)', border: 'none' }, children: "\u5F00\u59CB\u4E0A\u4F20" })), uploading && (_jsxs("div", { style: { marginTop: 16 }, children: [_jsx(Progress, { percent: pct, status: "active" }), _jsxs("p", { style: { textAlign: 'center', color: 'var(--color-text-secondary)', marginTop: 8 }, children: ["\u5206\u7247\u4E0A\u4F20\u4E2D ", pct, "%"] })] }))] }));
}
