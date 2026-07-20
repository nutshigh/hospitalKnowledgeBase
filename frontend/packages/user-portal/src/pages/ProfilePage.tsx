import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Spin, Input } from 'antd';
import { UserOutlined, LogoutOutlined, SettingOutlined } from '@ant-design/icons';
import { useUserStore } from '../stores/userStore';
import Layout from '../components/Layout';
import ColorBadge from '../components/ColorBadge';
import IndicatorTrendChart from '../components/IndicatorTrendChart';

interface UserSummary {
  total_reports: number;
  earliest_date: string | null;
  latest_date: string | null;
  latest_overall_level: string | null;
  latest_red: number; latest_yellow: number; latest_green: number;
  baseline_date: string | null;
}

interface TrendPoint {
  report_id: number; report_date: string; value: number; color?: string;
}

interface IndicatorTrend {
  item_name_standard: string | null;
  item_name: string;
  unit: string | null;
  points: TrendPoint[];
  latest_deviation: string | null;
  trend_direction: string | null;
}

interface AbnormalDist {
  item_name_standard: string;
  red_count: number;
  yellow_count: number;
  last_color: string;
}

interface OverviewResponse {
  user_summary: UserSummary | null;
  indicator_trends: IndicatorTrend[];
  abnormal_distribution: AbnormalDist[];
}

export default function ProfilePage() {
  const { api, logout } = useUserStore();
  const nav = useNavigate();
  const [data, setData] = useState<OverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  useEffect(() => {
    api.get('/profile/overview').then(r => setData(r.data)).catch(() => setData(null)).finally(() => setLoading(false));
  }, []);

  if (loading) return (
    <Layout title="我的健康档案">
      <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
    </Layout>
  );

  if (!data || !data.user_summary || data.user_summary.total_reports === 0) {
    return (
      <Layout title="我的健康档案">
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--color-text-secondary)' }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>📋</div>
          <div style={{ fontSize: 15, fontWeight: 500, marginBottom: 8 }}>暂无档案数据</div>
          <div style={{ fontSize: 13 }}>上传您的体检报告后,可在此查看健康变化趋势</div>
        </div>
        <BottomSettings onLogout={() => { logout(); nav('/login'); }} />
      </Layout>
    );
  }

  const s = data.user_summary;
  const filtered = data.indicator_trends.filter(t => {
    if (!search) return true;
    return (t.item_name_standard || t.item_name || '').toLowerCase().includes(search.toLowerCase());
  });
  const topTrends = filtered.slice(0, 10);

  return (
    <Layout title="我的健康档案">
      <div style={{
        background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
        padding: 20, boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)', marginBottom: 16,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <div style={{ width: 48, height: 48, borderRadius: '50%', background: 'var(--color-primary-light)',
            display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <UserOutlined style={{ fontSize: 20, color: 'var(--color-primary)' }} />
          </div>
          <div>
            <div style={{ fontWeight: 600, fontSize: 15 }}>体检用户</div>
            <div style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
              共 {s.total_reports} 份报告 · {s.earliest_date || '未知'} 至 {s.latest_date || '未知'}
            </div>
          </div>
          {s.latest_overall_level && (
            <div style={{ marginLeft: 'auto' }}>
              <ColorBadge level={s.latest_overall_level} />
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8, fontSize: 12 }}>
          <span style={{ color: 'var(--color-red)', fontWeight: 600 }}>红区 {s.latest_red}</span>
          <span style={{ color: 'var(--color-border)' }}>|</span>
          <span style={{ color: 'var(--color-yellow)', fontWeight: 600 }}>黄区 {s.latest_yellow}</span>
          <span style={{ color: 'var(--color-border)' }}>|</span>
          <span style={{ color: 'var(--color-green)', fontWeight: 600 }}>绿区 {s.latest_green}</span>
        </div>
      </div>

      {data.abnormal_distribution.length > 0 && (
        <div style={{
          background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
          padding: '16px 20px', boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)', marginBottom: 16,
        }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>异常指标分布</div>
          {data.abnormal_distribution.map((a, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '8px 0', borderBottom: i < data.abnormal_distribution.length - 1 ? '1px solid var(--color-border-light)' : 'none',
              fontSize: 13,
            }}>
              <span>{a.item_name_standard}</span>
              <span style={{ color: 'var(--color-text-secondary)', fontSize: 12 }}>
                红 {a.red_count} · 黄 {a.yellow_count}
                {' '}<ColorBadge level={a.last_color} size="sm" />
              </span>
            </div>
          ))}
        </div>
      )}

      <div style={{
        background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
        padding: '16px 20px', boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)',
      }}>
        <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>指标走势</div>
        <Input.Search
          placeholder="搜索指标名"
          size="small" value={search} onChange={e => setSearch(e.target.value)}
          style={{ marginBottom: 12 }}
        />
        {topTrends.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 24, color: 'var(--color-text-secondary)', fontSize: 13 }}>
            暂无可视化指标
          </div>
        ) : (
          topTrends.map((t, i) => {
            const last = t.points[t.points.length - 1];
            return (
              <div key={i} style={{
                padding: '10px 0', borderBottom: i !== topTrends.length - 1 ? '1px solid var(--color-border-light)' : 'none',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>
                    {t.item_name_standard || t.item_name}
                    {t.trend_direction && (
                      <span style={{
                        marginLeft: 6, fontSize: 11,
                        color: t.trend_direction === 'up' ? 'var(--color-red)' : 'var(--color-green)',
                      }}>
                        {t.trend_direction === 'up' ? '↑' : '↓'}
                      </span>
                    )}
                  </span>
                  <span style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
                    {last ? `${last.value}${t.unit ? ' ' + t.unit : ''}` : '-'}
                    {last?.color && <ColorBadge level={last.color} size="sm" />}
                  </span>
                </div>
                <IndicatorTrendChart data={t.points} />
              </div>
            );
          })
        )}
      </div>

      <BottomSettings onLogout={() => { logout(); nav('/login'); }} />
    </Layout>
  );
}

function BottomSettings({ onLogout }: { onLogout: () => void }) {
  return (
    <div style={{
      marginTop: 24, background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
      overflow: 'hidden', boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)',
    }}>
      <div
        onClick={() => {}}
        style={{
          display: 'flex', alignItems: 'center', gap: 12, padding: '14px 20px',
          cursor: 'pointer', borderBottom: '1px solid var(--color-border-light)',
        }}
      >
        <SettingOutlined />
        <span style={{ fontSize: 14 }}>设置</span>
        <span style={{ marginLeft: 'auto', color: 'var(--color-text-secondary)' }}>›</span>
      </div>
      <div
        onClick={onLogout}
        style={{
          display: 'flex', alignItems: 'center', gap: 12, padding: '14px 20px',
          cursor: 'pointer', color: 'var(--color-red)',
        }}
      >
        <LogoutOutlined />
        <span style={{ fontSize: 14 }}>退出登录</span>
      </div>
    </div>
  );
}