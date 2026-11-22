import type { BootstrapConfig } from '@/lib/bootstrap/bootstrap'

function SettingValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="system-setting-value">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

export function SystemSettingsPage({ config }: { config: BootstrapConfig }) {
  const capabilityCount = config.capabilities.length

  return (
    <section className="page-content system-settings-page">
      <div className="page-heading">
        <p className="eyebrow">SYS-SETTINGS · #812</p>
        <h1>系统设置</h1>
        <p>查看当前实例由服务端 bootstrap 明确提供的运行配置。此页面不直接修改服务端环境变量。</p>
      </div>

      <section className="system-settings-section" aria-labelledby="runtime-settings-title">
        <header>
          <div>
            <h2 id="runtime-settings-title">运行模式</h2>
            <p>认证模式由服务端启动配置决定，前端仅展示，不提供本地切换。</p>
          </div>
          <span className={config.auth.enabled ? 'settings-status enabled' : 'settings-status'}>
            {config.auth.enabled ? '已启用' : '未启用'}
          </span>
        </header>
        <div className="system-settings-grid">
          <SettingValue label="认证模式" value={config.auth.enabled ? '权限模式' : '免登录模式'} />
          <SettingValue label="平台版本" value={config.version} />
          <SettingValue label="已声明能力" value={String(capabilityCount)} />
        </div>
      </section>

      <section className="system-settings-section" aria-labelledby="capability-settings-title">
        <header>
          <div>
            <h2 id="capability-settings-title">功能能力</h2>
            <p>仅列出 bootstrap 返回的 capability；未声明的能力不会被前端自行开启。</p>
          </div>
        </header>
        {capabilityCount > 0 ? (
          <ul className="settings-capability-list">
            {config.capabilities.map((capability) => (
              <li key={capability}>
                <span>{capability}</span>
                <span className="settings-status enabled">服务端已声明</span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="settings-empty">
            <strong>服务端未声明功能能力</strong>
            <span>当前 bootstrap 的 capabilities 为空；这不代表可以在此处启用功能。</span>
          </div>
        )}
      </section>

      <section className="system-settings-section" aria-labelledby="environment-settings-title">
        <header>
          <div>
            <h2 id="environment-settings-title">环境与租户配置</h2>
            <p>敏感环境变量和默认租户标识不由当前 bootstrap 契约公开。</p>
          </div>
        </header>
        <div className="settings-readonly-note">
          <strong>只读信息未开放</strong>
          <span>后端尚未提供环境变量清单、默认租户或安全脱敏后的配置查询接口。为避免泄露密钥或展示推测值，此处不读取浏览器环境变量，也不提供虚假的开关。</span>
        </div>
      </section>
    </section>
  )
}
