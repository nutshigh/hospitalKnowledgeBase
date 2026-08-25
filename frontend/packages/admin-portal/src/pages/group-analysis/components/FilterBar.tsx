import { useEffect, useState } from "react";
import { Form, Select, DatePicker, Radio, Input, Button } from "antd";
import type { GroupBy } from "../../../api/groupAnalysis";
import { listTenants, TenantItem } from "../../../api/groupAnalysis";
import dayjs from "dayjs";

const { RangePicker } = DatePicker;

const GROUP_BY_OPTIONS = [
  { value: "hospital", label: "医院" },
  { value: "batch", label: "批次" },
  { value: "age_group", label: "年龄段" },
  { value: "gender", label: "性别" },
  { value: "time_month", label: "时间(月)" },
];

const AGE_GROUP_OPTIONS = [
  "<20", "20-29", "30-39", "40-49", "50-59", "60+",
].map(v => ({ value: v, label: v }));

export interface FiltersState {
  hospital_ids?: string[];
  batch_ids?: string[];
  date_from?: string;
  date_to?: string;
  gender?: string;
  age_groups?: string[];
}

interface Props {
  value: FiltersState;
  onChange: (next: FiltersState) => void;
  onSubmit: () => void;
  groupBy: GroupBy;
  onGroupByChange: (g: GroupBy) => void;
}

export default function FilterBar({ value, onChange, onSubmit, groupBy, onGroupByChange }: Props) {
  const [tenants, setTenants] = useState<TenantItem[]>([]);
  const [tenantsLoading, setTenantsLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    setTenantsLoading(true);
    listTenants(true)
      .then(items => { if (alive) setTenants(items); })
      .catch(() => { if (alive) setTenants([]); })
      .finally(() => { if (alive) setTenantsLoading(false); });
    return () => { alive = false; };
  }, []);

  return (
    <Form layout="inline" style={{ marginBottom: 16 }}>
      <Form.Item label="分组维度">
        <Select value={groupBy} onChange={v => onGroupByChange(v as GroupBy)}
          options={GROUP_BY_OPTIONS} style={{ width: 120 }} />
      </Form.Item>
      <Form.Item label="医院">
        <Select
          mode="multiple"
          placeholder="留空=全部"
          showSearch
          optionFilterProp="label"
          loading={tenantsLoading}
          value={value.hospital_ids || []}
          onChange={v => onChange({ ...value, hospital_ids: v as string[] })}
          options={tenants.map(t => ({
            value: t.hospital_id,
            label: `${t.hospital_name} (${t.hospital_id})`,
          }))}
          style={{ minWidth: 240 }}
        />
      </Form.Item>
      <Form.Item label="批次UUID">
        <Input placeholder="逗号或回车分隔" style={{ width: 220 }}
          value={(value.batch_ids || []).join(",")}
          onChange={e => onChange({
            ...value,
            batch_ids: e.target.value.split(",").map(s => s.trim()).filter(Boolean),
          })} />
      </Form.Item>
      <Form.Item label="日期范围">
        <RangePicker
          value={value.date_from && value.date_to ? [
            dayjs(value.date_from), dayjs(value.date_to)
          ] : undefined}
          onChange={(_, ds) => onChange({
            ...value,
            date_from: ds[0] as string || undefined,
            date_to: ds[1] as string || undefined,
          })} />
      </Form.Item>
      <Form.Item label="性别">
        <Radio.Group value={value.gender || ""}
          onChange={e => onChange({ ...value, gender: e.target.value || undefined })}>
          <Radio value="">全部</Radio>
          <Radio value="男">男</Radio>
          <Radio value="女">女</Radio>
        </Radio.Group>
      </Form.Item>
      <Form.Item label="年龄段">
        <Select mode="multiple" placeholder="留空=全部"
          value={value.age_groups || []}
          onChange={v => onChange({ ...value, age_groups: v as string[] })}
          options={AGE_GROUP_OPTIONS} style={{ minWidth: 200 }} />
      </Form.Item>
      <Form.Item>
        <Button type="primary" onClick={onSubmit}>查询</Button>
      </Form.Item>
    </Form>
  );
}
